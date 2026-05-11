#!/usr/bin/env python3
"""
Stage 5 – Format verified.csv as a plain-text publication list
matching the BCM CV section structure, ready to copy-paste into Word.

Usage:
    python format_cv.py --csv verified.csv --out cv_list.txt
    python format_cv.py --csv verified.csv --author "Provenza" --out cv_list.txt

The --author flag wraps the matching surname with asterisks (*Provenza N*)
so you can use Word's Find & Replace to apply bold formatting.
"""

import argparse
import csv
import re
import sys
from pathlib import Path


# Section keys, headers, and the CV section label printed above each block
SECTIONS = [
    ("1a", "1. Full Papers in Peer Reviewed Journals\n\ta. Published"),
    ("1b", "\tb. Accepted/In Press"),
    ("2a", "2. Full Papers Without Peer Review\n\ta. Published"),
    ("2b", "\tb. In Preparation"),
    ("4c", "4. Books\n\tc. Book Chapters Written"),
]


def get_section(row):
    status   = (row.get("status")   or "").strip().lower()
    pub_type = (row.get("pub_type") or "").strip().lower()

    if pub_type == "abstract":
        return None  # abstracts excluded from this output
    if pub_type == "book_chapter":
        return "4c"
    if status == "in_press":
        return "1b"
    if status == "preprint":
        return "2b"
    if pub_type == "non_peer_reviewed":
        return "2a"
    if status == "published":
        return "1a"
    return None


def build_journal_line(row):
    """Return the journal/venue string, appending the DOI if not already present."""
    jrn = (row.get("journal") or "").strip().rstrip(".")
    doi = (row.get("doi")     or "").strip()
    if doi and doi not in jrn:
        return f"{jrn}. {doi}".lstrip(". ") if jrn else doi
    return jrn


def extract_surname(author_arg):
    """Extract just the surname from any name format.

    "Nicole R Provenza" → "Provenza"
    "Provenza NR"       → "Provenza"
    "Provenza N"        → "Provenza"
    "Provenza"          → "Provenza"
    """
    parts = re.findall(r"[A-Za-z]+", author_arg or "")
    if not parts:
        return author_arg
    # If the last token is short all-uppercase initials, the surname is the first token
    if parts[-1].isupper() and len(parts[-1]) <= 3:
        return parts[0]
    # Otherwise surname is the last token (e.g. "Nicole R Provenza")
    return parts[-1]


def format_entry(row, highlight_surname=""):
    authors = (row.get("authors") or "").strip().rstrip(".,")
    year    = (row.get("year")    or "").strip()
    title   = (row.get("title")   or "").strip().rstrip(".")
    status  = (row.get("status")  or "").strip().lower()
    jline   = build_journal_line(row)

    if highlight_surname:
        # Wrap "Surname NR" or "Surname, N." with asterisks for easy Find & Replace in Word
        # Pattern 1: surname followed by initials (e.g. "Provenza NR", "Provenza, N.")
        marked, n = re.subn(
            rf"({re.escape(highlight_surname)},?\s+[A-Z][A-Z.]*\.?)",
            r"*\1*",
            authors,
            count=1,
            flags=re.IGNORECASE,
        )
        if n:
            authors = marked
        else:
            # Pattern 2: surname alone (e.g. full-name entries like "Nicole R. Provenza")
            authors = re.sub(
                rf"\b({re.escape(highlight_surname)})\b",
                r"*\1*",
                authors,
                count=1,
                flags=re.IGNORECASE,
            )

    if status == "published" and year:
        return f"{authors} ({year}). {title}. {jline}."
    else:
        # in_press, preprint, book_chapter — no year in parentheses
        return f"{authors}. {title}. {jline}."


def main():
    ap = argparse.ArgumentParser(description="Format verified CSV as plain-text CV publication list")
    ap.add_argument("--csv",    required=True,          help="verified.csv from review.py")
    ap.add_argument("--out",    default="cv_list.txt",  help="Output text file")
    ap.add_argument("--author", default="",
                    help="Author name in any format, e.g. 'Nicole R Provenza' or 'Provenza NR'")
    args = ap.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"[ERROR] Not found: {csv_path}")

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Sort by year descending; entries without a year go at the end
    def sort_key(r):
        y = (r.get("year") or "").strip()
        return (1 if not y else 0, -(int(y) if y.isdigit() else 0))

    rows.sort(key=sort_key)

    buckets = {key: [] for key, _ in SECTIONS}
    skipped = 0
    for row in rows:
        sec = get_section(row)
        if sec:
            buckets[sec].append(row)
        else:
            skipped += 1

    highlight_surname = extract_surname(args.author) if args.author else ""

    out_lines = []
    if highlight_surname:
        out_lines.append(
            f"NOTE: '{highlight_surname}' is marked with asterisks (*like this*).\n"
            f"      In Word: Ctrl+H → Find '*{highlight_surname}*', Replace with bold text.\n"
        )

    for key, header in SECTIONS:
        out_lines.append(header)
        entries = buckets[key]
        if not entries:
            out_lines.append("\tNone")
            out_lines.append("")
        else:
            for row in entries:
                out_lines.append(format_entry(row, highlight_surname))
                out_lines.append("")  # blank line between entries

    total = sum(len(v) for v in buckets.values())
    Path(args.out).write_text("\n".join(out_lines), encoding="utf-8")
    print(f"[✓] Formatted {total} entries → {args.out}")
    if skipped:
        print(f"    ({skipped} entries skipped: abstracts or unrecognised status)")


if __name__ == "__main__":
    main()
