# NFS Recon — file generation & verification

One-page reference for generating NFS recon test data, the files produced,
and how to verify them.

---

## How to generate

### Through the NL CLI (one prompt → one zip)

```bash
cd /Users/user/Recon_generator

# 1000 Issuing transactions (IDFC's card at any bank's ATM)
python3 paygen_nl.py --prompt "1000 NFS issuing ATM transactions, 1 April 2026"

# 1000 Acquiring transactions (IDFC's ATM, any bank's card)
python3 paygen_nl.py --prompt "1000 NFS acquiring ATM transactions, 1 April 2026"
```

Each prompt produces one zip in `~/Downloads/` named:

```
paygen_NFS_ISS_ATM_<count>txn_<YYYYMMDD>_<timestamp>.zip
paygen_NFS_ACQ_ATM_<count>txn_<YYYYMMDD>_<timestamp>.zip
```

Both flow through `generators/nfs_orchestrator.py`, which builds a single
list of transactions and writes every file from that list — so RRN, amount,
date and ATM ID match across all four legs (NPCI / Switch / CBS / GL).

### Through the orchestrator directly

```bash
python3 generators/nfs_orchestrator.py \
  --num-txns 1000 --role issuer --date 20260401 --seed 42
```

Flags: `--role issuer|acquirer`, `--num-txns`, `--date YYYYMMDD`, `--seed`,
`--participant-id IDF`, `--output-dir <path>`, `--no-zip`.

---

## What each file is

| File (inside zip) | Source side | Format | Spec reference |
|-------------------|-------------|--------|----------------|
| `250ISSuerIDF{DDMMYY}.mIDF` | NPCI Issuer raw | fixed-width, **407 chars/record** | 250Issuer Rawdata File Format PDF |
| `250ACQuirerIDF{DDMMYY}.mIDF` | NPCI Acquirer raw | fixed-width, **274 chars/record** | 250Acquirer Rawdata File Format PDF |
| `NFSRawdataIDF{DDMMYY}.zip` | NPCI daily deliverable | zip containing the two raw files above | NFS-OSG V2.5 Table 6 |
| `VerifReversalTransIDF{DDMMYY}.xls` | NPCI late-reversal report | xls, 14 cols, ~330 rows for 1000-txn input | NFS-OSG V2.5 Table 6 + §8.2.5 / Annexure N |
| `ntsl_nfs.xlsx` | Settlement summary | xls, 8 blocks (Issuer WDL Txn Amt etc.) | NFS-OSG V2.5 Annexure K |
| `tlf_nfs.txt` | Bank's switch (BASE24 log) | 3462-char data lines, 4 records/line, CRLF | bank-internal (TLFX sample) |
| `cbs_nfs.txt` | Bank's Core Banking | pipe-delimited, `FH` header + records | bank-internal (CBSMCW sample) |
| `fss_gl_out_nfs.txt` | Bank's GL | pipe-delimited, `H` + `D` rows | bank-internal |
| `master_table_IDF{DDMMYY}.csv` | Ground truth | case_num + 3 status flags per RRN | tool output |

---

## Expected output volume (per 1000-transaction prompt)

| Item | Count |
|------|-------|
| Case rows in master_table | 1000 (18 cases × ~56 txns/case) |
| NPCI raw records emitted | ~660 (NULL-NPCI cases omit the record per spec) |
| CBS records | ~660 (NULL-CBS cases omit; CBS=Reversed adds CWRR row) |
| TLF data lines | ~168 (4 records packed per line) |
| FSS GL OUT detail rows | matches CBS row count |
| VerifReversal late-reversal rows | ~330 (cases 2/3/5/6/8/9) |

---

## The 18-case matrix

CBS in {1, NULL} × Switch in {1, 0, NULL} × NPCI in {1, 0, NULL} = 18 cases.
CBS=0 is **excluded** — auth is real-time so a failed CBS means the
transaction never completed (no dispensing).

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

## How to verify the output

### 1. Record-length check (NPCI files)

```bash
unzip -p <zip> out_*/250ISSuerIDF*.mIDF | awk '{print length}' | sort -u
# expect: 407

unzip -p <zip> out_*/250ACQuirerIDF*.mIDF | awk '{print length}' | sort -u
# expect: 274
```

### 2. Field-position decode (Issuer file, byte positions 1-indexed)

| Position | Field | Expected for IDFC NFS ATM |
|----------|-------|--------------------------|
| `[1:3]` | Participant ID | `IDF` |
| `[4:5]` | Transaction Type | `04` (WDL) — also `05` MATM, `07`, `08` |
| `[6:7]` | From Account Type | `02` savings (also `00`, `01`, `03`) |
| `[8:9]` | To Account Type | `  ` (blank for single-leg WDL) |
| `[10:21]` | **RRN — MATCHING KEY** | 12 digits |
| `[22:23]` | Response Code | `00` for success; `51`/`91` for decline |
| `[24:42]` | PAN | 19 chars (16-digit PAN + 3 space pad) |
| `[74:77]` | MCC | `6011` (ATM) / `6012` (Micro-ATM) / `6013` (ICCW) |
| `[99:106]` | **ATM Terminal ID — MATCHING KEY** | 8 chars |
| `[158:160]` | Network ID | `ATM` |
| `[222:236]` | **Transaction Amount — MATCHING KEY** | 15-digit paise, zero-padded |
| `[270:284]` | Issuer Settlement Amount | same value as Txn Amount for domestic |

### 3. Cross-file RRN match (one transaction must appear everywhere)

```bash
# Pick first RRN from ISSRPIDF
unzip -p <zip> out_*/250ISSuerIDF*.mIDF | head -1 | cut -c10-21
# returns e.g. 615631219101

# Confirm it appears in all four files
for f in 250ISSuerIDF*.mIDF tlf_nfs.txt cbs_nfs.txt fss_gl_out_nfs.txt; do
  unzip -p <zip> "out_*/$f" | grep -c 615631219101
done
# expect each line > 0
```

### 4. Amount alignment

For RRN that appears in CBS:
- ISSRPIDF `[222:236]` is paise (e.g. `000000001300000` = ₹13,000)
- CBS field 2 is rupees with decimals (e.g. `13000.00`)
- FSS GL OUT field 8 is rupees with decimals
- All three numeric values must equal

### 5. Case distribution in master_table

```bash
unzip -p <zip> out_*/master_table_IDF*.csv \
  | awk -F'|' 'NR>1 {print $1}' | sort -n | uniq -c
# expect 18 distinct case numbers, ~55-56 rows each for 1000-txn input
```

---

## What recon matching actually does

For each RRN, the recon engine compares 4 sides:

```
   NPCI ISSRPIDF [10:21]  ─┐
   Switch TLF [538:550]    ├─► must agree on RRN + amount + date + ATM ID
   Bank CBS field 5        │
   Bank GL REF_7 col       ─┘
```

- All 4 agree → match (case 1: CLOSED)
- One side missing → exception → action per master_table.action column
- Amounts differ → exception → flagged by recon engine

The 18-case matrix is the test for this — 12 cases produce records,
6 cases (where NPCI = NULL) deliberately omit the record so the
recon engine has to flag them as "missing on NPCI side".
