# Publication Verification Pipeline

Verifies an academic CV's publication list against PubMed, CrossRef, and OpenAlex — then walks you through an interactive review of anything that needs a human decision.

---

## Before You Begin

> **Windows user?** Follow the steps below, but use these substitutions everywhere in this guide:
> - Use `python` instead of `python3`
> - Use `pip` instead of `pip3`
> - Open your terminal inside VS Code: press **Ctrl+`** (the backtick key, top-left of keyboard)
>
> **Important — convert the CV file first:** The CV is a `.doc` file. Before running anything, open it in Microsoft Word and save a copy as `.docx` (File → Save As → Word Document). Then use that `.docx` file as the `--cv` argument. This avoids needing any extra software.

### Step 1 — Open a terminal

**Mac:** Press **⌘ Space**, type **Terminal**, and press Enter.  
**Windows:** Open VS Code, then press **Ctrl+`** to open the built-in terminal.

### Step 2 — Navigate to the project folder

Type the following (replace the path with wherever you saved this folder) and press Enter:

```bash
cd /path/to/cv-automation-project
```

For example, if the folder is on your Desktop:

**Mac:**
```bash
cd ~/Desktop/cv-automation-project
```

**Windows:**
```bash
cd C:\Users\YourName\Desktop\cv-automation-project
```

### Step 3 — Check your Python version

**Mac:**
```bash
python3 --version
```

**Windows:**
```bash
python --version
```

You need **Python 3.10 or newer**. If you see a version lower than 3.10 (or get an error saying Python isn't found), download the latest Python from [python.org/downloads](https://www.python.org/downloads/). On Windows, check the box that says **"Add Python to PATH"** during installation, then re-open your terminal.

### Step 4 — Install required libraries

**Mac:**
```bash
pip3 install requests rapidfuzz rich pypdf python-docx
```

**Windows:**
```bash
pip install requests rapidfuzz rich pypdf python-docx
```

---

## Quick Start — full pipeline in one command

**Mac** (can use the original `.doc` file directly):
```bash
python3 run.py \
  --cv   BCM_CV_Provenza_20250927.doc \
  --author "Nicole R Provenza"
```

**Windows** (use the `.docx` copy you saved in Step 1):
```bash
python run.py --cv BCM_CV_Provenza_20250927.docx --author "Nicole R Provenza"
```

This runs all five stages in order. When Stage 4 begins, it will ask you questions interactively in the terminal — read the prompts and type your answer, then press Enter.

**All output files are saved in the `output/` folder** (created automatically):

| File | What it is |
|---|---|
| `output/publications.csv` | Publications extracted from the CV |
| `output/fetched.json` | Raw results from all three APIs |
| `output/diff.json` | Comparison between CV and API results |
| `output/verified.csv` | Final reviewed publication list |
| `output/cv_list.txt` | **Ready-to-paste plain text for the Word document** |

The intermediate files are kept so you can re-run any individual stage without repeating the slow API calls.

---

## Stages

### Stage 1 — Parse CV → CSV

**Mac:**
```bash
python3 src/parse_cv.py --cv BCM_CV_Provenza_20250927.doc --out output/publications.csv
```
**Windows** (use the .docx copy):
```bash
python src/parse_cv.py --cv BCM_CV_Provenza_20250927.docx --out output/publications.csv
```

Reads a `.pdf`, `.docx`, `.doc`, or `.txt` CV and extracts all publication entries.  
Detects sections: peer-reviewed published, in-press, book chapters, in-preparation.  
Outputs a CSV with columns:

| Column | Description |
|---|---|
| `id` | Stable 8-char hash of the normalised title |
| `title` | Paper title |
| `authors` | Author list |
| `year` | Publication year |
| `journal` | Journal / venue |
| `doi` | DOI |
| `pmid` | PubMed ID (empty until fetch/enrich) |
| `openalex_id` | OpenAlex work ID (empty until fetch) |
| `status` | `published` / `in_press` / `preprint` / `retracted` / `unknown` |
| `pub_type` | `peer_reviewed` / `book_chapter` / `conference` / `patent` |
| `source` | `cv` |
| `notes` | Free-text notes |

---

### Stage 2 — Fetch from APIs

**Mac:**
```bash
python3 src/fetch_pubs.py \
  --csv    output/publications.csv \
  --author "Nicole R Provenza" \
  --out    output/fetched.json
```
**Windows:**
```bash
python src/fetch_pubs.py --csv output/publications.csv --author "Nicole R Provenza" --out output/fetched.json
```

Queries three sources:

| API | What it does |
|---|---|
| **PubMed** | Author search + DOI lookup |
| **CrossRef** | Author search + DOI lookup; also retraction checking |
| **OpenAlex** | Author entity resolution → full works list |

Saves `fetched.json` containing the baseline and all API results.

**Optional flags:**
- `--enrich` — also look up each existing CV entry by DOI for richer metadata (slower)
- `--email you@example.com` — your email address; including it gives you better API response times from CrossRef

---

### Stage 3 — Diff

**Mac:**
```bash
python3 src/diff_pubs.py --fetched output/fetched.json --out output/diff.json
```
**Windows:**
```bash
python src/diff_pubs.py --fetched output/fetched.json --out output/diff.json
```

Compares the baseline CSV against API results.  
Matching uses **DOI equality** (definitive) or **fuzzy title similarity** (≥ 85 token-sort-ratio).

Produces four lists in `diff.json`:

| List | Meaning |
|---|---|
| `matched` | Baseline entries confirmed by ≥ 1 API (possibly enriched with PMID / OA-ID) |
| `status_changed` | Entries whose status changed (e.g. preprint → published, or retracted) |
| `duplicates` | Pairs within the baseline that look like the same paper |
| `new_entries` | Papers the APIs found that aren't in the CV yet |

---

### Stage 4 — Interactive Review

**Mac:**
```bash
python3 src/review.py --diff output/diff.json --out output/verified.csv
```
**Windows:**
```bash
python src/review.py --diff output/diff.json --out output/verified.csv
```

Walks you through three queues:

1. **Duplicates** — for each pair: keep A / keep B / merge / keep-both / skip
2. **Status changes** — accept new status / keep old / enter custom
3. **New entries** — add / skip / edit before adding

Outputs `verified.csv` with the same columns as `publications.csv`.

---

### Stage 5 — Format for Word

**Mac:**
```bash
python3 src/format_cv.py --csv output/verified.csv --author "Nicole R Provenza" --out output/cv_list.txt
```
**Windows:**
```bash
python src/format_cv.py --csv output/verified.csv --author "Nicole R Provenza" --out output/cv_list.txt
```

Reads the verified CSV and writes a plain-text file (`cv_list.txt`) formatted to match the BCM CV section structure:

```
1. Full Papers in Peer Reviewed Journals
    a. Published
    b. Accepted/In Press
2. Full Papers Without Peer Review
    b. In Preparation
4. Books
    c. Book Chapters Written
```

**To paste into Word:**
1. Open `output/cv_list.txt` in any text editor, select all, and copy
2. Paste into the Word document, replacing the existing publication list
3. To apply bold to the author's name: press **Ctrl+H** (Find & Replace), search for `*Provenza*`, and replace with the same text in bold

> **Note:** The `--author "Nicole R Provenza"` flag extracts the surname and wraps every occurrence (`*Provenza N*`, `*Provenza NR*`, etc.) with asterisks so you can easily Find & Replace to apply bold in Word. Entries from PubMed may have abbreviated journal names (e.g., `Biol Psychiatry` instead of `Biological Psychiatry`) — check and expand these before submitting the CV.

---

## Re-running individual stages

The intermediate files let you iterate without hitting APIs again.

**Mac:**
```bash
# Tweak the parser and re-parse without re-fetching
python3 src/parse_cv.py --cv BCM_CV_Provenza_20250927.doc --out output/publications.csv

# Re-run diff with the same fetched.json
python3 src/diff_pubs.py --fetched output/fetched.json --out output/diff.json

# Re-run review interactively
python3 src/review.py --diff output/diff.json --out output/verified.csv

# Re-generate the Word-ready text file
python3 src/format_cv.py --csv output/verified.csv --author "Nicole R Provenza" --out output/cv_list.txt
```

**Windows** (use `.docx` for the CV):
```bash
python src/parse_cv.py --cv BCM_CV_Provenza_20250927.docx --out output/publications.csv
python src/diff_pubs.py --fetched output/fetched.json --out output/diff.json
python src/review.py --diff output/diff.json --out output/verified.csv
python src/format_cv.py --csv output/verified.csv --author "Nicole R Provenza" --out output/cv_list.txt
```

---

## Adjusting fuzzy match sensitivity

In `src/diff_pubs.py`, change:
```python
TITLE_THRESHOLD = 85   # lower = more matches caught, higher = stricter
```

---

## File structure

```
cv-automation-project/
├── run.py             ← the only file you need to run
├── README.md
├── src/               ← pipeline scripts (run.py calls these automatically)
│   ├── parse_cv.py    ← Stage 1: CV → publications.csv
│   ├── fetch_pubs.py  ← Stage 2: publications.csv → fetched.json
│   ├── diff_pubs.py   ← Stage 3: fetched.json → diff.json
│   ├── review.py      ← Stage 4: diff.json → verified.csv (interactive)
│   └── format_cv.py   ← Stage 5: verified.csv → cv_list.txt (Word-ready)
└── output/            ← created automatically when you run the pipeline
    ├── publications.csv
    ├── fetched.json
    ├── diff.json
    ├── verified.csv
    └── cv_list.txt    ← copy-paste this into the Word document
```
