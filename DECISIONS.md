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
- ~~Ambiguous DD/MM vs MM/DD dates take the reading closest to, and not after,
  `submitted_on`.~~ Superseded the same day, see the next section.
- **Perceptual hashes are taken after applying EXIF rotation**, so a re-saved or rotated
  copy of a receipt still matches. Near-duplicate threshold is pHash distance <= 6; to be
  revisited once real pairs can be eyeballed.
- **"Sideways" is reported as two separate signals**: EXIF orientation 5-8 (certain, and
  fixable by applying EXIF), and wider-than-tall after EXIF (a hint only, since a
  landscape bill also lands there).
- **GSTIN validity = length 15, state code 01-38/97/99, PAN pattern, mod-36 check
  character.** Case and spaces are cleaned before checking, and counted separately.
- ~~Clerk-note themes are keyword rules, and a note can carry several themes.~~ Rewritten
  from the real notes, see the next section.
- **Column names are taken from the task brief** and live as constants at the top of
  `scripts/audit.py`. A section whose columns are missing is skipped with a note instead
  of crashing; section 0 of the report prints the real headers. (The real headers matched.)
- **Python 3.11 via a conda env at `.venv/`** (3.11 was not installed system-wide).

## 2026-10-13 — First audit of the real data

Source: `reports/audit.md`, from 283 processed claims (submitted 9 June to 18 July 2026)
and 190 unprocessed ones (19 July to 7 August). **Only 2 of the expected 467 photos were
on disk**, so nothing below rests on the images; the photo findings are still open.

### Findings

1. **Bills claimed twice are the leak, and today's check misses them.** 15 bill numbers
   appear on two claims each. 14 of the 15 later claims were approved (Rs 7,774.40, 4.0% of
   the Rs 195,558 claimed); the clerk stopped 1. Nine were re-claimed by the same employee,
   six by a colleague. Priya's Hotel Shubham case is one of them: bill HSR/26-27/4873,
   CLM-30533 at Rs 2,352 and CLM-30736 at Rs 2,400, both approved.
2. **The Rs 2,000 line does not separate safe from risky.** All 8 claims that were queried
   or rejected are under Rs 2,000, and so are 13 of the 14 paid repeats (Rs 5,374.40). All
   25 claims of Rs 2,000 or more were approved. Under the line sit 258 of 283 claims (91%)
   and 59% of the rupees.
3. **`status` cannot be used to train or to prove decisions.** 275 of 283 are approved
   (97.2%); "approve everything" scores 97%. Only 43 claims carry a note, in 13 distinct
   wordings and 7 themes.
4. **Repeats leave marks that need no clerk.** 5 of the 7 claims submitted more than 20
   days after the expense are repeats (repeats: median 21 days after the bill date; all
   claims: 5). On 8 of the 15 the rep typed a date that is not the bill date, which is half
   of all 16 date mismatches. On 3 the amount was changed. The unprocessed batch has 10
   late claims, and 3 that match an already-paid claim on employee, vendor, date and amount
   (CLM-31331, CLM-31366, CLM-31402).
5. **Vendor + date + amount is not a safe duplicate key.** 11 pairs agree on all three but
   carry different bill numbers (4 parking, 3 food, 3 fuel, 1 courier). And matching on the
   clerk's exact vendor spelling finds only 10 of the 15 real repeats.
6. **The clerk's typing is not clean ground truth.** 7 of 112 GSTINs fail the checksum,
   each one character away from the GSTIN the same vendor has on other claims. There are
   117 spellings of roughly 70 vendors. One bill number was typed with two amounts (435 and
   335), one bill date has day and month swapped (CLM-30950), and 6 queried claims have no
   clerk fields at all.
7. **Amounts mostly agree, and the exceptions are specific.** 268 of 277 claims are within
   the policy's Rs 10 of the bill (247 exact). Seven over-claim by more, Rs 1,867.80 in all;
   only 3 carry an "amt corrected" note, leaving 4 (Rs 768) approved with no note. Three of
   the seven are by one person (TPD-0388). Two under-claim, one a typo (432 for 4,032).
8. **Parking is as Priya describes, and small.** All 38 parking claims come from Rohan,
   Pooja and Harshal: Rs 30 (34 claims) or Rs 20 (4), Rs 1,100 in total. That is 12 to 14
   claims each across two months, not one a day. One ticket number (110914) was claimed
   twice by the same person and paid twice. Same-day pairs between the three carry
   different ticket numbers wherever the clerk typed one.
9. **Rep-typed fields are messy but all parse.** 473 of 473 amounts (7 formats) and 473 of
   473 dates (3 formats). 122 dates could be read either way; day-first is right for all of
   them and none land after submission. The rep's vendor equals the clerk's string on only
   97 of 277 claims; all but 5 of the rest match once case, punctuation, word order and
   typos are allowed for. 25 `image_file` values carry a phone folder path, 8 end `.jpeg`.
10. **The photo set is the open risk.** 473 claims name 473 different files, 467 photos are
    expected, so at least 6 never arrived (3 train claims were queried "bill not attached").
    No file name is used twice, so re-used photos can only be found by image content. The
    date inside a photo name is the upload date (0 or 1 day before submission), not the
    bill date. Near-duplicate and sideways checks are empty until the photos are imported.

### Decisions

- **`parse_date` reads DD/MM by default.** MM/DD is used only when DD/MM is not a real
  date, or when DD/MM lands after `submitted_on` and MM/DD does not. Replaces "closest to
  and not after submission", which would have picked MM/DD whenever both readings fell
  before submission. On this data the two rules agree on every row; day-first is the safer
  default for Indian bills.
- **Clerk-note themes are rewritten from the 13 real notes**, one theme per note, first
  match wins: duplicate bill, altered bill, amount corrected to bill, no bill photo,
  unreadable photo, GST invoice confirmed, routine ok. Nothing is left unthemed.
- **The duplicate key is bill number + vendor**, with the vendor compared ignoring case,
  punctuation, word order and small typos (similarity >= 0.8). Vendor + date + amount is a
  second, weaker signal for claims whose bill number is not known yet. (Findings 1 and 5.)
- **A duplicate check has to look back across batches**, at least to the already-processed
  claims, and at other employees' claims too. (Findings 1 and 4.)
- **Decisions will come from rules, not from a model fitted to `status`.** (Finding 3.)
  Evidence for the decision logic should be the known-bad claims: the 15 repeats, the 9
  amount mismatches, the 6 with no usable photo and the 1 overwritten bill.
- **Field accuracy will be scored with tolerance**: vendor on similarity, amount within
  Rs 10, date exact, GSTIN exact but only where the clerk's value passes the checksum.
  Exact string match against the clerk would count clerk typos as our errors. (Finding 6.)
- **A GSTIN that fails the checksum is treated as a misread**, to be re-read or sent to
  review rather than output. Every checksum failure in the clerk data is a one-character
  slip. (Finding 6.)
- **Late submission and a rep date that differs from the bill date count as review
  signals**, not as reasons on their own. (Finding 4.)
- **Open question for Nilesh, not decided here:** whether auto-approval under Rs 2,000
  stands as briefed. The data says the line alone would pay 13 of the 14 repeats. (Finding 2.)
- **Photos are not committed yet.** The two on disk total 0.1 MB; the full set gets a size
  check when it is imported.
