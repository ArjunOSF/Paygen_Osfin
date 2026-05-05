"""
nfs_adjustment_generator.py
===========================
NFS Adjustment Report (Excel .xlsx) — dispute adjustments + chargebacks.
Source: real sample with 44 columns.

Output: single sheet "Sheet1" with header row + N detail rows.

Column inventory (44):
   1 id                       23 AdjRef
   2 Adjdate                  24 BankAdjRef
   3 Adjtype                  25 AdjProof
   4 ACQ                      26 ReasonDesc
   5 ISR                      27 Pincode
   6 Response                 28 ATMLocation
   7 TxnDate                  29 MultiDisputeGroup
   8 TxnTime                  30 FCQM
   9 RRN                      31 AdjSettlementDate
  10 ATMID                    32 CustomerPenalty
  11 CardNo                   33 AdjTime
  12 ChbDate                  34 Cycle
  13 ChbRef                   35 TATExpiryDate
  14 TxnAmount                36 AcqSTLAmount
  15 AdjAmount                37 AcqCC
  16 ACQFee                   38 PanEntryMode
  17 ISSFee                   39 ServiceCode
  18 ISSFeeSW                 40 CardDataInputCapability
  19 NpciFee                  41 MCCCode
  20 AcqFeeTax                42 ComplaintNumber
  21 IssFeeTax                43 ComplaintClosedReason
  22 NpciTAX                  44 Remark

Adjtype values: Credit Adj / Chargeback
ReasonDesc:     Found Cash / Account debit without dispensing
Cycle:          2C
MCCCode:        6011 (ATM)
"""

from __future__ import annotations
import argparse, json, os, random, re, sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

try:
    import openpyxl
    from openpyxl import Workbook
except ImportError:
    print("error: openpyxl not installed. Run: pip3 install openpyxl", file=sys.stderr)
    sys.exit(2)


COLUMNS = [
    "id", "Adjdate", "Adjtype", "ACQ", "ISR", "Response", "TxnDate", "TxnTime",
    "RRN", "ATMID", "CardNo", "ChbDate", "ChbRef", "TxnAmount", "AdjAmount",
    "ACQFee", "ISSFee", "ISSFeeSW", "NpciFee", "AcqFeeTax", "IssFeeTax", "NpciTAX",
    "AdjRef", "BankAdjRef", "AdjProof", "ReasonDesc", "Pincode", "ATMLocation",
    "MultiDisputeGroup", "FCQM", "AdjSettlementDate", "CustomerPenalty",
    "AdjTime", "Cycle", "TATExpiryDate", "AcqSTLAmount", "AcqCC", "PanEntryMode",
    "ServiceCode", "CardDataInputCapability", "MCCCode", "ComplaintNumber",
    "ComplaintClosedReason", "Remark",
]


@dataclass
class AdjTxn:
    id: str
    adj_type: str          # "Credit Adj" or "Chargeback"
    txn_date: str          # DD-MM-YYYY
    txn_time: str          # HH:MM:SS
    rrn: str               # 12-digit
    atm_id: str
    card_no: str           # masked PAN
    chb_date: str
    chb_ref: str
    txn_amount: float
    adj_amount: float
    bank_adj_ref: str
    reason_desc: str
    atm_location: str
    pincode: str
    adj_settlement_date: str


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


def _make_txn(idx: int, business_date: str, rng: random.Random,
              rrn_start: int, id_start: int) -> AdjTxn:
    adj_id = str(id_start + idx).zfill(5)
    # Bias toward Credit Adj (~70%) vs Chargeback (~30%) per real-file pattern
    adj_type = rng.choices(["Credit Adj", "Chargeback"], weights=[70, 30], k=1)[0]
    reason   = rng.choice(["Found Cash", "Account debit without dispensing"])

    txn_yyyymmdd = (datetime.strptime(business_date, "%Y%m%d") - timedelta(days=4)
                    ).strftime("%Y%m%d")
    chb_yyyymmdd = (datetime.strptime(business_date, "%Y%m%d") - timedelta(days=2)
                    ).strftime("%Y%m%d")

    rrn = str(rrn_start + idx).zfill(12)
    hh = rng.randint(0, 23); mm = rng.randint(0, 59); ss = rng.randint(0, 59)
    txn_time = f"{hh:02d}:{mm:02d}:{ss:02d}"

    pan = "5" + "".join(str(rng.randint(0, 9)) for _ in range(5))
    pan_masked = f"'{pan}{'*'*7}{rng.randint(100, 999)}"

    amount = rng.choice([200.0, 500.0, 1000.0, 2200.0, 3500.0, 5000.0, 10000.0])
    chb_ref = f"HPS/IDF/E{rng.randint(10000, 99999)}"

    if adj_type == "Chargeback":
        bank_adj_ref = f"CRM-{rng.randint(1000, 9999)}{rng.randint(0, 9)}"
    else:
        bank_adj_ref = "Proactive"

    locations = [("'NELLORE", "524004"), ("'14 NEW B", "635207"),
                 ("'BANGALORE", "560001"), ("'CHENNAI", "600001"),
                 ("'MUMBAI", "400001"), ("'DELHI", "110001")]
    loc, pin = rng.choice(locations)

    return AdjTxn(
        id=adj_id, adj_type=adj_type,
        txn_date=_ddmmyyyy(txn_yyyymmdd), txn_time=txn_time,
        rrn=rrn, atm_id=f"MCRM{rng.randint(100, 999)}{rng.randint(10, 99)}",
        card_no=pan_masked, chb_date=_ddmmyyyy(chb_yyyymmdd),
        chb_ref=chb_ref, txn_amount=amount, adj_amount=amount,
        bank_adj_ref=bank_adj_ref, reason_desc=reason,
        atm_location=loc, pincode=pin,
        adj_settlement_date=_ddmmyyyy(business_date),
    )


def _row(t: AdjTxn) -> List:
    """Map AdjTxn to the 44-column row in COLUMNS order."""
    return [
        t.id,                            # 1 id
        t.adj_settlement_date,           # 2 Adjdate (= adjustment posting date)
        t.adj_type,                      # 3 Adjtype
        "HPS",                           # 4 ACQ
        "IDF",                           # 5 ISR
        "'00",                           # 6 Response
        t.txn_date,                      # 7 TxnDate
        t.txn_time,                      # 8 TxnTime
        f"'{t.rrn}",                     # 9 RRN (text-preserving leading apostrophe)
        t.atm_id,                        # 10 ATMID
        t.card_no,                       # 11 CardNo
        t.chb_date,                      # 12 ChbDate
        t.chb_ref,                       # 13 ChbRef
        t.txn_amount,                    # 14 TxnAmount
        t.adj_amount,                    # 15 AdjAmount
        0,                               # 16 ACQFee
        0,                               # 17 ISSFee
        0,                               # 18 ISSFeeSW
        0,                               # 19 NpciFee
        0,                               # 20 AcqFeeTax
        0,                               # 21 IssFeeTax
        0,                               # 22 NpciTAX
        f"IDF/HPS/{t.id}",               # 23 AdjRef
        t.bank_adj_ref,                  # 24 BankAdjRef
        "Proactive" if t.adj_type == "Credit Adj" else f"CRM-{t.id}",  # 25 AdjProof
        t.reason_desc,                   # 26 ReasonDesc
        t.pincode,                       # 27 Pincode
        t.atm_location,                  # 28 ATMLocation
        "N",                             # 29 MultiDisputeGroup
        "N",                             # 30 FCQM
        t.adj_settlement_date,           # 31 AdjSettlementDate
        "0.00",                          # 32 CustomerPenalty
        "01:35:37",                      # 33 AdjTime
        "2C",                            # 34 Cycle
        t.adj_settlement_date,           # 35 TATExpiryDate
        t.txn_amount,                    # 36 AcqSTLAmount
        "356",                           # 37 AcqCC
        "05",                            # 38 PanEntryMode
        "2XX",                           # 39 ServiceCode
        "4",                             # 40 CardDataInputCapability
        "6011",                          # 41 MCCCode
        "",                              # 42 ComplaintNumber
        "",                              # 43 ComplaintClosedReason
        "",                              # 44 Remark
    ]


def generate(num_txns: int, business_date: str,
             seed: Optional[int] = None) -> Tuple[List[AdjTxn], Workbook]:
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    rrn_start = rng.randint(600_000_000_000, 699_999_999_999)
    id_start  = rng.randint(10_000, 99_900)

    txns = [_make_txn(i, business_date, rng, rrn_start, id_start)
            for i in range(num_txns)]

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(COLUMNS)
    for t in txns:
        ws.append(_row(t))

    return txns, wb


def write_outputs(txns: List[AdjTxn], wb: Workbook, out_path: str,
                  business_date: str) -> None:
    wb.save(out_path)
    base, _ = os.path.splitext(out_path)

    totals = {
        "business_date": business_date,
        "num_records": len(txns),
        "credit_adj_count": sum(1 for t in txns if t.adj_type == "Credit Adj"),
        "chargeback_count": sum(1 for t in txns if t.adj_type == "Chargeback"),
        "total_adj_amount_rupees": round(sum(t.adj_amount for t in txns), 2),
        "reason_counts": _counter([t.reason_desc for t in txns]),
    }
    with open(base + "_expected_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


def _counter(xs):
    out = {}
    for x in xs: out[x] = out.get(x, 0) + 1
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="NFS Adjustment Report generator (.xlsx)")
    p.add_argument("--num-txns", type=int, default=50)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--random", action="store_true")
    p.add_argument("--output", default="adjustment.xlsx")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    txns, wb = generate(args.num_txns, bdate, args.seed)
    write_outputs(txns, wb, args.output, bdate)
    print(f"  wrote {len(txns)} rows × 44 cols → {args.output}")
    print(f"  totals → {os.path.splitext(args.output)[0]}_expected_totals.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
