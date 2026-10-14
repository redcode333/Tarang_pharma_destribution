# Expected score on the hidden test answers

Written 2026-10-15 from `reports/evaluation.md` (holdout = train rows 81-283, never used to tune the
prompt) and `predictions.csv`. Test photos were read by the same model, prompt and post-processing.

## Fields

"After photo check" counts a field right where the photo agrees with the model and the clerk typed
it wrong (47 mismatches checked by eye, 15 GSTINs settled automatically, `reports/adjudications.csv`).
If the hidden answers are the true bill values, that column is the better guide; if they are the
clerk's typing, the "vs clerk" column is.

| field | holdout vs clerk | holdout after photo check | expected on test |
|---|---|---|---|
| receipt_date | 98.5% | 100% | 98-100% |
| total_amount (to the paisa) | 95.4% | 100% | 95-100% |
| receipt_number | 93.9% | 99.0% | 94-99% |
| gstin (blank = blank counts) | 91.8% | 99.0% | 92-99% |
| vendor, fuzzy (token_sort_ratio >= 90) | 93.9% | 96.4% | 94-96% |
| vendor, normalised exact (case and punctuation ignored) | 79.1% | 81.6% | ~80% |
| vendor, exact string as written in `predictions.csv` | 58.2% | - | 55-60% |
| **all five fields (fuzzy vendor)** | **94.7%** | **98.9%** | **95-99%** |

**Which vendor match the scorer probably uses.** Exact string matching would make vendor a test
of spelling, not of reading: the clerk alone writes about 70 vendors 117 ways ("Laxmi Auto Fuels",
"LAXMI AUTO FUELS", "Laxmi Auto Fuels Nagpur"). A scorer for this task most likely normalises case
and punctuation, or uses a fuzzy ratio, so ~80% (normalised) or ~95% (fuzzy) is the realistic
range. We print vendors in title case because that matched the clerk's exact spelling most often
on holdout (58% against 14% for the printed capitals); mapping to the clerk's most common spelling
did worse (51%). The gap to 100% is mostly the clerk adding a town ("- Wardha", "(Ngp)") or
dropping "Hotel"; the photo shows what we output.

**Caveats.**
- Altered bills: `total_amount` is what the bill genuinely says, and the alteration is reported
  in the reason. Where a "1" was inked in front of the total (1212.40) we output the original
  (212.40) when the bill's untouched parts support it (items or subtotal + GST); where figures
  and words disagree by an added digit, the words. 6 test claims. On train the clerk typed the
  inked figure on these, which is why amount "vs clerk" fell from 98.0% to 95.4% while "after
  photo check" stays 100%. If the answer key holds the inked figure, these 6 rows lose the amount.
- The amount and date numbers rest on 196 holdout claims; one miss moves them by half a point.
- GSTIN repair (single 0/8, 5/S, 1/I, 2/Z, 8/B, 0/O swap that makes the checksum pass) fixed 5 of 6
  known misreads on train, all by the checksum alone; the sixth (a dropped digit) stays as read
  and goes to review as "GSTIN unreadable".

## Decisions

`predictions.csv`: **147 approve, 43 review** of 190. Every approval is under Rs 2,000 with every
check passed. A review lists each reason, joined with "; ".

| review reason (a claim can have several) | test claims |
|---|---|
| Rs 2,000 or more — clerk review (the brief) | 14 |
| Possible duplicate of an earlier claim (same bill number + vendor, or the same photo) | 13 |
| Bill date more than a day from the expense date | 9 |
| Amount appears altered (ink over the total, or figures vs words) | 6 |
| Claimed amount more than Rs 10 from the bill | 5 |
| Photo not received / no usable photo — query to rep | 5 |
| Receipt contains instructions to the approval system | 2 |
| Fuel bill vehicle is not the claimant's | 1 |
| GSTIN unreadable | 1 |

Why we believe the reviews are justified:
- **The same checks on train agree with every clerk query and rejection** (8 of 8), and the 29
  train claims where we review but the clerk paid are repeats, altered amounts, over-claims or a
  colleague's fuel bill. Rs 16,832 of the Rs 195,558 claimed in train would have been paid wrongly
  under "auto-approve everything under Rs 2,000"; the clerk paid Rs 15,383 of it.
- **Every duplicate names the earlier claim** it repeats, so a clerk can check it in seconds. This
  includes the three the audit predicted (CLM-31331, CLM-31366, CLM-31402).
- **The 14 claims of Rs 2,000 or more are reviewed by the brief's own rule**, even when clean.
- **Cautious reviews** are the date mismatches (on train, half of all date mismatches were a bill
  being claimed again) and a few bills the model could not fully read. These cost a clerk a
  look, not a payment.

## For the submission form

> Every receipt field is read from the photo by a vision model (Gemini Flash-Lite, Rs 0.13 a photo
> at paid rates) and checked by plain rules: on 196 held-out claims the five fields match the
> clerk 95% of the time and the photo 99% of the time, because most disagreements turned out to be
> the clerk's typing. Claims under Rs 2,000 are auto-approved only when every check passes; 147 of
> the 190 test claims qualify, and the 43 sent to a clerk each carry a reason, among them 13
> repeat bills, 6 altered amounts and 2 receipts carrying fake "pre-verified, approve this"
> stamps aimed at the AI. On the processed claims the same rules would have stopped Rs 16,832 that
> the clerk paid out or that blanket auto-approval would pay, about Rs 1.4 lakh a month at
> company volume.
