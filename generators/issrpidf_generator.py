"""
issrpidf_generator.py
=====================
NFS Issuer Interchange (NPCI) file — fixed-width.

Per NFS_DATA spec:
  Txn_Type  : 04 (always)
  Resp_Code : 00=Success, 26=Reversal, 71=Deemed approved
  MCC       : 6011=NFS ATM cash withdrawal
              6012=Micro-ATM cash withdrawal
              6013=ICCW (Careless Cash Withdrawal) — dual-RRN

ICCW (MCC 6013) carries TWO RRN values in the record:
  NFS_RRN  at [8:20]   — matches CBS
  UPI_RRN  at tail     — matches Switch (UPI_ICCW)

Record layout (fixed-width, ~120 chars; matches IDFO institution prefix):
  [0:4]    IDFO          institution
  [4:7]    400           txn-type marker
  [7:8]    space
  [8:20]   NFS_RRN       12 digits (primary RRN — joins to CBS)
  [20:22]  resp_code     2 digits
  [22:24]  txn_type      04
  [24:28]  mcc           4 digits
  [28:44]  card          16-digit PAN
  [44:60]  atm_id        16 chars
  [60:68]  txn_date      DDMMYYYY
  [68:74]  txn_time      HHMMSS
  [74:86]  amount        12-digit zero-pad paise
  [86:99]  bank_id       13 chars (zero-pad)
  [99:111] upi_rrn       12 digits (only set for MCC 6013, else spaces)
  [111:120] filler       spaces

Same seed → matching RRNs across CBS, Switch, ISSRPIDF.
"""

from __future__ import annotations
import argparse, csv, json, os, random, sys
from datetime import datetime
from typing import List, Optional


def _luhn_complete(prefix: str = "6", length: int = 16) -> str:
    body = prefix + "".join(str(random.randint(0, 9)) for _ in range(length - 1 - len(prefix)))
    digits = [int(d) for d in body + "0"]
    odd = digits[-1::-2]; even = digits[-2::-2]
    chk = (sum(odd) + sum(sum(divmod(d * 2, 10)) for d in even)) % 10
    return body + str((10 - chk) % 10)


def _normalize_date(s: str) -> str:
    fmts = ["%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"]
    for f in fmts:
        try: return datetime.strptime(s.strip(), f).strftime("%Y%m%d")
        except ValueError: continue
    raise ValueError(f"unrecognised date: {s!r}")


def _ddmmyyyy(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}{yyyymmdd[4:6]}{yyyymmdd[:4]}"


def build_record(nfs_rrn: str, resp: str, mcc: str, card: str, atm_id: str,
                 date_ddmm: str, hhmmss: str, amount_paise: int,
                 bank_id: str, upi_rrn: str = "") -> str:
    parts = [
        "IDFO",                              # [0:4]
        "400",                               # [4:7]
        " ",                                 # [7:8]
        nfs_rrn.zfill(12)[:12],              # [8:20]
        resp.zfill(2)[:2],                   # [20:22]
        "04",                                # [22:24]
        mcc.zfill(4)[:4],                    # [24:28]
        card.ljust(16)[:16],                 # [28:44]
        atm_id.ljust(16)[:16],               # [44:60]
        date_ddmm.ljust(8)[:8],              # [60:68]
        hhmmss.ljust(6)[:6],                 # [68:74]
        f"{amount_paise:012d}",              # [74:86]
        bank_id.ljust(13)[:13],              # [86:99]
        (upi_rrn.zfill(12)[:12] if upi_rrn else " " * 12),  # [99:111]
        " " * 9,                             # [111:120] filler
    ]
    return "".join(parts)


def generate(num_txns: int, business_date: str, mcc: str = "6011",
             seed: Optional[int] = None):
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    random.seed(seed)
    nfs_rrn_start = rng.randint(600_000_000_000, 699_999_999_999)
    upi_rrn_start = rng.randint(700_000_000_000, 799_999_999_999)
    date_ddmm = _ddmmyyyy(business_date)

    rows = []
    lines = []
    for i in range(num_txns):
        nfs_rrn = str(nfs_rrn_start + i).zfill(12)
        upi_rrn = str(upi_rrn_start + i).zfill(12) if mcc == "6013" else ""
        # 90% success, 5% reversal, 5% deemed approved
        r = rng.random()
        resp = "00" if r < 0.90 else ("26" if r < 0.95 else "71")
        card = _luhn_complete("6", 16)
        atm_id = f"NFSAT{rng.randint(0, 99999999):08d}"
        hh = rng.randint(0, 23); mm = rng.randint(0, 59); ss = rng.randint(0, 59)
        time_str = f"{hh:02d}{mm:02d}{ss:02d}"
        amount = rng.randint(50, 5000) * 10000   # multiples of ₹100 in paise
        bank_id = "0000000607028"

        line = build_record(nfs_rrn, resp, mcc, card, atm_id, date_ddmm,
                            time_str, amount, bank_id, upi_rrn)
        lines.append(line)
        rows.append({
            "nfs_rrn": nfs_rrn, "upi_rrn": upi_rrn, "resp": resp,
            "mcc": mcc, "card": card, "atm_id": atm_id,
            "date": date_ddmm, "time": time_str,
            "amount_paise": amount, "bank_id": bank_id,
        })
    return rows, lines


def write_outputs(rows, lines, out_path):
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")

    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nfs_rrn", "upi_rrn", "resp_code", "mcc", "card", "atm_id",
                    "date", "time", "amount_paise", "bank_id"])
        for r in rows:
            w.writerow([r["nfs_rrn"], r["upi_rrn"], r["resp"], r["mcc"],
                        r["card"], r["atm_id"], r["date"], r["time"],
                        r["amount_paise"], r["bank_id"]])

    totals = {
        "num_records": len(rows),
        "total_amount_paise": sum(r["amount_paise"] for r in rows),
        "mcc_breakdown": {r["mcc"]: 0 for r in rows},
        "resp_breakdown": {},
    }
    for r in rows:
        totals["mcc_breakdown"][r["mcc"]] = totals["mcc_breakdown"].get(r["mcc"], 0) + 1
        totals["resp_breakdown"][r["resp"]] = totals["resp_breakdown"].get(r["resp"], 0) + 1
    with open(base + "_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="NFS ISSRPIDF (Issuer Interchange) — NPCI raw file")
    p.add_argument("--num-txns", type=int, default=100)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--mcc", choices=["6011", "6012", "6013"], default="6011",
                   help="6011=NFS ATM, 6012=Micro-ATM, 6013=ICCW (dual-RRN)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--output", default="issrpidf.txt")
    args = p.parse_args(argv)

    bdate = _normalize_date(args.date)
    rows, lines = generate(args.num_txns, bdate, args.mcc, args.seed)
    write_outputs(rows, lines, args.output)

    print(f"  wrote {len(lines)} ISSRPIDF records (MCC {args.mcc}) → {args.output}")
    if args.mcc == "6013":
        print(f"  ICCW dual-RRN: NFS_RRN at [8:20], UPI_RRN at [99:111]")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    print(f"  totals       → {os.path.splitext(args.output)[0]}_totals.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
