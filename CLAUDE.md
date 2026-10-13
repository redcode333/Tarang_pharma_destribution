# tarang-receipts

Take-home for Banao Technologies — "Task 2: Tarang Pharma Distribution, expense receipts".
Python 3.11.

## Deliverables

1. `predictions.csv` — same format as `data/sample_submission.csv`: one row per claim in
   `claims_test.csv` with `claim_id, vendor, receipt_date (YYYY-MM-DD), receipt_number, gstin,
   total_amount, decision (approve | review), reason`.
2. A working service:
   - one endpoint: receipt photo + claim → extracted fields, decision, reasons
   - one web screen
   - must start with no API key and fail politely
3. Evidence that it works.
4. One-page memo to Nilesh, with rupee savings.
5. Screen recording, 3 minutes or less.
6. The filled `submission-form.md` (not in `data/` yet).

## Key facts (checked against `data/email-thread.txt` and `data/expense-policy.pdf`)

- Brief from Nilesh Agrawal (Finance Controller): fields read off the photo at 95% or better,
  claims under Rs 2,000 auto-approved, clerks look only at Rs 2,000 and above, plus a savings
  number and a running cost per photo below what a clerk costs.
- Two finance clerks at Rs 38,000 a month each, loaded.
- Typing and checking one claim: about 3.5 minutes.
- A claim queried back to a rep: about Rs 45, and the rep is paid a week later.
- Amol Kathane (Area Sales Manager): reps must not wait longer to be paid.
- Volume: about 2,400 photo claims a month company-wide (about 160 field staff); the Nagpur
  division (16 people) is about a tenth. About nine in ten claims are under Rs 2,000.
- Wrong payouts are recovered from salary only if found within the same quarter; little is found.
- Priya Bhende (clerk): approves small bills on amount alone; saw the same Hotel Shubham bill
  twice with slightly different amounts and let it go; has stopped opening the daily Rs 30
  parking slips from Rohan, Pooja and Harshal; unsure whether pen marks on bills matter.
- Sameer Joshi (IT): `image_file` may carry a full phone path or `.jpeg`; a few uploads never
  reached the server; dates and amounts are typed free-hand; sideways photos are not fixed.

## Policy rules (expense policy v3.2)

- Fuel: actuals; the bill must show the vehicle number (vehicle numbers are in `employees.csv`).
- Food: meals with doctors or stockists up to Rs 2,500 per bill without prior approval; field meals at actuals.
- Lodging: outstation nights only. Grades M1 and M2 up to Rs 3,000 per night before GST, M3 up to
  Rs 4,500. Must be a GST tax invoice in the employee's name.
- Local travel: cab or auto at actuals, with the driver's receipt.
- Courier: at actuals.
- Parking: at actuals; daily parking is expected for Nagpur city reps.
- The claim amount is the bill amount; rounding to the nearest Rs 10 is accepted.
- A bill is claimed once, by the person who paid it.
- A bill must not be altered. Notes written on it (doctor's name, head count, "paid") are allowed.
- No readable bill photo → queried back to the rep.
- Claiming a bill twice, claiming a colleague's bill, or altering a bill is misconduct.

## Data

- `data/` — `claims_train.csv` (283 rows, with clerk columns and status), `claims_test.csv` (190 rows),
  `employees.csv`, `sample_submission.csv`, `expense-policy.pdf`, `email-thread.txt`, `README.txt`.
  The portal adds a UUID prefix to every download; strip it.
- `data/images/` — receipt photos, 467 expected. Import with `scripts/import_images.py`.
- `reports/audit.md` — what the data looks like; read it before changing decision rules.

## Commands

Python 3.11 lives in a conda env at `.venv/` (no `activate` needed; call the interpreter directly).

- Tests: `.venv/python.exe -m pytest -q`
- Import photos: `.venv/python.exe scripts/import_images.py --downloads <browser download folder>`
- Data audit: `.venv/python.exe scripts/audit.py` → writes `reports/audit.md`, caches image hashes in `cache/image_stats.csv`
- Recreate the env: `conda create -y -p ./.venv python=3.11 pip`, then `.venv/python.exe -m pip install pandas pillow imagehash pytest`

## Code

- `src/receipts/normalize.py` — `resolve_image`, `parse_amount`, `parse_date`, `gstin_problems`
- `scripts/audit.py` — data audit; column names are constants at the top of the file
- `scripts/import_images.py` — moves portal downloads into `data/images/`
- `DECISIONS.md` — decision log, newest last

## Repo rules

- `.gitignore` covers only venv, `.env` and `__pycache__`. `predictions.csv` and `cache/` are committed on purpose.
- Never commit the task-portal link or any token.
