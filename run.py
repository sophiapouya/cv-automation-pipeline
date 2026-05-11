#!/usr/bin/env python3
"""
Publication Verification Pipeline — master runner
--------------------------------------------------
Runs all five stages in sequence. All output files go into the output/
folder by default (created automatically if it doesn't exist).

Stages:
  1. parse   - CV  → output/publications.csv
  2. fetch   - output/publications.csv + author name → output/fetched.json
  3. diff    - output/fetched.json → output/diff.json
  4. review  - output/diff.json → output/verified.csv   (interactive)
  5. format  - output/verified.csv → output/cv_list.txt

Full run:
    python run.py --cv BCM_CV_Provenza_20250927.doc \
                  --author "Nicole R Provenza" \
                  --out output/verified.csv

Individual stages:
    python run.py --stage parse  --cv cv.docx
    python run.py --stage fetch  --author "Nicole R Provenza"
    python run.py --stage diff
    python run.py --stage review
    python run.py --stage format
"""

import argparse
import subprocess
import sys
from pathlib import Path


STAGES = ["parse", "fetch", "diff", "review", "format"]


def run(cmd: list[str]):
    print(f"\n{'─'*60}")
    print(f"  $ {' '.join(cmd)}")
    print(f"{'─'*60}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        sys.exit(f"\n[ERROR] Stage failed with exit code {result.returncode}")


def main():
    ap = argparse.ArgumentParser(
        description="Publication verification pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # What to run
    ap.add_argument("--stage", choices=STAGES + ["all"],
                    default="all", help="Which stage to run (default: all)")

    # Input files
    ap.add_argument("--cv",      help="CV file (.pdf, .docx, .doc, .txt)")
    ap.add_argument("--author",  help="Author name for API searches, e.g. 'Nicole R Provenza'")

    # Intermediate / output files (all default to output/ folder)
    ap.add_argument("--csv",      default="output/publications.csv",
                    help="Baseline CSV path (default: output/publications.csv)")
    ap.add_argument("--fetched",  default="output/fetched.json",
                    help="Fetched JSON path (default: output/fetched.json)")
    ap.add_argument("--diff",     default="output/diff.json",
                    help="Diff JSON path (default: output/diff.json)")
    ap.add_argument("--out",      default="output/verified.csv",
                    help="Final verified CSV path (default: output/verified.csv)")
    ap.add_argument("--cv-list",  default="output/cv_list.txt",
                    help="Formatted plain-text CV list (default: output/cv_list.txt)")

    # Options
    ap.add_argument("--enrich", action="store_true",
                    help="Enrich existing entries via DOI lookups (fetch stage, slower)")
    ap.add_argument("--auto-accept-matched", action="store_true",
                    help="Skip review of cleanly matched entries (review stage)")

    args = ap.parse_args()

    src = Path(__file__).parent / "src"
    parse_script  = str(src / "parse_cv.py")
    fetch_script  = str(src / "fetch_pubs.py")
    diff_script   = str(src / "diff_pubs.py")
    review_script = str(src / "review.py")
    format_script = str(src / "format_cv.py")

    # Create output directory before any stage writes to it
    out_dir = Path(args.csv).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stage = args.stage

    # ── Stage 1: Parse ────────────────────────────────────────────────────────
    if stage in ("all", "parse"):
        if not args.cv:
            sys.exit("[ERROR] --cv is required for the parse stage")
        run([sys.executable, parse_script,
             "--cv", args.cv,
             "--out", args.csv])

    # ── Stage 2: Fetch ────────────────────────────────────────────────────────
    if stage in ("all", "fetch"):
        if not args.author:
            sys.exit("[ERROR] --author is required for the fetch stage")
        cmd = [sys.executable, fetch_script,
               "--csv",    args.csv,
               "--author", args.author,
               "--out",    args.fetched]
        if args.enrich:
            cmd.append("--enrich")
        run(cmd)

    # ── Stage 3: Diff ─────────────────────────────────────────────────────────
    if stage in ("all", "diff"):
        run([sys.executable, diff_script,
             "--fetched", args.fetched,
             "--out",     args.diff])

    # ── Stage 4: Review ───────────────────────────────────────────────────────
    if stage in ("all", "review"):
        cmd = [sys.executable, review_script,
               "--diff", args.diff,
               "--out",  args.out]
        if args.auto_accept_matched:
            cmd.append("--auto-accept-matched")
        run(cmd)

    # ── Stage 5: Format ───────────────────────────────────────────────────────
    if stage in ("all", "format"):
        cmd = [sys.executable, format_script,
               "--csv", args.out,
               "--out", args.cv_list]
        if args.author:
            cmd += ["--author", args.author]
        run(cmd)

    print(f"\n✓ Pipeline complete.")
    print(f"  Verified CSV : {args.out}")
    print(f"  CV text list : {args.cv_list}")


if __name__ == "__main__":
    main()
