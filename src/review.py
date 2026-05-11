#!/usr/bin/env python3
"""
Stage 4 – Interactive CLI review of diff results.

Walks the user through three review queues:
  1. Duplicates       – keep A, keep B, or merge
  2. Status changes   – accept new status or keep old
  3. New entries      – add to list or skip

Outputs a final verified CSV.

Usage:
    python review.py --diff diff.json --out verified.csv [--auto-accept-matched]
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.prompt import Prompt

console = Console()

CSV_FIELDS = [
    "id", "title", "authors", "year", "journal",
    "doi", "pmid", "openalex_id", "status", "pub_type", "source", "notes",
]


def make_id(title: str) -> str:
    norm = re.sub(r"\W+", " ", title.lower()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:8]


def truncate(s: str, n: int = 80) -> str:
    s = (s or "").strip()
    return s[:n] + "…" if len(s) > n else s


def entry_panel(entry: dict, label: str = "", color: str = "cyan") -> Panel:
    lines = [
        f"[bold]{truncate(entry.get('title','(no title)'), 90)}[/bold]",
        f"  Authors : {truncate(entry.get('authors',''), 70)}",
        f"  Year    : {entry.get('year','')}   Journal : {truncate(entry.get('journal',''), 50)}",
        f"  DOI     : {entry.get('doi','(none)')}",
        f"  PMID    : {entry.get('pmid','(none)')}   OA-ID: {entry.get('openalex_id','(none)')}",
        f"  Status  : {entry.get('status','')}   Type: {entry.get('pub_type','')}",
    ]
    if entry.get("_status_change"):
        lines.append(f"  [yellow]⚠ Change: {entry['_status_change']}[/yellow]")
    if entry.get("_unverified"):
        lines.append("  [dim]⚠ Not found in any API[/dim]")
    return Panel("\n".join(lines), title=f"[{color}]{label}[/{color}]",
                 border_style=color, expand=True)


def prompt_choice(prompt: str, choices: list[str]) -> str:
    choices_str = "/".join(choices)
    while True:
        val = Prompt.ask(f"[bold]{prompt}[/bold] [{choices_str}]").strip().lower()
        if val in [c.lower() for c in choices]:
            return val
        console.print(f"[red]Please enter one of: {choices_str}[/red]")


def clean_entry(e: dict) -> dict:
    """Strip internal diff fields; ensure all CSV fields present."""
    out = {}
    for f in CSV_FIELDS:
        out[f] = e.get(f, "")
    # Regenerate id if missing
    if not out["id"] and out["title"]:
        out["id"] = make_id(out["title"])
    return out


# ── Queue 1: Duplicates ───────────────────────────────────────────────────────
def review_duplicates(pairs: list[dict]) -> tuple[list[dict], set[str]]:
    """Returns (entries_to_add, ids_to_drop)."""
    if not pairs:
        console.print("[dim]No internal duplicates found.[/dim]\n")
        return [], set()

    console.rule(f"[bold yellow]DUPLICATES ({len(pairs)} pairs)[/bold yellow]")
    to_drop = set()
    to_add  = []   # merged entries

    for i, pair in enumerate(pairs, 1):
        a = pair["entry_a"]
        b = pair["entry_b"]
        sim = pair.get("similarity", "?")
        console.print(f"\n[bold]Pair {i}/{len(pairs)}[/bold]  "
                      f"title similarity: {sim}%  "
                      f"DOI match: {pair.get('doi_match', False)}")
        console.print(entry_panel(a, "Entry A", "cyan"))
        console.print(entry_panel(b, "Entry B", "magenta"))

        choice = prompt_choice(
            "Action", ["A", "B", "merge", "keep-both", "skip"])

        if choice == "a":
            to_drop.add(b["id"])
        elif choice == "b":
            to_drop.add(a["id"])
        elif choice == "merge":
            # Prefer non-empty fields from A, fill with B
            merged = dict(a)
            for field in CSV_FIELDS:
                if not merged.get(field) and b.get(field):
                    merged[field] = b[field]
            merged["notes"] = (merged.get("notes","") +
                               f" [merged from duplicate {b['id']}]").strip()
            to_drop.add(a["id"])
            to_drop.add(b["id"])
            to_add.append(merged)
            console.print("[green]✓ Will merge into single entry.[/green]")
        elif choice == "keep-both":
            pass  # do nothing
        else:
            pass  # skip = keep both

    return to_add, to_drop


# ── Queue 2: Status changes ───────────────────────────────────────────────────
def review_status_changes(entries: list[dict]) -> list[dict]:
    if not entries:
        console.print("[dim]No status changes detected.[/dim]\n")
        return []

    console.rule(f"[bold yellow]STATUS CHANGES ({len(entries)})[/bold yellow]")
    resolved = []

    for i, entry in enumerate(entries, 1):
        console.print(f"\n[bold]Change {i}/{len(entries)}[/bold]")
        console.print(entry_panel(entry, "Entry with changed status", "yellow"))
        old_status = entry.get("status", "")
        new_status = entry.get("_api_status", "")
        console.print(f"  CV status  : [cyan]{old_status}[/cyan]")
        console.print(f"  API status : [yellow]{new_status}[/yellow]")
        console.print(f"  Change desc: {entry.get('_status_change','')}")

        choice = prompt_choice(
            f"Accept new status '{new_status}'?", ["yes", "no", "custom"])

        e = dict(entry)
        if choice == "yes":
            e["status"] = new_status
            e["notes"] = (e.get("notes","") +
                          f" [status updated from {old_status} to {new_status}]").strip()
        elif choice == "custom":
            e["status"] = Prompt.ask("Enter custom status").strip()
        # else: keep old status

        resolved.append(e)

    return resolved


# ── Queue 3: New entries ──────────────────────────────────────────────────────
def review_new_entries(entries: list[dict]) -> list[dict]:
    if not entries:
        console.print("[dim]No new entries from APIs.[/dim]\n")
        return []

    console.rule(f"[bold yellow]NEW ENTRIES FROM APIs ({len(entries)})[/bold yellow]")
    to_add = []

    # Sort by year descending
    entries_sorted = sorted(entries, key=lambda e: e.get("year",""), reverse=True)

    for i, entry in enumerate(entries_sorted, 1):
        console.print(f"\n[bold]New entry {i}/{len(entries_sorted)}[/bold]")
        console.print(entry_panel(entry, f"API source: {entry.get('source','?')}", "green"))

        choice = prompt_choice("Add to verified list?", ["yes", "no", "edit"])

        if choice == "yes":
            to_add.append(entry)
        elif choice == "edit":
            e = dict(entry)
            console.print("[dim]Press Enter to keep current value.[/dim]")
            for field in ("title", "authors", "year", "journal", "doi", "status", "pub_type"):
                current = e.get(field, "")
                val = Prompt.ask(f"  {field}", default=current)
                e[field] = val
            e["source"] = "manual"
            to_add.append(e)

    return to_add


# ── Summary table ─────────────────────────────────────────────────────────────
def print_summary(final: list[dict]):
    console.rule("[bold green]FINAL LIST SUMMARY[/bold green]")
    t = Table(box=box.SIMPLE, show_header=True)
    t.add_column("pub_type", style="cyan")
    t.add_column("status",   style="yellow")
    t.add_column("count",    style="bold white", justify="right")

    from collections import Counter
    counts = Counter((e.get("pub_type","?"), e.get("status","?")) for e in final)
    for (pt, st), n in sorted(counts.items()):
        t.add_row(pt, st, str(n))
    t.add_row("─" * 20, "─" * 15, "─" * 5)
    t.add_row("TOTAL", "", str(len(final)))
    console.print(t)


# ── Write CSV ─────────────────────────────────────────────────────────────────
def write_csv(entries: list[dict], out_path: Path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(entries)
    console.print(f"\n[bold green][✓] Wrote {len(entries)} entries → {out_path}[/bold green]")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Interactive review of publication diff")
    ap.add_argument("--diff",             required=True, help="diff.json from diff_pubs.py")
    ap.add_argument("--out",              default="verified.csv", help="Output verified CSV")
    ap.add_argument("--auto-accept-matched", action="store_true",
                    help="Skip review of cleanly matched entries")
    args = ap.parse_args()

    diff_path = Path(args.diff)
    if not diff_path.exists():
        sys.exit(f"[ERROR] Not found: {diff_path}")

    data = json.loads(diff_path.read_text(encoding="utf-8"))

    matched        = data.get("matched", [])
    status_changed = data.get("status_changed", [])
    duplicates     = data.get("duplicates", [])
    new_entries    = data.get("new_entries", [])

    console.print(Panel(
        f"[bold]Publication Review[/bold]\n\n"
        f"  Matched entries   : {len(matched)}\n"
        f"  Status changes    : {len(status_changed)}\n"
        f"  Duplicate pairs   : {len(duplicates)}\n"
        f"  New (API only)    : {len(new_entries)}",
        border_style="blue"))

    # ── Step 1: Duplicates
    merged_entries, ids_to_drop = review_duplicates(duplicates)

    # ── Step 2: Status changes
    resolved_changes = review_status_changes(status_changed)

    # ── Step 3: New entries
    added_entries = review_new_entries(new_entries)

    # ── Assemble final list
    # Start with matched, drop any flagged as duplicates, apply status updates
    status_update_map = {e["id"]: e for e in resolved_changes}
    final = []

    for e in matched:
        if e["id"] in ids_to_drop:
            continue
        # Apply status update if resolved
        if e["id"] in status_update_map:
            e = status_update_map[e["id"]]
        final.append(clean_entry(e))

    for e in merged_entries:
        final.append(clean_entry(e))

    for e in resolved_changes:
        # Only add if not already via matched
        if e["id"] not in {x["id"] for x in final}:
            final.append(clean_entry(e))

    for e in added_entries:
        final.append(clean_entry(e))

    # Deduplicate by id one last time
    seen = set()
    deduped = []
    for e in final:
        if e["id"] not in seen:
            deduped.append(e)
            seen.add(e["id"])

    print_summary(deduped)
    write_csv(deduped, Path(args.out))


if __name__ == "__main__":
    main()
