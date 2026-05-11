#!/usr/bin/env python3
"""
Stage 2 – Query PubMed, CrossRef, and OpenAlex.

Filtering strategy (fixes the 425-entry problem):
  1. Surname + initial must appear in the structured author field of each result
  2. CrossRef results are filtered against the actual author objects (not free text)
  3. OpenAlex picks the best-matching author entity, not just the top result
  4. All three API result sets are cross-corroborated: only items seen in ≥2
     sources (or from PubMed alone, which is already precise) are kept as "new"

Usage:
    python fetch_pubs.py --csv publications.csv \
                         --author "Nicole R Provenza" \
                         --email  "you@example.com" \
                         --out fetched.json
"""

import argparse
import csv
import json
import math
import re
import sys
import time
from pathlib import Path

import requests
from rapidfuzz import fuzz

DELAY = 0.4
CORROBORATION_REQUIRED = True   # flip with --no-corroborate


# ── HTTP helper ───────────────────────────────────────────────────────────────
def get_json(url, params=None, headers=None, label=""):
    try:
        time.sleep(DELAY)
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [WARN] {label}: {e}")
        return None


# ── Author name parsing ───────────────────────────────────────────────────────
def normalize_name(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def compact_initials(text):
    tokens = re.findall(r"[A-Za-z]+", text or "")
    return "".join(token[0].lower() for token in tokens if token)


def parse_target(author_arg):
    """
    Accept: "Provenza N", "Provenza NR", "Nicole Provenza", "Nicole R Provenza"
    Returns a target dict used by all matchers.
    """
    parts = re.findall(r"[A-Za-z]+", author_arg or "")
    if not parts:
        return {"raw": author_arg, "surname": "", "initials": "", "given": "", "full_name": ""}

    if len(parts) >= 2 and len(parts[-1]) <= 3 and parts[-1].isupper():
        surname = parts[0].lower()
        initials = parts[-1].lower()
        given = ""
    elif len(parts) >= 2:
        surname = parts[-1].lower()
        given = " ".join(parts[:-1]).lower()
        initials = compact_initials(given)
    else:
        surname = parts[0].lower()
        initials = ""
        given = ""

    return {
        "raw": author_arg,
        "surname": surname,
        "initials": initials,
        "given": given,
        "full_name": normalize_name(author_arg),
    }


def author_matches_target(family, given, target):
    family_norm = normalize_name(family)
    if family_norm != target["surname"]:
        return False
    target_initials = target["initials"]
    if not target_initials:
        return True
    given_initials = compact_initials(given)
    return bool(given_initials) and given_initials.startswith(target_initials)


def author_in_string(author_str, target):
    """True if surname and matching initials appear in a freeform author string."""
    if not author_str or not target["surname"]:
        return False
    normalized = normalize_name(author_str)
    if target["full_name"] and target["full_name"] in normalized:
        return True
    if target["surname"] not in normalized:
        return False

    initials = target["initials"]
    if not initials:
        return True
    tokens = [token.lower() for token in re.findall(r"[A-Za-z]+", author_str)]
    surname = target["surname"]
    for idx, token in enumerate(tokens):
        if token != surname:
            continue
        prev = tokens[max(0, idx - 2):idx]
        next_ = tokens[idx + 1:idx + 3]
        window_initials = "".join(word[0] for word in prev + next_ if word)
        if initials[0] in window_initials:
            return True
    return False


# ── Dedup helpers ─────────────────────────────────────────────────────────────
def dedup(items):
    seen_dois, seen_titles, out = set(), [], []
    for item in items:
        doi = (item.get("doi") or "").lower().strip()
        title = (item.get("title") or "").lower().strip()
        if doi and doi in seen_dois:
            continue
        if any(fuzz.token_sort_ratio(title, t) >= 92 for t in seen_titles):
            continue
        if doi:
            seen_dois.add(doi)
        seen_titles.append(title)
        out.append(item)
    return out


# ── Corroboration ─────────────────────────────────────────────────────────────
def corroborate(all_fetched):
    """
    Cluster by title similarity; keep cluster if:
      - Contains a PubMed result (PubMed [Author] queries are precise), OR
      - Seen in ≥2 distinct API sources
    Returns one canonical entry per cluster, with IDs merged in.
    """
    if not CORROBORATION_REQUIRED:
        return all_fetched

    used = [False] * len(all_fetched)
    clusters = []
    for i, a in enumerate(all_fetched):
        if used[i]:
            continue
        cluster = [a]
        used[i] = True
        ta = (a.get("title") or "").lower()
        adoi = (a.get("doi") or "").lower().strip()
        for j, b in enumerate(all_fetched):
            if used[j]:
                continue
            tb = (b.get("title") or "").lower()
            bdoi = (b.get("doi") or "").lower().strip()
            if (adoi and bdoi and adoi == bdoi) or \
               fuzz.token_sort_ratio(ta, tb) >= 88:
                cluster.append(b)
                used[j] = True
        clusters.append(cluster)

    out = []
    for cluster in clusters:
        sources = {item.get("source","") for item in cluster}
        if "pubmed" not in sources and len(sources) < 2:
            continue  # not corroborated
        # Pick canonical: prefer pubmed > crossref > openalex
        for preferred in ("pubmed", "crossref", "openalex"):
            canonical = next((i for i in cluster if i.get("source") == preferred), None)
            if canonical:
                break
        canonical = dict(canonical)
        # Merge supplementary IDs
        for item in cluster:
            if not canonical.get("openalex_id") and item.get("openalex_id"):
                canonical["openalex_id"] = item["openalex_id"]
            if not canonical.get("pmid") and item.get("pmid"):
                canonical["pmid"] = item["pmid"]
        out.append(canonical)
    return out


# ── PubMed ────────────────────────────────────────────────────────────────────
PUBMED_SEARCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_SUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

def pubmed_search_doi(doi):
    if not doi:
        return []
    data = get_json(PUBMED_SEARCH,
                    params={"db":"pubmed","term":doi,"retmode":"json","retmax":5},
                    label="PubMed DOI")
    if not data:
        return []
    return _pm_summaries(data.get("esearchresult",{}).get("idlist",[]))

def pubmed_search_author(target):
    surname = target["surname"]
    initials = target["initials"]
    query = f"{surname.capitalize()} {initials.upper()}[Author]" if initials else f"{surname.capitalize()}[Author]"
    print(f"  [PubMed] Query: {query}")
    data = get_json(PUBMED_SEARCH,
                    params={"db":"pubmed","term":query,"retmode":"json","retmax":500},
                    label="PubMed author")
    if not data:
        return []
    ids = data.get("esearchresult",{}).get("idlist",[])
    print(f"  [PubMed] {len(ids)} PMIDs")
    results = _pm_summaries(ids)
    kept = [r for r in results if author_in_string(r.get("authors",""), target)]
    print(f"  [PubMed] {len(kept)} after author filter")
    return kept

def _pm_summaries(pmids):
    out = []
    for i in range(0, len(pmids), 50):
        batch = pmids[i:i+50]
        data = get_json(PUBMED_SUMMARY,
                        params={"db":"pubmed","id":",".join(batch),
                                "retmode":"json","retmax":50},
                        label="PubMed summary")
        if not data:
            continue
        for uid in data.get("result",{}).get("uids",[]):
            out.append(_norm_pm(data["result"][uid]))
    return out

def _norm_pm(doc):
    doi = next((a.get("value","") for a in doc.get("articleids",[])
                if a.get("idtype") == "doi"), "")
    ym = re.search(r"\b(19|20)\d{2}\b", doc.get("pubdate",""))
    return {
        "title":    doc.get("title","").rstrip("."),
        "authors":  ", ".join(a.get("name","") for a in doc.get("authors",[])
                               if a.get("authtype") == "Author"),
        "year":     ym.group(0) if ym else "",
        "journal":  doc.get("source",""),
        "doi":      doi.lower().strip(),
        "pmid":     doc.get("uid",""),
        "openalex_id": "",
        "status":   "published",
        "pub_type": "peer_reviewed",
        "source":   "pubmed",
    }


# ── CrossRef ─────────────────────────────────────────────────────────────────
CROSSREF_WORKS = "https://api.crossref.org/works"

def crossref_lookup_doi(doi, mailto):
    if not doi:
        return None
    data = get_json(f"{CROSSREF_WORKS}/{doi}",
                    headers={"User-Agent": f"pub-pipeline/1.0 (mailto:{mailto})"},
                    label="CrossRef DOI")
    if not data or data.get("status") != "ok":
        return None
    return _norm_cr(data["message"])

def crossref_search_author(target, mailto):
    surname = target["surname"]
    q = target["raw"] or surname
    print(f"  [CrossRef] Query author: {q}")
    data = get_json(CROSSREF_WORKS,
                    params={
                        "query.author": q,
                        "rows": 200,
                        "select": "DOI,title,author,published,container-title,type",
                    },
                    headers={"User-Agent": f"pub-pipeline/1.0 (mailto:{mailto})"},
                    label="CrossRef author")
    if not data:
        return []
    items = data.get("message",{}).get("items",[])
    print(f"  [CrossRef] {len(items)} raw items")
    kept = []
    for item in items:
        # Filter against structured author objects — not freeform text
        raw_authors = item.get("author", [])
        if not any(author_matches_target(a.get("family", ""), a.get("given", ""), target) for a in raw_authors):
            continue
        kept.append(_norm_cr(item))
    print(f"  [CrossRef] {len(kept)} after author filter")
    return kept

def _norm_cr(item):
    title   = (item.get("title") or [""])[0]
    authors = ", ".join(
        f"{a.get('family','')} {(a.get('given','') or '')[:1]}".strip()
        for a in item.get("author", [])
    )
    dp   = item.get("published",{}).get("date-parts",[[]])
    year = str(dp[0][0]) if dp and dp[0] else ""
    journal = (item.get("container-title") or [""])[0]
    return {
        "title":    title,
        "authors":  authors,
        "year":     year,
        "journal":  journal,
        "doi":      item.get("DOI","").lower().strip(),
        "pmid":     "",
        "openalex_id": "",
        "status":   "published",
        "pub_type": "peer_reviewed",
        "source":   "crossref",
    }


# ── OpenAlex ─────────────────────────────────────────────────────────────────
OPENALEX_WORKS   = "https://api.openalex.org/works"
OPENALEX_AUTHORS = "https://api.openalex.org/authors"

def openalex_search_author(author_arg, target):
    print(f"  [OpenAlex] Resolving author entity: {author_arg}")
    adata = get_json(OPENALEX_AUTHORS,
                     params={"search": author_arg, "per-page": 5},
                     label="OpenAlex author lookup")
    if not adata:
        return []
    candidates = [
        c for c in adata.get("results", [])
        if target["surname"] in normalize_name(c.get("display_name", ""))
    ]
    if not candidates:
        print("  [OpenAlex] No matching entity found")
        return []

    def candidate_score(candidate):
        name = normalize_name(candidate.get("display_name", ""))
        score = 0.0
        if target["full_name"] and name == target["full_name"]:
            score += 1000
        elif target["given"] and target["given"].split()[0] in name:
            score += 250
        score += math.log1p(candidate.get("works_count", 0))
        return score

    entity = max(candidates, key=candidate_score)
    print(f"  [OpenAlex] Using: {entity['display_name']} "
          f"({entity.get('works_count',0)} works)")

    all_works, cursor = [], "*"
    while True:
        wdata = get_json(OPENALEX_WORKS,
                         params={
                             "filter": f"author.id:{entity['id']}",
                             "per-page": 200, "cursor": cursor,
                             "select": ("id,title,authorships,publication_year,"
                                        "primary_location,doi,type"),
                         },
                         label="OpenAlex works")
        if not wdata:
            break
        items = wdata.get("results", [])
        all_works.extend(items)
        cursor = wdata.get("meta",{}).get("next_cursor")
        if not cursor or not items:
            break

    print(f"  [OpenAlex] {len(all_works)} works total")
    results = [_norm_oa(w) for w in all_works]
    kept = [r for r in results if author_in_string(r.get("authors",""), target)]
    print(f"  [OpenAlex] {len(kept)} after author filter")
    return kept

def _norm_oa(item):
    authorships = item.get("authorships", [])
    authors = ", ".join(
        a.get("author",{}).get("display_name","") for a in authorships)
    loc = item.get("primary_location",{}) or {}
    journal = (loc.get("source",{}) or {}).get("display_name","")
    doi = (item.get("doi","") or "").lower().replace("https://doi.org/","").strip()
    return {
        "title":    item.get("title","") or "",
        "authors":  authors,
        "year":     str(item.get("publication_year","")),
        "journal":  journal,
        "doi":      doi,
        "pmid":     "",
        "openalex_id": item.get("id","").replace("https://openalex.org/",""),
        "status":   "published",
        "pub_type": "peer_reviewed",
        "source":   "openalex",
    }


# ── Enrich baseline by DOI ────────────────────────────────────────────────────
def enrich_entries(entries, mailto):
    enriched = []
    for i, e in enumerate(entries):
        doi = e.get("doi","")
        print(f"  [{i+1}/{len(entries)}] {(e.get('title',''))[:55]}...")
        if doi:
            pm = pubmed_search_doi(doi)
            if pm:
                p = pm[0]
                if not e.get("pmid"):    e["pmid"]    = p["pmid"]
                if not e.get("year"):    e["year"]    = p["year"]
                if not e.get("journal"): e["journal"] = p["journal"]
            cr = crossref_lookup_doi(doi, mailto)
            if cr:
                if not e.get("journal"): e["journal"] = cr["journal"]
                if not e.get("authors"): e["authors"] = cr["authors"]
        enriched.append(e)
    return enriched


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    global CORROBORATION_REQUIRED
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv",    required=True)
    ap.add_argument("--author", required=True,
                    help="e.g. 'Nicole R Provenza' or 'Provenza NR'")
    ap.add_argument("--email",  default="pub-pipeline@example.com",
                    help="Your email for CrossRef polite pool")
    ap.add_argument("--out",    default="fetched.json")
    ap.add_argument("--enrich", action="store_true",
                    help="Enrich baseline entries via per-DOI lookups (slower)")
    ap.add_argument("--no-corroborate", action="store_true",
                    help="Skip cross-API corroboration (returns more, less filtered)")
    args = ap.parse_args()

    if args.no_corroborate:
        CORROBORATION_REQUIRED = False

    target = parse_target(args.author)
    print(f"[→] Target: surname='{target['surname']}', initials='{target['initials']}'")

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"[ERROR] CSV not found: {csv_path}")
    with open(csv_path, newline="", encoding="utf-8") as f:
        baseline = list(csv.DictReader(f))
    print(f"[→] Loaded {len(baseline)} baseline entries")

    if args.enrich:
        print("\n[→] Enriching baseline via DOI lookups...")
        baseline = enrich_entries(baseline, args.email)

    all_fetched = []

    print("\n── PubMed ──")
    all_fetched += pubmed_search_author(target)

    print("\n── CrossRef ──")
    all_fetched += crossref_search_author(target, args.email)

    print("\n── OpenAlex ──")
    all_fetched += openalex_search_author(args.author, target)

    print(f"\n[→] Before corroboration: {len(all_fetched)}")
    filtered = corroborate(all_fetched)
    filtered = dedup(filtered)
    print(f"[→] After corroboration + dedup: {len(filtered)}")

    output = {"baseline": baseline, "fetched": filtered, "author": args.author}
    Path(args.out).write_text(
        json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[✓] Saved → {args.out}")


if __name__ == "__main__":
    main()
