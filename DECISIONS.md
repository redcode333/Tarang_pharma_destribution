# Decisions

Newest last. Each entry: what was decided, and why.

## 2026-10-13 — Audit tooling (written before the data arrived)

`scripts/audit.py` and `src/receipts/normalize.py` were built and tested against
synthetic data only. **No finding from the real data is recorded here yet**; entries
based on `reports/audit.md` get added once the audit has run on the real files.

- **Image references match on file name only.** Folder parts (`/` or `\`) are dropped,
  case is ignored, and `.jpeg` equals `.jpg`. No fuzzy matching: a reference that still
  does not match is reported as missing rather than guessed.
- **A comma in an amount is only ever a thousands/lakh separator.** `1,250` and
  `1,25,000` parse; `1250,50` fails and is reported. A silent `125050` would be a wrong
  payout, a parse failure is just a query.
- **Negative amounts do not parse.** They are reported as failures for a human to look at.
- **Ambiguous DD/MM vs MM/DD dates take the reading closest to, and not after,
  `submitted_on`.** If neither reading is on or before the submission date, or there is
  no usable submission date, the value is read day-first (the Indian norm).
  Known cost: when both readings fall before submission, the later one wins even if it
  is the month-first reading. The audit counts how often each case happens.
- **Perceptual hashes are taken after applying EXIF rotation**, so a re-saved or rotated
  copy of a receipt still matches. Near-duplicate threshold is pHash distance <= 6; to be
  revisited once real pairs can be eyeballed.
- **"Sideways" is reported as two separate signals**: EXIF orientation 5-8 (certain, and
  fixable by applying EXIF), and wider-than-tall after EXIF (a hint only, since a
  landscape bill also lands there).
- **GSTIN validity = length 15, state code 01-38/97/99, PAN pattern, mod-36 check
  character.** Case and spaces are cleaned before checking, and counted separately.
- **Clerk-note themes are keyword rules, and a note can carry several themes.** Notes that
  match nothing are listed in the report so the rules can be tuned against real wording.
- **Column names are taken from the task brief** and live as constants at the top of
  `scripts/audit.py`. A section whose columns are missing is skipped with a note instead
  of crashing; section 0 of the report prints the real headers.
- **Python 3.11 via a conda env at `.venv/`** (3.11 was not installed system-wide).
