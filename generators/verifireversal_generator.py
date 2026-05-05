"""
verifireversal_generator.py
===========================
NFS Verification Report (VeriFireversal) — late reversals.
Source: real sample (xlsx, 14 columns + 2-row title block).

Sheet structure:
  Row 1: "National Payments Corporation of India"
  Row 2: "Verification Report"
  Row 3: column headers (14 columns)
  Row 4+: detail rows

Columns (14):
  TransType, Resp_Code, Cardno, RRN, StanNo, ACQ, ISS,
  Trasn_Date, Trans_Time, ATMId, SettleDate, RequestAmt,
  Received Amt, Status

Key values:
  TransType   '04 (ATM withdrawal)
  Resp_Code   '28 (late reversal processed)
  Status      "Processed late reversal and reversed originally settled transaction"
  Received Amt 0 (always — net received is zero after reversal)
"""

from __future__ import annotations
import argparse, json, os, random, re, sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

try:
    from openpyxl import Workbook
except ImportError:
    print("error: openpyxl not installed. Run: pip3 install openpyxl", file=sys.stderr)
    sys.exit(2)


COLUMNS = ["TransType", "Resp_Code", "Cardno", "RRN", "StanNo", "ACQ", "ISS",
           "Trasn_Date", "Trans_Time", "ATMId", "SettleDate",
           "RequestAmt", "Received Amt", "Status"]

ACQ_BANKS = ["MIT", "SBI", "IDF", "ICICI", "HDFC", "AXIS"]
ISS_BANKS = ["IDF", "KTB", "UOB", "PNB", "BOB", "CANARA"]


@dataclass
class VeriRow:
    rrn: str
    stan_no: str
    pan: str
    acq: str
    iss: str
    txn_date: str
    txn_time: str
    atm_id: str
    settle_date: str
    request_amt: int


def _normalize_date(s: str) -> str:
    s = s.strip().replace(",", "")
    fmts = ["%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y",
            "%d %B %Y", "%d %b %Y"]
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s)
    for cand in (s, cleaned):
        for f in fmts:
            try: return datetime.strptime(cand, f).strftime("%Y%m%d")
            except ValueError: continue
    raise ValueError(f"unrecognised date: {s!r}")


def _ddmmyyyy(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}-{yyyymmdd[4:6]}-{yyyymmdd[:4]}"


def _luhn_complete(prefix: str, length: int = 16) -> str:
    body = prefix + "".join(str(random.randint(0, 9)) for _ in range(length - 1 - len(prefix)))
    digits = [int(d) for d in body + "0"]
    odd = digits[-1::-2]; even = digits[-2::-2]
    chk = (10 - (sum(odd) + sum(sum(divmod(d * 2, 10)) for d in even)) % 10) % 10
    return body + str(chk)


def _make_row(idx: int, business_date: str, rng: random.Random,
              rrn_start: int, stan_start: int) -> VeriRow:
    pan = _luhn_complete(rng.choice(["4", "5", "6"]), 16)
    return VeriRow(
        rrn=str(rrn_start + idx).zfill(12),
        stan_no=str(stan_start + idx).zfill(8),
        pan=pan,
        acq=rng.choice(ACQ_BANKS),
        iss=rng.choice(ISS_BANKS),
        txn_date=_ddmmyyyy(business_date),
        txn_time=f"{rng.randint(0,23):02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d}",
        atm_id=rng.choice([f"T{rng.randint(1000000, 9999999)}",
                            f"S1BW{rng.randint(1000, 9999)}",
                            f"ER{rng.randint(100000, 999999)}",
                            f"{rng.randint(10000000, 99999999)}"]),
        settle_date=_ddmmyyyy(business_date),
        request_amt=rng.choice([200, 500, 1000, 2000, 3000, 5000, 10000, 20000]),
    )


def _to_excel_row(r: VeriRow) -> List:
    return [
        "'04",                # TransType
        "'28",                # Resp_Code
        f"'{r.pan}",           # Cardno (text-preserving)
        r.rrn,                 # RRN
        r.stan_no,             # StanNo
        r.acq,                 # ACQ
        r.iss,                 # ISS
        r.txn_date,            # Trasn_Date
        r.txn_time,            # Trans_Time
        r.atm_id,              # ATMId
        r.settle_date,         # SettleDate
        r.request_amt,         # RequestAmt
        0,                     # Received Amt
        "Processed late reversal and reversed originally settled transaction",
    ]


def generate(num_txns: int, business_date: str,
             seed: Optional[int] = None) -> Tuple[List[VeriRow], Workbook]:
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    rrn_start  = rng.randint(600_000_000_000, 699_999_999_999)
    stan_start = rng.randint(20_000_000, 99_000_000)

    rows = [_make_row(i, business_date, rng, rrn_start, stan_start)
            for i in range(num_txns)]

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["National Payments Corporation of India"])
    ws.append(["Verification Report"])
    ws.append(COLUMNS)
    for r in rows:
        ws.append(_to_excel_row(r))

    return rows, wb


def write_outputs(rows: List[VeriRow], wb: Workbook, out_path: str,
                  business_date: str) -> None:
    wb.save(out_path)
    base, _ = os.path.splitext(out_path)
    totals = {
        "business_date": business_date,
        "num_records": len(rows),
        "total_request_amount": sum(r.request_amt for r in rows),
        "received_amount": 0,
        "all_status": "Processed late reversal and reversed originally settled transaction",
    }
    with open(base + "_expected_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="NFS VeriFireversal report generator (.xlsx)")
    p.add_argument("--num-txns", type=int, default=20)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--random", action="store_true")
    p.add_argument("--output", default="verifireversal.xlsx")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    rows, wb = generate(args.num_txns, bdate, args.seed)
    write_outputs(rows, wb, args.output, bdate)
    print(f"  wrote {len(rows)} rows × 14 cols → {args.output}")
    print(f"  totals → {os.path.splitext(args.output)[0]}_expected_totals.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
