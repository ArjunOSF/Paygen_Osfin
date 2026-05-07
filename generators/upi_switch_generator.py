"""
upi_switch_generator.py
=======================
NFS ICCW UPI Switch file — pipe-delimited 19 fields.

File structure:
  Line 1   : SHA1 hash of remaining file content (40 hex chars)
  Line 2   : FHIM{YYYYMMDD}                          (file header)
  Lines 3+ : transaction records (19 pipe-delimited fields)
  Last     : FT{count_9_digits}                       (file trailer)

19 fields per record (0-indexed):
  [0]  txn_ref     bank prefix (IPM for ICCW) + zeros + NFS_RRN concat
  [1]  UPI_RRN     12-digit numeric    — joins ISSRPIDF [365:377]
  [2]  NFS_RRN     12-digit            — joins CBS / ISSRPIDF [9:21]
  [3]  upi_txn_id  18-digit
  [4]  txn_dt      DD/MM/YYYY HH:MM:SS AM/PM
  [5]  mobile_no
  [6]  customer_name
  [7]  vpa         iccwpaysis@india1
  [8]  description INDIA1 PAYMENTS LIMITED
  [9]  amount      12-char zero-padded paise
  [10] account_no
  [11] gl_account  95821102016
  [12] card_ref
  [13] dr_cr       C
  [14] i_d         I
  [15] txn_type    DEBIT / DEBIT-REVERSAL / DEBIT-AUTOREVERSAL
  [16] fr          FR
  [17] flag        Y
  [18] channel     MOB
"""

from __future__ import annotations
import argparse, csv, hashlib, json, os, random, sys
from datetime import datetime
from typing import List, Optional


def _normalize_date(s: str) -> str:
    fmts = ["%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"]
    for f in fmts:
        try: return datetime.strptime(s.strip(), f).strftime("%Y%m%d")
        except ValueError: continue
    raise ValueError(f"unrecognised date: {s!r}")


def _ddmmyyyy(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}/{yyyymmdd[4:6]}/{yyyymmdd[:4]}"


CUSTOMER_NAMES = [
    "Kondasani Vinod Kumar", "Ramesh Sharma", "Anita Desai",
    "Suresh Patel", "Priya Iyer", "Rajesh Khanna", "Meena Pillai",
    "Vikram Rao", "Lakshmi Subramaniam", "Arun Mehta",
]

TXN_TYPES = ["DEBIT", "DEBIT", "DEBIT", "DEBIT", "DEBIT-REVERSAL", "DEBIT-AUTOREVERSAL"]


def _format_record(nfs_rrn: str, upi_rrn: str, business_date: str,
                   amount_paise: int, rng: random.Random) -> str:
    upi_txn_id = "".join(str(rng.randint(0, 9)) for _ in range(18))
    hh = rng.randint(0, 11); mm = rng.randint(0, 59); ss = rng.randint(0, 59)
    ampm = rng.choice(["AM", "PM"])
    txn_dt = f"{_ddmmyyyy(business_date)} {hh:02d}:{mm:02d}:{ss:02d} {ampm}"
    mobile = f"91{rng.randint(7000000000, 9999999999)}"
    cust = rng.choice(CUSTOMER_NAMES)
    account = str(rng.randint(10**9, 10**10 - 1))
    card_ref = str(rng.randint(10**6, 10**7 - 1))
    txn_type = rng.choice(TXN_TYPES)
    txn_ref = f"IPM{'0' * 20}{nfs_rrn}"

    return "|".join([
        txn_ref, upi_rrn, nfs_rrn, upi_txn_id, txn_dt,
        mobile, cust, "iccwpaysis@india1", "INDIA1 PAYMENTS LIMITED",
        f"{amount_paise:012d}", account, "95821102016", card_ref,
        "C", "I", txn_type, "FR", "Y", "MOB",
    ])


def generate(num_txns: int, business_date: str, seed: Optional[int] = None,
             retry_pct: float = 0.10):
    """PROMPT 11 FIX 1: NFS_RRN is NOT unique. ~10% of records share NFS_RRN
    with another row (same customer retry) — different UPI_RRN, different timestamp,
    same amount. Recon match key MUST be NFS_RRN + Date + Amount, never NFS_RRN alone."""
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    # Mirror ISSRPIDF's RNG sequence so RRNs align:
    nfs_rrn_start = rng.randint(600_000_000_000, 699_999_999_999)
    upi_rrn_start = rng.randint(900_000_000_000, 999_999_999_999)

    rows = []
    body_lines = []
    n_retries = int(num_txns * retry_pct)
    retry_indices = set(rng.sample(range(1, num_txns), min(n_retries, num_txns - 1)))

    for i in range(num_txns):
        if i in retry_indices:
            # Retry: reuse the previous row's NFS_RRN + amount, fresh UPI_RRN + bumped time
            prev = rows[-1]
            nfs_rrn = prev["nfs_rrn"]
            upi_rrn = str(upi_rrn_start + i).zfill(12)
            amount  = prev["amount_paise"]
        else:
            nfs_rrn = str(nfs_rrn_start + i).zfill(12)
            upi_rrn = str(upi_rrn_start + i).zfill(12)
            rng.choice(["a"])
            amount = rng.randint(50, 5000) * 10000
        rec = _format_record(nfs_rrn, upi_rrn, business_date, amount, rng)
        body_lines.append(rec)
        rows.append({"nfs_rrn": nfs_rrn, "upi_rrn": upi_rrn,
                     "amount_paise": amount,
                     "is_retry": i in retry_indices})

    header = f"FHIM{business_date}"
    trailer = f"FT{len(body_lines):09d}"
    body_for_hash = "\n".join([header] + body_lines + [trailer]) + "\n"
    sha = hashlib.sha1(body_for_hash.encode("utf-8")).hexdigest()

    all_lines = [sha, header] + body_lines + [trailer]
    return rows, all_lines


def write_outputs(rows, lines, out_path):
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")

    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nfs_rrn", "upi_rrn", "amount_paise"])
        for r in rows:
            w.writerow([r["nfs_rrn"], r["upi_rrn"], r["amount_paise"]])

    # Count duplicate NFS_RRNs (retry transactions)
    rrn_counts = {}
    for r in rows:
        rrn_counts[r["nfs_rrn"]] = rrn_counts.get(r["nfs_rrn"], 0) + 1
    duplicates = sum(1 for c in rrn_counts.values() if c > 1)
    totals = {
        "num_records": len(rows),
        "total_amount_paise": sum(r["amount_paise"] for r in rows),
        "structure": "SHA1 + FHIM + N records + FT",
        "duplicate_nfs_rrns": duplicates,
        "match_key": "NFS_RRN + Date + Amount  (NFS_RRN alone is NOT unique)",
    }
    with open(base + "_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="NFS ICCW UPI Switch file generator")
    p.add_argument("--num-txns", type=int, default=100)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--output", default="upi_switch.txt")
    args = p.parse_args(argv)

    bdate = _normalize_date(args.date)
    rows, lines = generate(args.num_txns, bdate, args.seed)
    write_outputs(rows, lines, args.output)

    print(f"  wrote UPI_SWITCH: 1 SHA + 1 FHIM + {len(rows)} txns + 1 FT → {args.output}")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
