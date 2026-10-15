# Tarang receipts

Reads expense-claim receipt photos for Tarang Pharma Distribution and decides **approve** or
**review**, with reasons. Built for the Banao Technologies take-home "Task 2: Tarang Pharma
Distribution, expense receipts".

- A vision model (Gemini Flash-Lite) reads vendor, date, bill number, GSTIN and total from the photo,
  plus what the rules need: vehicle number, guest name, nights, pen marks, signs of alteration, and
  any text on the bill that tries to instruct the system.
- Plain, tested rules decide. A claim is auto-approved only if it is **under Rs 2,000 and passes
  every check**: usable photo, no injected instructions, amount not altered, claim within Rs 10 of
  the bill, dates consistent, not a repeat of an earlier bill or photo, fuel bill in the claimant's
  vehicle, food within Rs 2,500, lodging rules, readable GSTIN, confident read.
- One endpoint and one screen. The service starts without any API key and says so politely.

## Results

| | |
|---|---|
| Field accuracy, 198 held-out train claims | 94.7% vs the clerk's typing; **98.9%** after checking disagreements against the photo (most were clerk typos) |
| Decisions on train | reviews all 8 claims the clerk queried or rejected; auto-approves 86% of claims under Rs 2,000 (221 of 258) |
| Paid wrongly, found by the checks | Rs 17,283 approved by clerks on 283 train claims (repeat bills, altered amounts, over-claims); Rs 15,832 of the under-Rs-2,000 share would also pass plain auto-approval. About Rs 1.47 lakh a month at 2,400 claims (estimate from six weeks of one division) |
| Test predictions | 147 approve, 43 review (`predictions.csv`) |
| Model cost | Rs 0.13 a photo at paid Gemini rates, against Rs 12.6–31.7 a claim for a clerk |

Details: [reports/evaluation.md](reports/evaluation.md) (accuracy, decisions vs the clerk, every
disagreement), [reports/cost.md](reports/cost.md), [reports/expected_score.md](reports/expected_score.md),
[reports/audit.md](reports/audit.md) (what the data looks like), [memo.md](memo.md) (one page for
the Finance Controller, also [memo.pdf](memo.pdf)), [SUBMISSION_NOTES.md](SUBMISSION_NOTES.md) (how it was
built and measured, what was thrown away) and [DECISIONS.md](DECISIONS.md) (every judgement call and why).

Screenshots of the screen: [approve](reports/evidence/ui_approve.png),
[duplicate](reports/evidence/ui_duplicate.png), [injection stamp](reports/evidence/ui_injection.png),
[no API key](reports/evidence/ui_no_key.png).

## Run it on a clean machine

Needs Python 3.11 or newer and git.

**Windows (PowerShell)**

```powershell
git clone <repo-url> tarang-receipts
cd tarang-receipts
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # optional: put your GEMINI_API_KEY in it
uvicorn app.main:app
```

**Mac / Linux**

```bash
git clone <repo-url> tarang-receipts
cd tarang-receipts
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional: put your GEMINI_API_KEY in it
uvicorn app.main:app
```

Open http://localhost:8000. Pick "Load example" for one of three processed claims (a clean
approve, a repeat bill, a receipt with a fake "pre-verified" stamp) and press **Analyze**, or upload
any receipt photo and fill in the claim.

- **No key:** the server starts; `/api/health` says no model is configured, and Analyze returns
  HTTP 503 with a plain message. A free key comes from https://aistudio.google.com/apikey.
- The first start indexes the 283 processed claims for the duplicate check (a few seconds) into
  `cache/history.sqlite`; every analysed claim is added to it.

### API

- `GET /api/health` — whether a model key is configured, and which models.
- `POST /api/analyze` — multipart: `photo` plus `emp_id`, `submitted_on`, `expense_date`,
  `category`, `vendor`, `amount_claimed`, `rep_comment` (optional `claim_id`). Returns `fields`,
  `decision`, `reasons`, `checks` (name, passed, detail), `confidence`, `model`, `latency_ms`,
  `est_cost_inr`.

## Regenerate predictions.csv

All 467 photos are already read and cached in `cache/extractions/` (keyed by the photo's hash),
so this needs no key and makes no model calls:

```bash
python scripts/make_predictions.py --no-model
```

Without `--no-model`, any test photo missing from the cache is read by the model first. To re-read
photos from scratch: `python scripts/run_extract.py --refresh` (free tier: about 500 photos a day).

Other scripts: `scripts/evaluate.py` (accuracy and decisions vs the clerk), `scripts/cost.py`,
`scripts/audit.py` (data audit), `scripts/run_extract.py` (extraction, resumable). Tests:
`pytest -q`.

## Layout

```
app/              main.py (FastAPI) and static/index.html (the screen)
src/receipts/     extract.py (prompt, schema, post-processing), providers/ (Gemini, Groq),
                  rules.py, dedupe.py, decide.py, normalize.py, vendors.py, images.py, pricing.py
scripts/          audit, extraction, evaluation, predictions, cost
cache/            extractions/ (one JSON per photo, with raw model replies), usage.csv (every call)
data/             the task's CSVs, policy, email thread and 467 photos
reports/          audit, evaluation, error gallery, cost, expected score, screenshots
```
