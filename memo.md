**To:** Nilesh Agrawal, Finance Controller · **Re:** Reading expense receipts automatically · **Date:** 15 October 2026

## Recommendation

Read every bill automatically. Auto-approve a claim under Rs 2,000 only when all checks pass: 85% of small claims did (220 of 258, June–July). Send everything else to a clerk with the reason already written. Do not auto-approve on amount alone.

## Accuracy

On 196 processed claims kept aside for testing, the system matched the clerk on 94.7% of fields and the photo on 98.9%. Most disagreements were clerk typing: of 53 checked by hand, 44 were clerk errors (dropped digits, wrong dates, blank GSTINs, inked amounts typed as genuine).

| Field | vs clerk | vs photo |
|---|---|---|
| Bill date | 98.5% | 100% |
| Amount | 95.4% | 100% |
| Bill number | 93.9% | 99.0% |
| GSTIN | 91.8% | 99.0% |
| Vendor | 93.9% | 96.4% |

Against the photo, all five fields clear your 95%; against the clerk's typing, date and amount do.

## What it saves

A clerk costs Rs 216 an hour (Rs 76,000 for 352 hours). The 78% of claims approved automatically save 3.5 minutes each. The model costs Rs 0.13 a photo.

| | Claims/month | Clerk time saved | Model cost | Net |
|---|---|---|---|---|
| Company | 2,400 | 109 h, Rs 23,577 | Rs 317 | **Rs 23,260** |
| Nagpur | 240 | 11 h, Rs 2,358 | Rs 32 | **Rs 2,326** |

## What it recovers

In Nagpur's claims of 9 June – 18 July (283 claims), the checks found repeat bills, altered amounts and over-claims:

- **Under Rs 2,000:** Rs 16,832 on 29 claims. Your plain "auto-approve under Rs 2,000" rule would pay all of it with no human look. The clerks paid Rs 15,383 of it.
- **Rs 2,000 or more:** Rs 2,900 on 2 claims that the clerks saw and approved anyway.

Approved wrongly in all: Rs 18,283. 283 claims is 1.18 months of Nagpur's volume, so that is about **Rs 15,505 a month in Nagpur, Rs 1.55 lakh company-wide**. This is an estimate from about six weeks of one division.

## The fake "PRE-VERIFIED" stamp

Five receipts carry a stamp telling "automated processing" to approve the bill and skip the duplicate check. Every one is an altered or repeated bill. A tool that obeys text on the page would have paid them. Ours treats receipt text as evidence, never instructions, and flags the stamp itself.

## Faster pay for reps

For Amol: clean claims go straight into the weekly payment run, with no wait for a clerk. Only flagged claims queue.

## What I need from you

1. Sign-off on two limits: a bill may differ from the expense date by ±1 day, and may be at most 60 days old.
2. Permission to query the repeat claims found.
3. App fixes from IT: auto-rotate photos, retry failed uploads, save bare file names.
4. A vendor master with GSTINs.
5. An owner for the review queue.
6. A paid API key for production: the free tier stops at 500 photos a day.
