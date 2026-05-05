"""
rupay_dsr_generator.py
======================
RuPay Daily Settlement Report (.xlsx).
Source: real sample (Book7, Sheet1, ~3005 rows, 30 columns).

Layout per sub-member:
  Detail rows: bank × txn-type × direction × channel
  Total       (subtotal per direction)
  Total Total
  INVAR D GST (GST adjustment block)
  Total
  Total Total Total  (grand total per bank)
  ↓ next sub-member

30 columns:
  A  Settlement Date
  B  Product Name              (RuPay POS / RuPay ATM / etc.)
  C  Bank Name                 (sub-member name)
  D  Settle Acq ID/ISS Bin     (IDFC16 / IDFC149 / IDFC8 etc.)
  E  Acq ID                    (817603 / 608367 / etc.)
  F  Inward/Outward            (INVAR A / INVAR D / INVAR D GST)
  G  Status                    (A=Auth/Approved | D=Declined)
  H  Transaction Cycle         (DMS Auth Transaction)
  I  Transaction Type          (Balance / Purchase / qSPA RC Money Load through)
  J  Channel                   (POS / ECOM / qSPA / MoneyLoad)
  K  TXN COUNT
  L  TXN CCY                   (356)
  M  Txn Amt DR
  N  Txn Amt CR
  O  SET CCY                   (356)
  P  SETAM DR
  Q  SETAM CR
  R  Int Fee Amt DR
  S  Int Fee Amt CR
  T  Mem Inc Fee Amt DR
  U  Mem Inc Fee Amt CR
  V  Customer Compensation Dr
  W  Customer Compensation Cr
  X  Oth Fee Amt DR
  Y  Oth Fee Amt CR
  Z  Oth Fee GST DR
  AA Oth Fee GST CR
  AB Final Sum Cr
  AC Final Sum Dr
  AD Final Net
"""

from __future__ import annotations
import argparse, json, os, random, re, sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple, Dict

try:
    from openpyxl import Workbook
except ImportError:
    print("error: openpyxl not installed. Run: pip3 install openpyxl", file=sys.stderr)
    sys.exit(2)


COLUMNS = [
    "Settlement Date", "Product Name", "Bank Name",
    "Settle Acq ID/ISS Bin", "Acq ID", "Inward/Outward", "Status",
    "Transaction Cycle", "Transaction Type", "Channel",
    "TXN COUNT", "TXN CCY", "Txn Amt DR", "Txn Amt CR", "SET CCY",
    "SETAM DR", "SETAM CR", "Int Fee Amt DR", "Int Fee Amt CR",
    "Mem Inc Fee Amt DR", "Mem Inc Fee Amt CR",
    "Customer Compensation Dr", "Customer Compensation Cr",
    "Oth Fee Amt DR", "Oth Fee Amt CR", "Oth Fee GST DR", "Oth Fee GST CR",
    "Final Sum Cr", "Final Sum Dr", "Final Net",
]

SUB_MEMBERS = [
    ("EBIX PAYMENT SERVICES PVT LTD",        "IDFC16",  "817603"),
    ("EROUTE TECHNOLOGIES PVT LTD",          "IDFC149", "817392"),
    ("LIVQUIK TECHNOLOGY (INDIA) PVT LTD",   "IDFC8",   "608367"),
    ("IDFC FIRST BANK LTD",                  "IDFC01",  "600116"),
    ("PAYU PAYMENTS PRIVATE LIMITED",        "IDFC2",   "608362"),
    ("PLUXEE INDIA PRIVATE LIMITED",         "IDFC11",  "607544"),
    ("PAY POINT INDIA NETWORK PVT LTD",      "IDFC18",  "607353"),
    ("TRI O TECH SOLUTIONS PRIVATE LIMITED", "IDFC18",  "608371"),
]

TXN_TYPE_CHANNELS = [
    ("Balance",                    "POS"),
    ("Balance",                    "ECOM"),
    ("Purchase",                   "POS"),
    ("Purchase",                   "ECOM"),
    ("qSPA RC Money Load through", "MoneyLoad"),
]


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


def _make_detail_row(date_str: str, sub_member: Tuple[str, str, str],
                     direction: str, status: str,
                     txn_type: str, channel: str,
                     count: int, amt: float, fee_dr: float,
                     gst_dr: float) -> List:
    bank_name, set_bin, acq_id = sub_member
    final_dr  = round(fee_dr + gst_dr, 2)
    final_cr  = round(fee_dr if direction == "INVAR A" else 0, 2)
    final_net = round(final_cr - final_dr, 2)
    return [
        date_str, "RuPay POS", bank_name, set_bin, acq_id,
        direction, status, "DMS Auth Transaction",
        txn_type, channel,
        count, 356,
        round(amt, 2) if direction != "INVAR A" else 0,
        round(amt, 2) if direction == "INVAR A" else 0,
        356, 0, 0, 0, 0,
        round(fee_dr if direction != "INVAR A" else 0, 2),
        round(fee_dr if direction == "INVAR A" else 0, 2),
        0, 0,
        round(fee_dr if direction != "INVAR A" else 0, 2),
        round(fee_dr if direction == "INVAR A" else 0, 2),
        round(gst_dr if direction != "INVAR A" else 0, 2),
        round(gst_dr if direction == "INVAR A" else 0, 2),
        final_cr, final_dr, final_net,
    ]


def _make_subtotal_row(label_count: int, total_count: int, total_amt: float,
                       total_fee_dr: float, total_gst_dr: float) -> List:
    """Build a 'Total' / 'Total Total' / 'Total Total Total' row.
    label_count = number of 'Total' words to put in column A onward."""
    final_dr  = round(total_fee_dr + total_gst_dr, 2)
    row: List = [None] * 30
    # Place 'Total' in cols 1, 2, ..., label_count
    for i in range(label_count):
        row[i] = "Total"
    # Place totals at column K (idx 10) and onward
    if total_count:
        row[10] = total_count
    if total_amt:
        row[12] = round(total_amt, 2)
    if total_fee_dr:
        row[19] = round(total_fee_dr, 2)
        row[23] = round(total_fee_dr, 2)
        row[25] = round(total_gst_dr, 2)
    row[27] = 0
    row[28] = round(final_dr, 2)
    row[29] = round(-final_dr, 2)
    return row


def _generate_subsection(date_str: str, sub_member: Tuple[str, str, str],
                          direction: str, rng: random.Random) -> Tuple[List[List], int, float, float, float]:
    """Generate detail rows for one (sub_member, direction) block + return totals."""
    rows: List[List] = []
    total_count = 0
    total_amt   = 0.0
    total_fee   = 0.0
    total_gst   = 0.0

    for txn_type, channel in TXN_TYPE_CHANNELS:
        for status in ("A", "D"):
            count = rng.randint(1, 200)
            if status == "D":
                count = max(1, count // 5)
            amt   = round(rng.uniform(100, 30000), 2) if status == "A" else 0.0
            fee_dr = round(amt * 0.0015, 2) if status == "A" else 0.0
            gst_dr = round(fee_dr * 0.18, 2)

            rows.append(_make_detail_row(date_str, sub_member, direction, status,
                                          txn_type, channel, count, amt, fee_dr, gst_dr))
            total_count += count
            total_amt   += amt
            total_fee   += fee_dr
            total_gst   += gst_dr

    return rows, total_count, total_amt, total_fee, total_gst


def generate(business_date: str, sub_members: Optional[List] = None,
             seed: Optional[int] = None) -> Workbook:
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    if sub_members is None:
        sub_members = SUB_MEMBERS

    date_str = _ddmmyyyy(business_date)

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(COLUMNS)

    grand_count = 0
    grand_amt = 0.0
    grand_fee = 0.0
    grand_gst = 0.0

    for sm in sub_members:
        for direction in ("INVAR D", "INVAR A"):
            rows, sub_cnt, sub_amt, sub_fee, sub_gst = _generate_subsection(
                date_str, sm, direction, rng)
            for r in rows:
                ws.append(r)
            ws.append(_make_subtotal_row(1, sub_cnt, sub_amt, sub_fee, sub_gst))
            ws.append(_make_subtotal_row(2, sub_cnt, sub_amt, sub_fee, sub_gst))

            # GST adjustment block (one INVAR D GST row per direction block)
            gst_count = max(1, sub_cnt // 30)
            gst_row = _make_detail_row(date_str, sm, "INVAR D GST", "A",
                                        "Tax Adjustment", "POS",
                                        gst_count, 0, sub_gst, 0)
            ws.append(gst_row)
            ws.append(_make_subtotal_row(1, gst_count, 0, sub_gst, 0))

            grand_count += sub_cnt + gst_count
            grand_amt   += sub_amt
            grand_fee   += sub_fee + sub_gst
            grand_gst   += sub_gst

        # Sub-member grand total row
        ws.append(_make_subtotal_row(3, grand_count, grand_amt, grand_fee, grand_gst))

    return wb


def write_outputs(wb: Workbook, out_path: str, business_date: str,
                  sub_members: List) -> None:
    wb.save(out_path)
    base, _ = os.path.splitext(out_path)
    totals = {
        "business_date": business_date,
        "num_sub_members": len(sub_members),
        "sub_members": [s[0] for s in sub_members],
        "rows_total": "see file (multi-section, with subtotals)",
    }
    with open(base + "_expected_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="RuPay DSR (Daily Settlement Report) xlsx generator")
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--random", action="store_true")
    p.add_argument("--output", default="rupay_dsr.xlsx")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    wb = generate(bdate, SUB_MEMBERS, args.seed)
    write_outputs(wb, args.output, bdate, SUB_MEMBERS)
    print(f"  wrote DSR with {len(SUB_MEMBERS)} sub-members → {args.output}")
    print(f"  totals → {os.path.splitext(args.output)[0]}_expected_totals.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
