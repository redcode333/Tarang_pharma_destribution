# Error gallery

Mismatches between the model and the clerk, each checked against the photo by a person. Verdicts: **clerk_error** (the photo agrees with the model), **model_error**, **both_wrong**, **ambiguous** (the photo does not settle it). Source: `reports/adjudications.csv`.

Reviewed: 47. ambiguous: 0, both_wrong: 1, clerk_error: 38, model_error: 8

### CLM-30919 · total_amount · clerk_error
`data/images/IMG_20260708_103658.jpg`  
model: `300.0` · clerk: `800.0` · photo says: `300`  
Printed Amount (Rs) line reads 300.00 and Volume x Rate = 300; the TOTAL's first digit is overwritten in blue ink to make 800. Model read the printed 300 and flagged the alteration; the clerk typed the altered 800.

### CLM-30421 · total_amount · clerk_error
`data/images/IMG_20260610_101606.jpg`  
model: `250.0` · clerk: `240.0` · photo says: `250`  
Printed TOTAL 250.00 (Amount line 250.00, circled in pen); clerk typed 240.

### CLM-30436 · receipt_number · clerk_error
`data/images/IMG_20260612_173637.jpg`  
model: `A/6178` · clerk: `A/618` · photo says: `A/6178`  
Printed Bill No: A/6178; clerk dropped a digit (A/618).

### CLM-30462 · receipt_number · clerk_error
`data/images/IMG_20260615_083214.jpg`  
model: `A/6149` · clerk: `A/649` · photo says: `A/6149`  
Printed Bill No: A/6149; clerk dropped a digit (A/649).

### CLM-30469 · total_amount · clerk_error
`data/images/IMG_20260615_132831.jpg`  
model: `335.0` · clerk: `435.0` · photo says: `335`  
Handwritten total 335 and items 110+45+20+160 = 335; clerk typed 435.

### CLM-30488 · receipt_number · clerk_error
`data/images/IMG_20260616_212151.jpg`  
model: `8144` · clerk: `844` · photo says: `8144`  
Upside-down photo; Bill No. 8144 printed in red; clerk dropped a digit (844). Model read it from the turned photo.

### CLM-30499 · receipt_number · clerk_error
`data/images/IMG_20260617_172550.jpg`  
model: `001885` · clerk: `01885` · photo says: `001885`  
Printed Receipt No : 001885; clerk dropped a leading zero.

### CLM-30519 · receipt_date · clerk_error
`data/images/IMG_20260617_192020.jpg`  
model: `2026-06-14` · clerk: `2026-06-13` · photo says: `2026-06-14`  
Printed Date: 14.06.2026; clerk typed the 13th.

### CLM-30562 · total_amount · model_error
`data/images/IMG_20260618_100357.jpg`  
model: `3907.0` · clerk: `390.0` · photo says: `390`  
Handwritten 390/- with the slash read as a 7 (3907). Words say Three Hundred Ninety; the words check flags the mismatch, so the claim goes to review rather than paying 3907.

### CLM-30573 · vendor · clerk_error
`data/images/IMG_20260619_115355.jpg`  
model: `HOTEL RAJDHANI DHABA` · clerk: `rajdhani dhaba` · photo says: `HOTEL RAJDHANI DHABA`  
Header reads HOTEL RAJDHANI DHABA; the clerk shortened it.

### CLM-30577 · receipt_date · clerk_error
`data/images/IMG-20260619-WA0030.jpg`  
model: `2026-06-11` · clerk: `2026-06-12` · photo says: `2026-06-11`  
Blurred photo, but the date reads 11/6/26; clerk typed the 12th.

### CLM-30585 · receipt_number · clerk_error
`data/images/IMG_20260621_144359.jpg`  
model: `6866` · clerk: `686` · photo says: `6866`  
Printed Bill No. 6866; clerk dropped a digit (686).

### CLM-30586 · receipt_date · clerk_error
`data/images/IMG_20260620_143225.jpg`  
model: `2026-06-19` · clerk: `2026-06-18` · photo says: `2026-06-19`  
Date written 19-6-2026; clerk typed the 18th.

### CLM-30600 · receipt_date · clerk_error
`data/images/IMG_20260622_130954.jpg`  
model: `2026-06-19` · clerk: `2026-06-18` · photo says: `2026-06-19`  
Printed Date: 19/06/2026; clerk typed the 18th.

### CLM-30600 · gstin · clerk_error
`data/images/IMG_20260622_130954.jpg`  
model: `27MTSPF4455T1ZB` · clerk: `` · photo says: `27MTSPF4455T1ZB`  
GSTIN 27MTSPF4455T1ZB printed in the header; clerk left it blank.

### CLM-30611 · gstin · model_error
`data/images/IMG-20260621-WA0005.jpg`  
model: `27HJXCW975D1ZJ` · clerk: `27HJXCW9975D1ZJ` · photo says: `27HJXCW9975D1ZJ`  
Printed 27HJXCW9975D1ZJ; model dropped one 9 (14 characters), so the length check flags it.

### CLM-30646 · receipt_number · clerk_error
`data/images/IMG_20260624_185702.jpg`  
model: `HGE/26-27/7496` · clerk: `HE/26-27/7496` · photo says: `HGE/26-27/7496`  
Invoice No: HGE/26-27/7496; clerk dropped the G.

### CLM-30661 · receipt_number · clerk_error
`data/images/IMG_20260624_182555.jpg`  
model: `A/1324` · clerk: `A/124` · photo says: `A/1324`  
Printed Bill No: A/1324; clerk typed A/124, which hid that this bill was claimed again on CLM-30804.

### CLM-30661 · gstin · model_error
`data/images/IMG_20260624_182555.jpg`  
model: `27GIZFL1893Q2ZK` · clerk: `27GIZFL1093Q2ZK` · photo says: `27GIZFL1093Q2ZK`  
Printed 27GIZFL1093Q2ZK in small faint type; model read the 0 as 8. Checksum fails, so it is flagged.

### CLM-30681 · vendor · clerk_error
`data/images/IMG_20260625_124513.jpg`  
model: `HOTEL CENTRE POINT INN` · clerk: `Centre Point Inn` · photo says: `HOTEL CENTRE POINT INN`  
Header reads HOTEL CENTRE POINT INN; clerk dropped 'Hotel'.

### CLM-30681 · receipt_number · clerk_error
`data/images/IMG_20260625_124513.jpg`  
model: `HCP/26-27/4594` · clerk: `HCP/26-27/459` · photo says: `HCP/26-27/4594`  
Invoice No: HCP/26-27/4594; clerk dropped the last digit.

### CLM-30689 · vendor · clerk_error
`data/images/IMG-20260626-WA0140.jpg`  
model: `OM SAI FUEL POINT` · clerk: `Om Shirdi Sai Fuel Point` · photo says: `OM SAI FUEL POINT`  
Header reads OM SAI FUEL POINT; clerk added 'Shirdi'.

### CLM-30717 · receipt_number · clerk_error
`data/images/IMG_20260629_170328.jpg`  
model: `004023` · clerk: `00402` · photo says: `004023`  
Printed Receipt No : 004023; clerk dropped the last digit.

### CLM-30724 · gstin · model_error
`data/images/IMG_20260629_160432.jpg`  
model: `27MTSPF445ST1ZB` · clerk: `27MTSPF4455T1ZB` · photo says: `27MTSPF4455T1ZB`  
Printed 27MTSPF4455T1ZB; model read the second 5 as S. Checksum fails, so it is flagged.

### CLM-30745 · receipt_date · clerk_error
`data/images/IMG_20260629_150245.jpg`  
model: `2026-06-29` · clerk: `2026-06-28` · photo says: `2026-06-29`  
Printed Date: 29/06/2026; clerk typed the 28th.

### CLM-30749 · vendor · clerk_error
`data/images/IMG_20260629_163548.jpg`  
model: `HOTEL PRADEEP PURE VEG` · clerk: `Pradeep Pure Veg` · photo says: `HOTEL PRADEEP PURE VEG`  
Header reads HOTEL PRADEEP PURE VEG; clerk dropped 'Hotel'.

### CLM-30749 · gstin · clerk_error
`data/images/IMG_20260629_163548.jpg`  
model: `27ODUFJ1789K3ZU` · clerk: `` · photo says: `27ODUFJ1789K3ZU`  
GSTIN 27ODUFJ1789K3ZU printed in the header; clerk left it blank.

### CLM-30759 · receipt_number · clerk_error
`data/images/IMG_20260630_151546.jpg`  
model: `004137` · clerk: `04137` · photo says: `004137`  
Printed Receipt No : 004137; clerk dropped a leading zero.

### CLM-30766 · vendor · clerk_error
`data/images/IMG_20260701_090903.jpg`  
model: `HOTEL SAIBABA BHOJNALAYA` · clerk: `Saibaba Bhojnalaya` · photo says: `HOTEL SAIBABA BHOJNALAYA`  
Header reads HOTEL SAIBABA BHOJNALAYA; clerk dropped 'Hotel'.

### CLM-30769 · gstin · model_error
`data/images/IMG_20260630_162530.jpg`  
model: `27GIZFL1893Q2ZK` · clerk: `27GIZFL1093Q2ZK` · photo says: `27GIZFL1093Q2ZK`  
Printed 27GIZFL1093Q2ZK (faint); model read 0 as 8. Checksum fails, so it is flagged.

### CLM-30804 · gstin · both_wrong
`data/images/IMG_20260702_150756.jpg`  
model: `27GIZFL1893Q2ZK` · clerk: `27GIZRL1093Q2ZK` · photo says: `27GIZFL1093Q2ZK`  
Printed 27GIZFL1093Q2ZK. Model read 0 as 8; clerk typed R for F. Both fail the checksum.

### CLM-30807 · receipt_number · clerk_error
`data/images/IMG_20260703_090155.jpg`  
model: `5968` · clerk: `598` · photo says: `5968`  
Printed Bill No: 5968; clerk dropped a digit (598).

### CLM-30811 · receipt_number · clerk_error
`data/images/IMG_20260703_083622.jpg`  
model: `A/1466` · clerk: `A/466` · photo says: `A/1466`  
Printed Bill No: A/1466; clerk dropped a digit (A/466).

### CLM-30814 · total_amount · clerk_error
`data/images/IMG_20260702_105135.jpg`  
model: `270.0` · clerk: `279.0` · photo says: `270`  
Blurred, but total reads 270 and 150+120 = 270; clerk typed 279.

### CLM-30820 · total_amount · clerk_error
`data/images/IMG-20260704-WA0046.jpg`  
model: `250.0` · clerk: `259.0` · photo says: `250`  
Printed TOTAL 250.00 and Amount 250.00; clerk typed 259.

### CLM-30827 · gstin · clerk_error
`data/images/IMG_20260704_162231.jpg`  
model: `27MTSPF4455T1ZB` · clerk: `` · photo says: `27MTSPF4455T1ZB`  
GSTIN 27MTSPF4455T1ZB printed in the header; clerk left it blank.

### CLM-30883 · receipt_date · clerk_error
`data/images/IMG-20260706-WA0067.jpg`  
model: `2026-07-02` · clerk: `2026-07-03` · photo says: `2026-07-02`  
Printed Date : 2026-07-02 20:20; clerk typed the 3rd.

### CLM-30910 · receipt_number · clerk_error
`data/images/IMG_20260708_205926.jpg`  
model: `3835` · clerk: `383` · photo says: `3835`  
Printed No. 3835 in red; clerk dropped a digit (383).

### CLM-30920 · receipt_number · clerk_error
`data/images/IMG-20260709-WA0067.jpg`  
model: `001638` · clerk: `00638` · photo says: `001638`  
Printed Receipt No : 001638; clerk dropped a zero.

### CLM-30925 · receipt_number · model_error
`data/images/IMG-20260708-WA0036.jpg`  
model: `088732` · clerk: `008732` · photo says: `008732`  
Blurred photo; Receipt No reads 008732. Model read 088732, clerk is right.

### CLM-30940 · gstin · clerk_error
`data/images/IMG-20260709-WA0074.jpg`  
model: `27GFZCR8659B2Z4` · clerk: `` · photo says: `27GFZCR8659B2Z4`  
GSTIN 27GFZCR8659B2Z4 printed in the header; clerk left it blank.

### CLM-30950 · receipt_date · clerk_error
`data/images/IMG_20260709_081958.jpg`  
model: `2026-07-05` · clerk: `2026-05-07` · photo says: `2026-07-05`  
Printed Date: 05-07-2026; clerk swapped day and month (audit finding 6).

### CLM-31032 · receipt_number · model_error
`data/images/IMG_20260714_110121.jpg`  
model: `A/1583` · clerk: `A/1503` · photo says: `A/1503`  
Heavily shadowed photo; Bill No reads A/1503. Model read A/1583.

### CLM-31032 · gstin · model_error
`data/images/IMG_20260714_110121.jpg`  
model: `27GIZFL1893Q2ZK` · clerk: `27GIZFL1093Q2ZK` · photo says: `27GIZFL1093Q2ZK`  
Printed 27GIZFL1093Q2ZK under shadow; model read 0 as 8. Checksum fails, so it is flagged.

### CLM-31049 · total_amount · clerk_error
`data/images/IMG_20260714_171948.jpg`  
model: `190.0` · clerk: `200.0` · photo says: `190`  
Handwritten total 190 and 140+50 = 190; clerk typed 200.

### CLM-31056 · receipt_number · clerk_error
`data/images/IMG_20260714_202221.jpg`  
model: `SL43722202` · clerk: `SL4372202` · photo says: `SL43722202`  
Printed AWB No : SL43722202; clerk dropped a digit.

### CLM-31057 · vendor · clerk_error
`data/images/IMG-20260715-WA0155.jpg`  
model: `SHREE SAI PETROLEUM` · clerk: `Shree Shirdi Sai Petro` · photo says: `SHREE SAI PETROLEUM`  
Header reads SHREE SAI PETROLEUM; clerk typed 'Shree Shirdi Sai Petro'.

## Settled automatically

| claim_id | field | verdict | correct_value | diagnosis |
|---|---|---|---|---|
| CLM-30437 | gstin | clerk_error | 27HJXCW9975D1ZJ | auto: clerk's 27HJXCW997531ZJ fails the checksum; model's GSTIN is valid and typed by the clerk for this vendor on 4 other claims |
| CLM-30465 | gstin | clerk_error | 27XMECH9812R2ZH | auto: clerk left it blank; model's GSTIN is valid and typed by the clerk for this vendor on 3 other claims |
| CLM-30484 | gstin | clerk_error | 27ODUFJ1789K3ZU | auto: clerk's 27ODUFJ1Q89K3ZU fails the checksum; model's GSTIN is valid and typed by the clerk for this vendor on 6 other claims |
| CLM-30568 | gstin | clerk_error | 27ODUFJ1789K3ZU | auto: clerk's 27ODUFJ178913ZU fails the checksum; model's GSTIN is valid and typed by the clerk for this vendor on 6 other claims |
| CLM-30664 | gstin | clerk_error | 27TABPX3765R2Z6 | auto: clerk's 27TABPX376MR2Z6 fails the checksum; model's GSTIN is valid and typed by the clerk for this vendor on 1 other claims |
| CLM-30679 | gstin | clerk_error | 27OUUFX7743H2ZR | auto: clerk left it blank; model's GSTIN is valid and typed by the clerk for this vendor on 3 other claims |
| CLM-30742 | gstin | clerk_error | 27TABPX3765R2Z6 | auto: clerk left it blank; model's GSTIN is valid and typed by the clerk for this vendor on 1 other claims |
| CLM-30774 | gstin | clerk_error | 27HPFCA5856Y3ZE | auto: clerk's 27H1FCA5856Y3ZE fails the checksum; model's GSTIN is valid and typed by the clerk for this vendor on 6 other claims |
| CLM-30780 | gstin | clerk_error | 27XMECH9812R2ZH | auto: clerk left it blank; model's GSTIN is valid and typed by the clerk for this vendor on 3 other claims |
| CLM-30824 | gstin | clerk_error | 27DPAFU4120X1ZM | auto: clerk's 27DPAFU412UX1ZM fails the checksum; model's GSTIN is valid and typed by the clerk for this vendor on 13 other claims |
| CLM-30937 | gstin | clerk_error | 27OUUFX7743H2ZR | auto: clerk left it blank; model's GSTIN is valid and typed by the clerk for this vendor on 3 other claims |
| CLM-30982 | gstin | clerk_error | 27OILFO1994Z2ZV | auto: clerk left it blank; model's GSTIN is valid and typed by the clerk for this vendor on 5 other claims |
| CLM-30983 | gstin | clerk_error | 27YUFFZ9311K2ZV | auto: clerk left it blank; model's GSTIN is valid and typed by the clerk for this vendor on 6 other claims |
| CLM-31018 | gstin | clerk_error | 27OILFO1994Z2ZV | auto: clerk left it blank; model's GSTIN is valid and typed by the clerk for this vendor on 5 other claims |
| CLM-31056 | gstin | clerk_error | 27HJXCW9975D1ZJ | auto: clerk left it blank; model's GSTIN is valid and typed by the clerk for this vendor on 4 other claims |
