#!/usr/bin/env python3
"""
Full Decode Pipeline — Repo 1 → Claude → Repo 2 → Repo 3 → Claude → Combined PPTX

Usage:
  python pipeline.py --url https://www.descope.com --slug descope
  python pipeline.py --url https://www.cyera.com --slug cyera --output ~/Downloads

Requirements:
  All three repos must exist at their default paths (or override with --repo1/2/3):
    ~/website-decoder
    ~/pptx-builder
    ~/social-decoder

  SCRAPINGDOG_API_KEY  — required for LinkedIn data (repo 3)
  ANTHROPIC_API_KEY    — optional, enables Claude decode in repo 1 + repo 3
                         If not set, Claude Code generates decode in-session
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ─── Default repo paths ───────────────────────────────────────────────────────
HOME = Path.home()
DEFAULT_REPO1 = HOME / "website-decoder"
DEFAULT_REPO2 = HOME / "pptx-builder"
DEFAULT_REPO3 = HOME / "social-decoder"


def run(cmd, cwd=None, env=None):
    """Run a shell command, stream output, raise on failure."""
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        env={**os.environ, **(env or {})},
    )
    if result.returncode != 0:
        print(f"\n[ERROR] Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)


def python(repo_path):
    """Return path to venv python for a repo."""
    venv_py = repo_path / "venv" / "bin" / "python"
    if venv_py.exists():
        return venv_py
    return Path(sys.executable)


def latest_run_id(screenshots_dir, domain):
    """Find the most recent run_id that actually contains screenshots."""
    domain_dir = screenshots_dir / domain
    if not domain_dir.exists():
        return None
    # Only consider dirs that have at least one PNG screenshot
    valid_runs = sorted([
        d for d in domain_dir.iterdir()
        if d.is_dir() and list(d.glob("*.png"))
    ])
    return valid_runs[-1].name if valid_runs else None


def find_latest_social_run(repo3, slug):
    """Find the most recent social decode run for a slug."""
    base = repo3 / "output" / slug
    if not base.exists():
        return None
    runs = sorted(base.iterdir())
    return runs[-1] if runs else None


def update_ppt_meta(repo1, domain, run_id):
    """Update ppt_content.json meta to point to the new run_id."""
    ppt_path = repo1 / "output" / "decodes" / domain / "ppt_content.json"
    if not ppt_path.exists():
        return False
    data = json.loads(ppt_path.read_text())
    data["meta"]["run_id"] = run_id
    data["meta"]["screenshot_path"] = f"output/screenshots/{domain}/{run_id}"
    ppt_path.write_text(json.dumps(data, indent=2))
    print(f"  Updated ppt_content.json meta → run_id={run_id}")
    return True


def banner(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


# ─── Main pipeline ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Full decode pipeline: website + social → combined PPTX")
    parser.add_argument("--url",    required=True,  help="Website URL, e.g. https://www.descope.com")
    parser.add_argument("--slug",   required=True,  help="LinkedIn company slug, e.g. descope")
    parser.add_argument("--output", default=str(HOME / "Downloads"),
                        help="Output directory for final PPTX (default: ~/Downloads)")
    parser.add_argument("--repo1",  default=str(DEFAULT_REPO1))
    parser.add_argument("--repo2",  default=str(DEFAULT_REPO2))
    parser.add_argument("--repo3",  default=str(DEFAULT_REPO3))
    parser.add_argument("--skip-website",  action="store_true", help="Skip repo 1 (reuse last run)")
    parser.add_argument("--skip-social",   action="store_true", help="Skip repo 3")
    parser.add_argument("--skip-decode",   action="store_true", help="Skip ppt_content generation check")
    parser.add_argument("--force-decode",  action="store_true", help="Force regenerate ppt_content.json even if it exists")
    args = parser.parse_args()

    repo1 = Path(args.repo1)
    repo2 = Path(args.repo2)
    repo3 = Path(args.repo3)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    scrapingdog_key = os.environ.get("SCRAPINGDOG_API_KEY", "")
    anthropic_key   = os.environ.get("ANTHROPIC_API_KEY", "")

    # Derive domain from URL
    from urllib.parse import urlparse
    domain = urlparse(args.url).netloc.replace("www.", "")
    slug   = args.slug.strip().lower()

    print(f"\n{'='*60}")
    print(f"  DECODE PIPELINE")
    print(f"  Website : {args.url}  ({domain})")
    print(f"  LinkedIn: linkedin.com/company/{slug}")
    print(f"  Output  : {output_dir}")
    print(f"  Claude  : {'API key set ✓' if anthropic_key else 'no key — in-session decode'}")
    print(f"{'='*60}")

    # ── STEP 1: Website decoder (Repo 1) ────────────────────────────────────
    if not args.skip_website:
        banner("STEP 1 / 5 — Website Decoder (Repo 1)")
        run([python(repo1), "analyze.py", args.url], cwd=repo1,
            env={"ANTHROPIC_API_KEY": anthropic_key} if anthropic_key else {})
    else:
        banner("STEP 1 / 5 — Website Decoder (SKIPPED)")

    # Find the run_id that was just created
    screenshots_dir = repo1 / "output" / "screenshots"
    run_id = latest_run_id(screenshots_dir, domain)
    if not run_id:
        print(f"ERROR: No screenshots found for {domain}")
        sys.exit(1)
    print(f"\n  Run ID: {run_id}")

    # ── STEP 2: Check / generate ppt_content.json ───────────────────────────
    banner("STEP 2 / 5 — PPT Content (Claude decode)")
    ppt_path = repo1 / "output" / "decodes" / domain / "ppt_content.json"

    report_path = repo1 / "output" / "reports" / f"report-{domain.replace('.', '-')}.md"
    needs_generate = not ppt_path.exists() or args.force_decode

    # Auto-regenerate if we just ran a fresh website crawl (not skipped)
    if not args.skip_website and not args.skip_decode:
        needs_generate = True

    if needs_generate:
        if anthropic_key and report_path.exists():
            print("  Generating fresh ppt_content.json via Claude API ...")
            script = (
                "import sys; sys.path.insert(0, '.'); "
                "from tools import ppt_content; from pathlib import Path; "
                f"ppt_content.generate("
                f"  report_path='{report_path.resolve()}',"
                f"  domain='{domain}',"
                f"  run_id='{run_id}',"
                f"  decodes_dir=Path('output/decodes/{domain}'),"
                f"  output_dir=Path('output/decodes/{domain}')"
                f")"
            )
            run([python(repo1), "-c", script],
                cwd=repo1, env={"ANTHROPIC_API_KEY": anthropic_key})
        elif not report_path.exists():
            print(f"  [!] Report not found: {report_path}")
            print("  Run without --skip-website to generate a fresh report first.")
            sys.exit(1)
        else:
            print("  [!] No ANTHROPIC_API_KEY — cannot generate ppt_content.json automatically.")
            print("  Run inside a Claude Code session or set ANTHROPIC_API_KEY.")
            sys.exit(1)
    else:
        update_ppt_meta(repo1, domain, run_id)
        print("  ppt_content.json found ✓ (reusing existing — use --force-decode to regenerate)")

    # ── STEP 3: Build PPTX (Repo 2) ─────────────────────────────────────────
    banner("STEP 3 / 5 — PPTX Builder (Repo 2) — website slides")
    # Social path will be added in step 5 — build website-only first to catch errors early
    run([python(repo2), "build_pptx.py",
         "--domain",    domain,
         "--run-id",    run_id,
         "--local-path", str(repo1.resolve())],
        cwd=repo2)

    # ── STEP 4: Social decoder (Repo 3) ─────────────────────────────────────
    social_run_dir = None
    if not args.skip_social:
        banner("STEP 4 / 5 — Social Decoder (Repo 3)")
        if not scrapingdog_key:
            print("  [!] SCRAPINGDOG_API_KEY not set — skipping social decode.")
        else:
            skip_api_decode = "" if anthropic_key else "--skip-decode"
            cmd = [python(repo3), "analyze.py", slug, "--max-posts", "10"]
            if skip_api_decode:
                cmd.append(skip_api_decode)
            run(cmd, cwd=repo3,
                env={
                    "SCRAPINGDOG_API_KEY": scrapingdog_key,
                    "ANTHROPIC_API_KEY":   anthropic_key,
                })
            social_run_dir = find_latest_social_run(repo3, slug)
            if social_run_dir:
                print(f"  Social run dir: {social_run_dir}")
    else:
        banner("STEP 4 / 5 — Social Decoder (SKIPPED)")
        social_run_dir = find_latest_social_run(repo3, slug)

    # ── STEP 5: Combined PPTX ───────────────────────────────────────────────
    banner("STEP 5 / 5 — Combined PPTX")
    cmd = [python(repo2), "build_pptx.py",
           "--domain",    domain,
           "--run-id",    run_id,
           "--local-path", str(repo1.resolve())]

    if social_run_dir and social_run_dir.exists():
        cmd += ["--social-path", str(social_run_dir)]
        print(f"  Including social slides from: {social_run_dir}")
    else:
        print("  No social data — building website-only PPTX")

    run(cmd, cwd=repo2)

    # ── Copy to output ───────────────────────────────────────────────────────
    suffix = "-combined" if (social_run_dir and social_run_dir.exists()) else ""
    built  = repo2 / "output" / f"{domain}-{run_id}{suffix}.pptx"
    final  = output_dir / f"{domain}-decode-{run_id}.pptx"

    if built.exists():
        shutil.copy2(str(built), str(final))
        size_mb = final.stat().st_size / 1024 / 1024
        print(f"\n{'='*60}")
        print(f"  PIPELINE COMPLETE")
        print(f"  PPTX: {final}  ({size_mb:.1f} MB)")
        print(f"  Slides: website (17) + social ({('8' if suffix else '0')})")
        print(f"{'='*60}\n")
    else:
        print(f"\n[ERROR] Expected output not found: {built}")
        sys.exit(1)


if __name__ == "__main__":
    main()
