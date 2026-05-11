#!/usr/bin/env python3
"""
Stage 3 – Diff the baseline CSV against fetched API results.

Produces a diff.json with four lists:
  - matched      : baseline entry confirmed by ≥1 API source (with enrichment)
  - status_changed: baseline entry whose status changed (e.g. preprint → published,
                    or published → retracted)
  - duplicates   : pairs of entries that look like the same paper (title similarity)
  - new_entries  : papers found by APIs that are NOT in the baseline

Usage:
    python diff_pubs.py --fetched fetched.json --out diff.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

from rapidfuzz import fuzz

TITLE_THRESHOLD = 85   # 0–100 token-sort-ratio to call two titles a match
DOI_MATCH       = True # always treat identical DOIs as matches


# ── Normalisation helpers ─────────────────────────────────────────────────────
def norm(text: str) -> str:
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def title_sim(a: str, b: str) -> float:
    return fuzz.token_sort_ratio(norm(a), norm(b))


def doi_match(a: str, b: str) -> bool:
    a = (a or "").lower().strip().rstrip(".")
    b = (b or "").lower().strip().rstrip(".")
    return bool(a and b and a == b)


def find_match(entry: dict, pool: list[dict]) -> dict | None:
    """Return the best match from pool for this entry, or None."""
    best_score = 0
    best = None
    edoi = (entry.get("doi") or "").lower().strip()
    for candidate in pool:
        cdoi = (candidate.get("doi") or "").lower().strip()
        if doi_match(edoi, cdoi):
            return candidate          # DOI match is definitive
        score = title_sim(entry.get("title", ""), candidate.get("title", ""))
        if score > best_score:
            best_score = score
            best = candidate
    if best_score >= TITLE_THRESHOLD:
        return best
    return None


# ── Status change detection ───────────────────────────────────────────────────
STATUS_RANK = {
    "preprint": 0,
    "unknown": 0,
    "in_press": 1,
    "published": 2,
    "retracted": 3,
}

def detect_status_change(baseline_status: str, api_status: str) -> str | None:
    """Return a human-readable description of the change, or None."""
    bs = (baseline_status or "").lower()
    as_ = (api_status or "").lower()
    if bs == as_:
        return None
    # Retraction always flags
    if as_ == "retracted":
        return f"RETRACTED (was: {bs})"
    # Preprint/in_press → published is a normal progression
    br = STATUS_RANK.get(bs, 0)
    ar = STATUS_RANK.get(as_, 0)
    if ar > br:
        return f"status advanced: {bs} → {as_}"
    if ar < br:
        return f"status downgraded: {bs} → {as_} (needs review)"
    return None


# ── Check for retractions via CrossRef ───────────────────────────────────────
def check_retraction_crossref(doi: str) -> bool:
    """Lightweight check: CrossRef flags retracted works with type='retraction'
    or has a relation 'is-retracted-by'. We just check the update-to field."""
    if not doi:
        return False
    try:
        import time
        import requests
        time.sleep(0.3)
        r = requests.get(
            f"https://api.crossref.org/works/{doi}",
            headers={"User-Agent": "pub-pipeline/1.0"},
            timeout=10,
        )
        if r.status_code != 200:
            return False
        data = r.json().get("message", {})
        # 'update-to' list contains retractions
        updates = data.get("update-to", [])
        for u in updates:
            if u.get("type", "").lower() == "retraction":
                return True
        # type field directly
        if data.get("type", "").lower() == "retraction":
            return True
    except Exception:
        pass
    return False


# ── Internal duplicate detection (within baseline) ───────────────────────────
def find_internal_duplicates(entries: list[dict]) -> list[dict]:
    """Find pairs within the baseline that look like the same paper."""
    pairs = []
    seen = set()
    for i, a in enumerate(entries):
        for j, b in enumerate(entries):
            if i >= j:
                continue
            pair_key = tuple(sorted([a["id"], b["id"]]))
            if pair_key in seen:
                continue
            if doi_match(a.get("doi", ""), b.get("doi", "")) or \
               title_sim(a.get("title", ""), b.get("title", "")) >= TITLE_THRESHOLD:
                pairs.append({
                    "type": "internal_duplicate",
                    "entry_a": a,
                    "entry_b": b,
                    "similarity": round(title_sim(a.get("title",""), b.get("title","")), 1),
                    "doi_match": doi_match(a.get("doi",""), b.get("doi","")),
                })
                seen.add(pair_key)
    return pairs


# ── Main diff ─────────────────────────────────────────────────────────────────
def diff(fetched_path: Path) -> dict:
    raw = json.loads(fetched_path.read_text(encoding="utf-8"))
    baseline: list[dict] = raw["baseline"]
    fetched:  list[dict] = raw["fetched"]

    matched        = []
    status_changed = []
    new_entries    = []

    # Tag each baseline entry
    baseline_matched_ids = set()

    for entry in baseline:
        api_hit = find_match(entry, fetched)
        if api_hit:
            baseline_matched_ids.add(entry["id"])
            # Enrich: fill blank fields from API
            enriched = dict(entry)
            for field in ("pmid", "openalex_id", "journal", "authors", "year"):
                if not enriched.get(field) and api_hit.get(field):
                    enriched[field] = api_hit[field]
            enriched["_api_source"] = api_hit.get("source", "")

            # Status change?
            change = detect_status_change(entry.get("status",""), api_hit.get("status",""))
            if change:
                enriched["_status_change"] = change
                enriched["_api_status"] = api_hit.get("status","")
                status_changed.append(enriched)
            else:
                matched.append(enriched)
        else:
            # No API hit — still keep it in matched (CV is source of truth)
            # but flag it as unverified
            entry_copy = dict(entry)
            entry_copy["_api_source"] = "none"
            entry_copy["_unverified"] = True
            matched.append(entry_copy)

    # New entries: fetched items not in baseline
    for api_item in fetched:
        if find_match(api_item, baseline) is None:
            new_entries.append(api_item)

    # Internal duplicates within baseline
    duplicates = find_internal_duplicates(baseline)

    print(f"  Matched / confirmed : {len([m for m in matched if not m.get('_unverified')])}")
    print(f"  Unverified (no API) : {len([m for m in matched if m.get('_unverified')])}")
    print(f"  Status changed      : {len(status_changed)}")
    print(f"  Internal duplicates : {len(duplicates)}")
    print(f"  New (API only)      : {len(new_entries)}")

    return {
        "matched":        matched,
        "status_changed": status_changed,
        "duplicates":     duplicates,
        "new_entries":    new_entries,
    }


def main():
    ap = argparse.ArgumentParser(description="Diff baseline vs fetched API results")
    ap.add_argument("--fetched", required=True, help="fetched.json from fetch_pubs.py")
    ap.add_argument("--out",     default="diff.json", help="Output diff JSON")
    args = ap.parse_args()

    fpath = Path(args.fetched)
    if not fpath.exists():
        sys.exit(f"[ERROR] Not found: {fpath}")

    print(f"[→] Diffing: {fpath}")
    result = diff(fpath)

    Path(args.out).write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[✓] Diff saved → {args.out}")


if __name__ == "__main__":
    main()
