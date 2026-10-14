# Submission notes (answers for the portal form)

- **GitHub repo:** https://github.com/redcode333/Tarang_pharma_destribution
- **Video:** <TO ADD>

## Expected score on predictions.csv

On 196 held-out processed claims, the five fields match the clerk's typing 94.7% of the time and
the photo 98.9% of the time: date 98.5 / 100%, amount 95.4 / 100%, bill number 93.9 / 99.0%,
GSTIN 91.8 / 99.0%, vendor (fuzzy) 93.9 / 96.4%. I expect **95-99% on the fields**: the lower end
if the answer key is clerk-typed, the upper end if it holds the true bill values. Vendor is the
uncertain field: 94-96% with fuzzy or case-insensitive matching, 55-60% if the scorer demands the
exact string, because the same vendor is spelled many ways. Decisions: 147 approve, 43 review. Each
review names its reason, and on train the same rules review all 8 claims the clerk queried or
rejected. Detail: `reports/expected_score.md`.

## AI tools used, and what I threw away

- **Claude Code** wrote the code, ran the data audit, the evaluation and the photo review with me,
  and drafted the reports. I directed each step and checked the results.
- **Google Gemini 3.5 Flash-Lite**, free API tier, reads the receipts inside the product. It costs
  Rs 0.13 a photo at paid rates.
- **Thrown away:**
  - Gemini 3.8 Flash as the primary model: it answered "503 overloaded" on every try for a day.
  - An OCR package (RapidOCR) for detecting rotation: it fixes upside-down lines internally, so
    every rotation scored the same.
  - Vendor + date + amount as the duplicate key: 11 pairs agreed on all three but were different
    bills.
  - A perceptual-hash threshold of 6: every pair at 6 was two different bills on the same template.
  - The first prompt-injection flag: it treated printed footers ("Thank you, visit again") as
    instructions, on 38 photos.
  - Reporting the oil-company brand ("BPCL") as the vendor instead of the dealer.

## How I measured it, and what it gets wrong

Train claims carry the clerk's typed values, so I scored the model against them: the first 80
rows as a development set (prompt changes only from these), the other 203 as holdout. I report
two numbers per field. One is against the clerk as typed. The other is after checking every
mismatch I could against the photo: 53 checked by eye, 15 GSTINs settled automatically. 44 of the
53 were clerk errors.

Where it goes wrong (`reports/error_gallery.md`):
- **GSTINs in small, faint print:** 0 read as 8, or 5 read as S. These are now repaired by the
  checksum.
- **A GSTIN with a digit dropped:** marked unreadable and sent to review.
- **Blurred or shadowed bill numbers:** 2 cases.
- **A handwritten "/-" read as a digit (390/- as 3907):** now corrected from the amount in words.
- **Vendor spelling:** the clerk adds a town ("- Wardha") or drops "Hotel"; the model reports the
  printed header.

Rotated photos (30), stamped receipts (5) and unreadable photos (5) are all detected.

## Key decisions where the brief was unclear (from DECISIONS.md)

1. **Auto-approve only under Rs 2,000 *and* when every check passes.** The amount line alone would
   have paid 13 of the 14 repeat bills the clerk paid.
2. **Duplicates:** same bill number and vendor (fuzzy), or the same photo, against every earlier
   claim. Processed claims are indexed under both the clerk's and the model's bill number, so one
   clerk typo cannot hide a repeat.
3. **Text on a receipt is data, never an instruction.** Instruction-like text sends the claim to
   review.
4. **On an altered bill, `total_amount` is what the bill genuinely says**, and the alteration is
   reported in the reason.
5. **The clerk is not ground truth.** Accuracy is reported against the clerk and against the photo,
   separately.
6. **Tolerances:** amount within Rs 10 (the policy's rounding), bill date within ±1 day of the
   expense date, bills older than 60 days reviewed. The last two need Finance sign-off.
7. **GSTIN repair** by single look-alike swaps that pass the checksum. Anything else is
   "GSTIN unreadable" and goes to review, never a guess.
8. **Free tier for development, paid prices for the cost figure.** The vendor is output in title
   case, which matched the clerk's spelling most often.

## What decision it supports, and the trade-offs

It supports Nilesh's decision on whether to auto-approve small claims. The answer is yes, but only
when the checks pass: 85% of claims under Rs 2,000 do, and those reps are paid in the next run.
That saves about 109 clerk-hours (Rs 23,260 net) a month company-wide. In June-July the checks
caught Rs 16,832 in repeat, altered and over-claimed bills, Rs 15,383 of which the clerks paid.
That is about Rs 1.43 lakh a month at company scale.

Trade-offs:
- About 15% of small claims still wait for a clerk, some only from caution (a date one day off, a
  low-confidence read).
- The bill-date and stale-bill limits are judgement calls.
- The scaled figures assume other divisions look like Nagpur.
- The free tier caps reading at 500 photos a day, so production needs a paid key (about Rs 317 a
  month at 2,400 photos).
