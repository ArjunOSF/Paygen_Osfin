"""
ntsl_generator.py
=================
NFS NTSL / Daily Settlement Statement (.xlsx).
Source: real sample NTSLIDF290326_2C.xlsx (~395 rows, single sheet).

Filename pattern: NTSL{INST}{DDMMYY}_{CYCLE}.xlsx
  Example: NTSLIDF290326_2C = NFS Settlement, IDFC, 29 Mar 2026, Cycle 2C

Sheet structure (single sheet, multi-block, ~395 rows):

BLOCK 1 — Main Bank (Debit)
  Title: "Daily Settlement Statement for IDFC FIRST BANK LTD as on DD/MM/YYYY"
  Description | No of Txns | Debit | Credit
  Acquirer rows (BI/MS/PC/WDL × Approved Fee/GST/Declined × CC/Micro-ATM)
  Issuer rows  (same + NPCI Switching Fee variants + ICCW-ATM for WDL)
  Settlement Charges
  Issuer/Acquirer Sub Totals
  Settlement Amount
  Net Adjusted Amount
  Final Settlement Amount
  Dispute Adjustments sub-block

BLOCK 2 — Main Bank Credit Card
  Title: "...IDFC FIRST BANK LTD CREDIT CARD as on..."
  Same template, (CC) suffix on rows

BLOCK 3..N — Sub-members (PREPAID / IDF ATM suffixes)
  EROUTE / INDIA1 / LIVQUIK / TRI O TECH / IDFC PREPAID / EBIX

GRAND TOTAL — "Final Settlement Amount Including Sub-Member Bank"

SPONSOR BANK REPORTS — for each sub-member, two reports
  "Sponsor Bank Report With {ENTITY} as an Acquirer as on..."
  "Sponsor Bank Report With {ENTITY} as an Issuer as on..."

MICRO ATM SUMMARY
  Headers: Description | FeeSlab | Count | TxnAmt | Fee | Fee Tax
  Acquirer/Issuer × Less than Rs. 100 / Rs. 100 and above

NOTES
  3 numbered notes about GST and interchange invoicing
"""

from __future__ import annotations
import argparse, json, os, random, re, sys
from datetime import datetime
from typing import List, Optional, Tuple, Dict

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font
except ImportError:
    print("error: openpyxl not installed. Run: pip3 install openpyxl", file=sys.stderr)
    sys.exit(2)


HEADERS = ["Description", "No of Txns", "Debit", "Credit"]
MICRO_HEADERS = ["Description", "FeeSlab", "Count", "TxnAmt", "Fee", "Fee Tax"]

TXN_TYPES = ["BI", "MS", "PC", "WDL"]   # Balance Inquiry / Mini Statement / PIN Change / Withdrawal


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


def _slash_date(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}/{yyyymmdd[4:6]}/{yyyymmdd[:4]}"


# ---------------------------------------------------------------------------
# Section emitters
# ---------------------------------------------------------------------------

def _acquirer_rows(rng: random.Random, suffix: str = "") -> List[List]:
    """Acquirer-side rows (Description / No of Txns / Debit / Credit).
    suffix: '' for debit, ' CC' for credit-card section."""
    rows: List[List] = []
    for tt in TXN_TYPES:
        # Approved Fee
        cnt = rng.randint(1, 500); fee_cr = round(cnt * rng.uniform(2, 8), 2)
        rows.append([f"Acquirer {tt} Approved Fee{suffix}", cnt, 0, fee_cr])
        rows.append([f"Acquirer {tt} Approved Fee - GST{suffix}", cnt, 0, round(fee_cr * 0.18, 2)])
        # Micro-ATM variants
        if rng.random() < 0.7:
            mcnt = rng.randint(1, 100)
            rows.append([f"Acquirer {tt} Approved Fee (Micro-ATM)", mcnt, 0, 0])
            rows.append([f"Acquirer {tt} Approved Fee - GST (Micro-ATM)", mcnt, 0, 0])
        # Declined
        rows.append([f"Acquirer {tt} Declined{suffix}", rng.randint(1, 50), 0, 0])
        if tt == "WDL":
            # Withdrawal-specific
            wdl_cnt = rng.randint(100, 5000); wdl_amt = wdl_cnt * rng.choice([200, 500, 1000, 2000, 5000])
            rows.append([f"Acquirer WDL Transaction Amount{suffix}", 1, 0, wdl_amt])
            mcnt = rng.randint(50, 200)
            rows.append([f"Acquirer WDL Approved Fee (Micro-ATM)", mcnt, 0, round(mcnt * 10.5, 2)])
            rows.append([f"Acquirer WDL Approved Fee - GST (Micro-ATM)", mcnt, 0, round(mcnt * 10.5 * 0.18, 2)])
            rows.append([f"Acquirer WDL Transaction Amount (Micro-ATM)", mcnt, 0, mcnt * 4500])
            rows.append([f"Acquirer WDL Declined (Micro-ATM)", rng.randint(1, 30), 0, 0])
    return rows


def _issuer_rows(rng: random.Random, suffix: str = "") -> List[List]:
    """Issuer-side rows. Includes NPCI Switching Fee + ICCW-ATM (Issuer-only)."""
    rows: List[List] = []
    for tt in TXN_TYPES:
        cnt = rng.randint(1, 1500); fee_dr = round(cnt * rng.uniform(2, 8), 2)
        rows.append([f"Issuer {tt} Approved Fee{suffix}", cnt, fee_dr, 0])
        rows.append([f"Issuer {tt} Approved Fee - GST{suffix}", cnt, round(fee_dr * 0.18, 2), 0])
        if rng.random() < 0.8:
            ncnt = cnt + rng.randint(0, 100)
            switch_fee = round(ncnt * rng.uniform(0.3, 0.6), 2)
            rows.append([f"Issuer {tt} Approved NPCI Switching Fee", ncnt, switch_fee, 0])
            rows.append([f"Issuer {tt} Approved NPCI Switching Fee - GST", ncnt, round(switch_fee * 0.18, 2), 0])
        rows.append([f"Issuer {tt} Approved Fee (Micro-ATM)", rng.randint(1, 50), 0, 0])
        rows.append([f"Issuer {tt} Approved Fee - GST (Micro-ATM)", rng.randint(1, 50), 0, 0])
        rows.append([f"Issuer {tt} Declined{suffix}", rng.randint(1, 300), 0, 0])
        if tt == "WDL":
            wcnt = rng.randint(500, 15000)
            rows.append([f"Issuer WDL Transaction Amount", wcnt, wcnt * 5000, 0])
            mcnt = rng.randint(50, 200)
            rows.append([f"Issuer WDL Approved Fee (Micro-ATM)", mcnt, round(mcnt * 13, 2), 0])
            rows.append([f"Issuer WDL Approved Fee - GST (Micro-ATM)", mcnt, round(mcnt * 13 * 0.18, 2), 0])
            rows.append([f"Issuer WDL Transaction Amount (Micro-ATM)", mcnt, mcnt * 3375, 0])
            iccw = rng.randint(1, 1000)
            rows.append([f"Issuer WDL Approved Fee (ICCW-ATM)", iccw, round(iccw * 19, 2), 0])
            rows.append([f"Issuer WDL Approved Fee - GST (ICCW-ATM)", iccw, round(iccw * 19 * 0.18, 2), 0])
            rows.append([f"Issuer WDL Transaction Amount (ICCW-ATM)", iccw, iccw * 2700, 0])
            rows.append([f"Issuer WDL Declined", rng.randint(100, 3000), 0, 0])
            rows.append([f"Issuer WDL Declined (Micro-ATM)", rng.randint(10, 100), 0, 0])
            rows.append([f"Issuer WDL Declined (ICCW-ATM)", rng.randint(1, 50), 0, 0])
    return rows


def _emit_section(ws, title: str, rng: random.Random, suffix: str,
                  include_acquirer: bool = True, include_issuer: bool = True) -> Tuple[float, float]:
    """Emit one settlement section. Returns (final_dr, final_cr)."""
    ws.append([title])
    ws.append([])
    ws.append(HEADERS)

    sub_dr_total = 0.0
    sub_cr_total = 0.0

    if include_acquirer:
        for r in _acquirer_rows(rng, suffix):
            ws.append(r)
            sub_dr_total += r[2]; sub_cr_total += r[3]
        ws.append([])

    if include_issuer:
        for r in _issuer_rows(rng, suffix):
            ws.append(r)
            sub_dr_total += r[2]; sub_cr_total += r[3]
        ws.append([])

    ws.append(["Settlement Charges", None, None, 0])
    ws.append([])
    ws.append(["Issuer/Acquirer Sub Totals", None, round(sub_dr_total, 2), round(sub_cr_total, 2)])
    ws.append([])
    settlement = round(sub_dr_total - sub_cr_total, 2)
    ws.append(["Settlement Amount", None, max(settlement, 0), max(-settlement, 0)])
    ws.append([])
    # Net Adjusted Amount (small adjustment)
    adj = round(rng.uniform(0, 100), 2) if rng.random() < 0.4 else 0
    ws.append(["Net Adjusted Amount", None, 0, adj])
    ws.append([])
    final = round(settlement - adj, 2)
    ws.append(["Final Settlement Amount", None, abs(final) if final > 0 else 0, abs(final) if final < 0 else 0])
    ws.append([])
    return abs(final), 0


def _emit_dispute_block(ws, rng: random.Random) -> None:
    """Dispute Adjustments sub-block."""
    ws.append(["Dispute Adjustments"])
    ws.append([])
    ws.append(HEADERS)
    cnt = rng.randint(0, 5)
    amt = round(rng.uniform(0, 10000), 2) if cnt else 0
    if cnt:
        ws.append(["Total CREDIT Adjustment Amount", cnt, 0, amt])
    ws.append([])
    ws.append(["Adjustment Sub Totals", None, 0, amt])
    ws.append([])
    ws.append(["Net Adjusted Amount", None, None, amt])
    ws.append([])


def _emit_sponsor_block(ws, sub_member_name: str, suffix: str,
                         date_str: str, rng: random.Random, role: str) -> None:
    """One Sponsor Bank Report block (Acquirer or Issuer)."""
    ws.append([f"Sponsor Bank Report With {sub_member_name}{suffix} as an {role} as on {date_str}"])
    ws.append([])
    ws.append(HEADERS)
    if role == "Acquirer":
        rows = _acquirer_rows(rng)[:6]
    else:
        rows = _issuer_rows(rng)[:8]
    for r in rows:
        ws.append(r)
    ws.append([])


def _emit_micro_atm_summary(ws, bank_name: str, date_str: str, rng: random.Random) -> None:
    """Micro ATM Summary block."""
    ws.append([f"Summary For Micro ATM Transactions(Approved Transaction) for {bank_name} as on {date_str}"])
    ws.append(MICRO_HEADERS)
    for side in ("Acquirer", "Issuer"):
        for slab in ("Less than Rs. 100", "Rs. 100 and above"):
            cnt = rng.randint(0, 200) if slab == "Rs. 100 and above" else 0
            txn_amt = cnt * rng.choice([4000, 4500, 5000])
            fee = round(cnt * rng.uniform(10, 13), 2)
            fee_tax = round(fee * 0.18, 2)
            ws.append([f"{side} WDLTransaction (Micro ATM)", slab, cnt, txn_amt, fee, fee_tax])
    ws.append([])


def _emit_notes(ws) -> None:
    ws.append(["Note:"])
    ws.append([])
    ws.append(["1.The break-up of GST as CGST+S/UTGST or IGST would be available in monthly NPCI Fee Invoice and monthly tax report for interchange fee."])
    ws.append([])
    ws.append(["2.Members are advised to record the transactions on gross basis i.e. for Issuing and Acquiring transactions separately."])
    ws.append([])
    ws.append(["3.The interchange fee receiving members will have to raise invoices to paying members within the specified timelines as per GST rule."])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

SUB_MEMBERS = [
    ("EROUTE TECHNOLOGIES PRIVATE LIMITED", " PREPAID"),
    ("INDIA1 PAYMENTS LIMITED", " IDF ATM"),
    ("LIVQUIK TECHNOLOGY (INDIA) PRIVATE LIMITED", ""),
    ("TRI O TECH SOLUTIONS PRIVATE LIMITED", " TRO"),
    ("IDFC PREPAID", ""),
    ("EBIX PAYMENT SERVICES PRIVATE LIMITED", ""),
]


def generate(business_date: str, bank_name: str = "IDFC FIRST BANK LTD",
             seed: Optional[int] = None) -> Workbook:
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    date_str = _slash_date(business_date)
    date_dash = f"{business_date[6:8]}-{business_date[4:6]}-{business_date[:4]}"

    wb = Workbook()
    ws = wb.active
    sheet_name = f"NTSLIDF{business_date[6:8]}{business_date[4:6]}{business_date[2:4]}_2C"
    ws.title = sheet_name[:31]

    # Block 1: Main bank (Debit)
    title1 = f"Daily Settlement Statement for {bank_name} as on {date_str}"
    final_main, _ = _emit_section(ws, title1, rng, "")
    _emit_dispute_block(ws, rng)

    # Block 2: Credit Card
    title2 = f"Daily Settlement Statement for {bank_name} CREDIT CARD as on {date_str}"
    final_cc, _ = _emit_section(ws, title2, rng, " (CC)", include_acquirer=False)
    _emit_dispute_block(ws, rng)

    # Block 3..N: Sub-members
    sub_member_finals: List[Tuple[str, str, float]] = []
    for sm_name, sm_suffix in SUB_MEMBERS:
        title_sm = f"Daily Settlement Statement for {sm_name}{sm_suffix} as on {date_str}"
        final_sm, _ = _emit_section(ws, title_sm, rng, "", include_acquirer=False)
        _emit_dispute_block(ws, rng)
        sub_member_finals.append((sm_name, sm_suffix, final_sm))

    # Grand total
    grand = final_main + final_cc + sum(f for _, _, f in sub_member_finals)
    ws.append(["Net Adjusted Amount", None, None, 0])
    ws.append([])
    ws.append(["Final Settlement Amount Including Sub-Member Bank", None, None, round(grand, 2)])
    ws.append([])

    # Sponsor Bank Reports — for each sub-member, two reports
    for sm_name, sm_suffix, _ in sub_member_finals:
        _emit_sponsor_block(ws, sm_name, sm_suffix, date_dash, rng, "Acquirer")
        _emit_sponsor_block(ws, sm_name, sm_suffix, date_dash, rng, "Issuer")
    # Plus Credit Card sponsor block
    _emit_sponsor_block(ws, bank_name, " CREDIT CARD", date_dash, rng, "Acquirer")
    _emit_sponsor_block(ws, bank_name, " CREDIT CARD", date_dash, rng, "Issuer")

    # Micro ATM Summary
    _emit_micro_atm_summary(ws, bank_name, date_dash, rng)

    # Notes
    _emit_notes(ws)

    return wb


def write_outputs(wb: Workbook, out_path: str, business_date: str,
                  bank_name: str) -> None:
    wb.save(out_path)
    base, _ = os.path.splitext(out_path)
    totals = {
        "business_date": business_date,
        "bank": bank_name,
        "sub_members": [s[0] for s in SUB_MEMBERS],
        "blocks": [
            "Main Bank (Debit)", "Main Bank Credit Card",
        ] + [f"Sub-member: {s[0]}" for s in SUB_MEMBERS] + [
            "Grand Total", "Sponsor Bank Reports", "Micro ATM Summary", "Notes",
        ],
    }
    with open(base + "_expected_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="NFS NTSL/DSR Daily Settlement Statement (.xlsx)")
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--bank-name", default="IDFC FIRST BANK LTD")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--random", action="store_true")
    p.add_argument("--output", default="ntsl.xlsx")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    wb = generate(bdate, args.bank_name, args.seed)
    write_outputs(wb, args.output, bdate, args.bank_name)
    print(f"  wrote NTSL with {2 + len(SUB_MEMBERS)} settlement blocks → {args.output}")
    print(f"  totals → {os.path.splitext(args.output)[0]}_expected_totals.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
