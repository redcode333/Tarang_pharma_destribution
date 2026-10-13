# tarang-receipts

Take-home for Banao Technologies — "Task 2: Tarang Pharma Distribution, expense receipts".
Python 3.11.

> DRAFT: written from the task prompt before the data files were available.
> Every number below must be checked against `data/email-thread.txt`,
> `data/expense-policy.pdf` and `data/README.txt` once they are in place.

## Deliverables

1. `predictions.csv` — same format as `data/sample_submission.csv`.
2. A working service:
   - one endpoint: receipt photo + claim → extracted fields, decision, reasons
   - one web screen
   - must start with no API key and fail politely
3. Evidence that it works.
4. One-page memo to Nilesh, with rupee savings.
5. Screen recording, 3 minutes or less.
6. The filled `submission-form.md`.

## Key facts (email thread + policy)

- Auto-approve threshold: Rs 2,000.
- Field-accuracy target: 95%.
- Clerk cost: Rs 38,000 × 2 per month.
- Handling time: 3.5 min per claim.
- Each query back to an employee: Rs 45, plus a 1-week payment delay.
- Volume: 2,400 claims/month company-wide (Nagpur ≈ 240).
- About 90% of claims are under Rs 2,000.
- Wrong payouts are rarely recovered.

## Policy rules

- Fuel: the bill must show the vehicle number.
- TODO: remaining rules (Food onwards) — the task prompt was cut off here; fill in from `data/expense-policy.pdf`.

## Data

- `data/` — `claims_train.csv`, `claims_test.csv`, `employees.csv`, `sample_submission.csv`,
  `expense-policy.pdf`, `email-thread.txt`, `README.txt`, `submission-form.md`
- `data/images/` — receipt photos

## Commands

Python 3.11 lives in a conda env at `.venv/` (no `activate` needed; call the interpreter directly).

- Tests: `.venv/python.exe -m pytest -q`
- Data audit: `.venv/python.exe scripts/audit.py` → writes `reports/audit.md`, caches image hashes in `cache/image_stats.csv`
- Recreate the env: `conda create -y -p ./.venv python=3.11 pip`, then `.venv/python.exe -m pip install pandas pillow imagehash pytest`

## Code

- `src/receipts/normalize.py` — `resolve_image`, `parse_amount`, `parse_date`, `gstin_problems`
- `scripts/audit.py` — data audit; column names are constants at the top of the file
- `DECISIONS.md` — decision log, newest last

## Repo rules

- `.gitignore` covers only venv, `.env` and `__pycache__`. `predictions.csv` and `cache/` are committed on purpose.
- Never commit the task-portal link or any token.
