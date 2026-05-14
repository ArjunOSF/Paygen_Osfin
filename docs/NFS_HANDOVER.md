# NFS Recon — full handover doc

How NFS works, what files we generate, how they connect, how to verify them,
and what changed from the previous output.

---

## 1. What is NFS?

**NFS** = National Financial Switch — NPCI's domestic ATM network. Connects
banks together so a customer with a card from one bank can withdraw from
another bank's ATM.

Two sides for every NFS transaction:

| Side | Meaning | File NPCI sends to the bank |
|------|---------|----------------------------|
| **Issuer** | IDFC's card used at any bank's ATM | `250ISSuerIDF{DDMMYY}.mIDF` (407 chars) |
| **Acquirer** | Any bank's card used at IDFC's ATM | `250ACQuirerIDF{DDMMYY}.mIDF` (274 chars) |

Both files come daily, packaged together in `NFSRawdataIDF{DDMMYY}.zip`.

---

## 2. The 3-way recon model

For one transaction (IDFC card → SBI ATM → ₹500 withdrawn):

```
   Bank's side          Switch (BASE24)      NPCI (network)
   (CBS + GL)           sees the message     records the
   debits the           and forwards         settlement
   customer's a/c       to NPCI              entry
        │                    │                    │
        └─── all three say "RRN=12345, ₹500, 01-Apr" ───┘
                                ✓ matched
```

If any side's record is **missing**, **declined**, or shows a **different amount**
→ that's an exception, and the bank needs to take action (refund / recovery / etc.).

**Matching keys (all four must agree):**
- **RRN** — 12-digit transaction ID assigned at the ATM
- **ATM ID** — where the transaction happened
- **Date**
- **Amount**

---

## 3. File types we generate

Per NFS-OSG V2.5 Table 6 (page 62) the NPCI deliverables are:

| File | Source | Format | Purpose |
|------|--------|--------|---------|
| `NFSRawdataIDF{DDMMYY}.zip` | NPCI | zip of 2 raw files | Daily transaction list |
| `250ISSuerIDF{DDMMYY}.mIDF` | NPCI | 407-char fixed-width | Issuer-side transaction details |
| `250ACQuirerIDF{DDMMYY}.mIDF` | NPCI | 274-char fixed-width | Acquirer-side transaction details |
| `VerifReversalTransIDF{DDMMYY}.xls` | NPCI | xls, 14 columns | Late reversals (received after cutoff) |
| `NTSLIDF{DDMMYY}.xlsx` | NPCI | xls, multi-block | Daily settlement summary |

To test end-to-end recon, we **also generate the bank-side files** (these aren't
defined by NPCI — they come from the bank's own systems):

| File | Source | Format | Purpose |
|------|--------|--------|---------|
| `tlf_nfs.txt` | Bank Switch (BASE24) | fixed-width 3462-char data, 4 records/line, CRLF | Switch transaction log |
| `cbs_nfs.txt` | Bank Core Banking (Finacle) | pipe-delimited, FH header + 17-field records | Customer account debit/credit log |
| `fss_gl_out_nfs.txt` | Bank GL (FSS) | pipe-delimited, H+D rows | GL posting per transaction |
| `master_table_IDF{DDMMYY}.csv` | tool ground-truth | pipe-delimited CSV | Case + CBS/Switch/NPCI status flags per RRN |

---

## 4. How the files connect (one transaction across all 5 files)

```
RRN = 615631219101    Amount = ₹13,000    Date = 01-04-2026
        │
   ┌────┴────────────────────────────────────────────┐
   │                                                  │
   ▼                                                  ▼
┌─────────────────┐   ┌─────────────────┐    ┌─────────────────┐
│  NPCI side      │   │  Switch side    │    │  Bank side      │
│  (ISSRPIDF)     │   │  (TLF)          │    │  (CBS + FSS GL) │
└─────────────────┘   └─────────────────┘    └─────────────────┘
RRN [10:21]           RRN within 3462-char    CBS field 5
Amount [222:236]      record at [538:550]     FSS REF_7 col
                                              CBS amount field 2
                                              FSS amount field 8
```

The recon engine reads RRN at each file's defined byte position, joins them,
and compares amount + date + ATM ID. The 18-case matrix below tests every
combination of agreement/disagreement.

---

## 5. The 18-case matrix

CBS in {1, NULL} × Switch in {1, 0, NULL} × NPCI in {1, 0, NULL} = 18 cases.
CBS=0 is excluded because NFS auth is real-time — a failed CBS means the
transaction never completed, so no record exists anywhere.

| # | CBS | Switch | NPCI | Action |
|---|-----|--------|------|--------|
| 1 | 1 | 1 | 1 | CLOSED — all matched |
| 2 | 1 | 1 | 0 | Refund to customer — GL → CASA |
| 3 | 1 | 1 | NULL | Refund to customer — GL → CASA |
| 4 | 1 | 0 | 1 | Force match — CLOSED |
| 5 | 1 | 0 | 0 | Refund to customer — GL → CASA |
| 6 | 1 | 0 | NULL | Refund to customer — GL → CASA |
| 7 | 1 | NULL | 1 | Force match — CLOSED |
| 8 | 1 | NULL | 0 | Refund to customer — GL → CASA |
| 9 | 1 | NULL | NULL | Refund to customer — GL → CASA |
| 10 | NULL | 1 | 1 | Recovery from customer — CASA → GL |
| 11 | NULL | 1 | 0 | No action — CLOSED |
| 12 | NULL | 1 | NULL | No action — CLOSED |
| 13 | NULL | 0 | 1 | Recovery from customer — CASA → GL |
| 14 | NULL | 0 | 0 | No action — CLOSED |
| 15 | NULL | 0 | NULL | No action — CLOSED |
| 16 | NULL | NULL | 1 | Recovery from customer — CASA → GL |
| 17 | NULL | NULL | 0 | No action — CLOSED |
| 18 | NULL | NULL | NULL | No action — CLOSED |

---

## 6. How to generate

```bash
cd /Users/user/Recon_generator

# Issuing — IDFC card used at any bank's ATM (407-char file)
python3 paygen_nl.py --prompt "1000 NFS issuing ATM transactions, 1 April 2026"

# Acquiring — IDFC ATM used by any bank's card (274-char file)
python3 paygen_nl.py --prompt "1000 NFS acquiring ATM transactions, 1 April 2026"
```

Each prompt produces one zip in `~/Downloads/` named:
```
paygen_NFS_{ISS|ACQ}_ATM_{count}txn_{YYYYMMDD}_{timestamp}.zip
```

Inside each zip (under `out_20260401/`):
- `250ISSuerIDF010426.mIDF` or `250ACQuirerIDF010426.mIDF` — NPCI raw
- `NFSRawdataIDF010426.zip` — NPCI deliverable wrapper
- `VerifReversalTransIDF010426.xls` — late reversals
- `cbs_nfs.txt`, `tlf_nfs.txt`, `fss_gl_out_nfs.txt`, `ntsl_nfs.xlsx` — bank-side files
- `master_table_IDF010426.csv` — ground truth

---

## 7. How to verify the output

### Step 1 — Record-length check

```bash
unzip -p <zip> 'out_*/250ISSuerIDF*.mIDF' | awk '{print length}' | sort -u
# expect: 407

unzip -p <zip> 'out_*/250ACQuirerIDF*.mIDF' | awk '{print length}' | sort -u
# expect: 274
```

### Step 2 — Field-position decode

```bash
LINE=$(unzip -p <zip> 'out_*/250ISSuerIDF*.mIDF' | head -1)
echo "Participant: ${LINE:0:3}"     # IDF
echo "Txn Type:    ${LINE:3:2}"     # 04 / 05 / 07 / 08
echo "RRN:         ${LINE:9:12}"    # 12 digits
echo "Resp Code:   ${LINE:21:2}"    # 00 (success)
echo "PAN:         ${LINE:23:19}"   # 16-digit PAN + 3 space pad
echo "MCC:         ${LINE:73:4}"    # 6011
echo "ATM ID:      ${LINE:98:8}"    # 8 chars
echo "Network:     ${LINE:157:3}"   # ATM
echo "Txn Amount:  ${LINE:221:15}"  # paise zero-padded
```

### Step 3 — Cross-file RRN match

```bash
# Pick an RRN from ISSRPIDF
RRN=$(unzip -p <zip> 'out_*/250ISSuerIDF*.mIDF' | head -1 | cut -c10-21)

# Confirm it appears in all 4 files
for f in 250ISSuerIDF010426.mIDF tlf_nfs.txt cbs_nfs.txt fss_gl_out_nfs.txt; do
  count=$(unzip -p <zip> "out_*/$f" | grep -c "$RRN")
  echo "$f: $count match(es)"
done
# expect each > 0 for case 1 (all matched) transactions
```

### Step 4 — Amount alignment

For a given RRN:
- ISSRPIDF `[222:236]` → 15-digit paise (`000000001300000` = ₹13,000)
- CBS field 2 → rupees with decimals (`13000.00`)
- FSS GL OUT field 8 → rupees with decimals (`00000000013000.00`)

All three must equal numerically.

### Step 5 — Case distribution

```bash
unzip -p <zip> 'out_*/master_table_IDF*.csv' \
  | awk -F'|' 'NR>1 {print $1}' | sort -n | uniq -c
# expect 18 distinct case numbers, ~55-56 rows each for a 1000-txn input
```

---

## 8. What was before vs what is now

| Aspect | Before | Now |
|--------|--------|-----|
| **NPCI filename** | `issrpidf_nfs.txt` (generic) | `250ISSuerIDF{DDMMYY}.mIDF` / `250ACQuirerIDF{DDMMYY}.mIDF` per NFS-OSG Table 6 |
| **Record length** | always 407 (issuer only) | **407** for issuing, **274** for acquiring — depends on prompt |
| **Acquiring vs Issuing** | NL CLI ignored role keyword | role correctly drives format + filename |
| **Data realism** | all-success records | **18-case matrix** — realistic exception flows |
| **VerifReversal xls** | only if user wrote "late reversal" | always emitted for cases 2/3/5/6/8/9 |
| **Inner NPCI zip** | none | `NFSRawdataIDF{DDMMYY}.zip` per NFS-OSG Table 6 |
| **Cross-file RRN match** | only first RRN aligned (independent generators sharing seed) | all RRNs across ISSRPIDF + TLF + CBS + FSS GL OUT aligned (orchestrator builds shared row list) |
| **Cross-file amount match** | diverged (each generator picked own amounts) | aligned (orchestrator passes one amount per row to every writer) |
| **Field positions** | template-patched | rebuilt from official NPCI Issuer/Acquirer PDFs |
| **Ground truth** | none | `master_table_IDF{DDMMYY}.csv` with case + CBS/Switch/NPCI flags |
| **Distinct cases tested** | 0 (or 1 — only happy path) | 18 |

---

## 9. Spec sources used

| Source | Used for | Status |
|--------|----------|--------|
| `250Issuer Rawdata File Format.pdf` | Issuer 407-char field positions | ✅ byte-verified |
| `250Acquirer Rawdata File Format.pdf` | Acquirer 274-char field positions | ✅ byte-verified |
| `NFS_Operating_and_Settlement_Guidelines_V2.5.pdf` Table 6 (p.62) | File naming convention | ✅ quoted |
| `NFS-OSG` §8.2.5 + Annexure N | VerifReversal report structure | ⚠ Annexure N page is blank in PDF; columns inferred from §8.2.5 + existing verifireversal_generator |
| `NFS-OSG` §8.2.3 + Annexure K | NTSL settlement summary | ✅ used |
| Real sample `__ISSRPIDF.txt` (19,133 records) | Field-position cross-check + realistic distributions (TxnType 91%/8%/0.7%/0.25%, FromAcct 79%/10%/9%/2%, MCC 95%/4%/1%) | ✅ all decode |
| Real sample `CBSMCW.txt` | CBS pipe-delimited 17-field format | ✅ used by `cbs_generator.py` |
| Real sample `FSS GL OUT.txt` (30,233 records) | GL H+D format, account numbers | ✅ used by `fss_gl_out_generator.py` |
| Real sample `TLFX.txt` | TLF 3462-char/data + 3610-char/header + CRLF | ✅ used by `tlf_generator.py` |

Bank-side files (TLF, CBS, FSS GL OUT) are **not** defined by NPCI — they come
from the bank's own switch / Finacle / FSS systems. We generate them to make
the recon test environment complete (so the recon engine has all 3 sides to
join), but their layouts come from real sample files, not NPCI documents.

---

## 10. Repository

- **Local:** `/Users/user/Recon_generator/`
- **Remote:** https://github.com/ArjunOSF/Paygen_Osfin
- **Latest commit:** `11f9170` — NFS orchestrator wiring

## 11. Key source files

| File | Role |
|------|------|
| `paygen_nl.py` | NL CLI entry — parses prompt, routes to generator/orchestrator, packages zip |
| `generators/nfs_orchestrator.py` | NFS one-pass orchestrator — single source of truth for RRN/amount/date across all files |
| `generators/issrpidf_generator.py` | NPCI raw file (Issuer 407 / Acquirer 274) — built from NPCI PDF field positions |
| `generators/tlf_generator.py` | Bank Switch log — 3462-char fixed-width |
| `generators/cbs_generator.py` | Bank Core Banking — pipe-delimited |
| `generators/fss_gl_out_generator.py` | Bank GL — H+D pipe-delimited |
| `generators/ntsl_generator.py` | NPCI settlement summary xlsx |
| `generators/verifireversal_generator.py` | Late-reversal xls |
| `docs/NFS_RECON.md` | Quick-reference card (prompts + verification) |
| `docs/NFS_HANDOVER.md` | This document |
