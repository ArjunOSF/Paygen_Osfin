"""
mci_ar_generator.py
===================
Mastercard Debit Switch Daily Control Report (MCI.AR).
Confirmed format from real IDFC Bank sample (WORK OF: 03/31/26).

Report structure:
  Page 1  — ACQUIRING PROCESSOR transactions (CIRRUS / MASTERCARD / MAESTRO ATM)
  Page 2  — ACQUIRING PROCESSOR financial settlement
  Page 3  — ISSUING PROCESSOR transactions
  Page 4  — ISSUING PROCESSOR financial settlement
  Page 5  — NET SETTLEMENT SUMMARY
  Page 8  — ISSUING PROCESSOR COUNTRY 356 (INR) transactions
  Page 9  — ISSUING PROCESSOR COUNTRY 356 (INR) financial settlement

Network → card type mapping:
  MC     → MASTERCARD
  NFS    → CIRRUS (NFS uses Mastercard's CIRRUS protocol for India)
  Others → skipped (RuPay / Visa go through their own switch reports)
"""

from datetime import datetime
from typing import List, Dict, Tuple
from data_generator import Transaction

LINE_W = 100


def _p(text: str) -> str:
    return text.ljust(LINE_W)


def _blank() -> str:
    return _p("")


def _divider(ch: str = "-", w: int = 80) -> str:
    return _p(ch * w)


def _fmt(amount_paise: int) -> str:
    return f"{amount_paise / 100:,.2f}"


def _section_hdr(report_id: str, title1: str, title2: str, work_date: str, page: int) -> List[str]:
    return [
        _p(f"{report_id}"),
        _p(f"MASTERCARD DEBIT SWITCH"),
        _p(f"DAILY CONTROL REPORT  {title1}"),
        _p(f"WORK OF: {work_date}{'':40}PAGE:{page:3d}"),
        _blank(),
    ]


def _processor_block(config: dict) -> List[str]:
    name   = config.get("processor_name", "IDFC BANK LIMITED")
    pid    = config.get("processor_id",   "9000002007")
    ica    = config.get("acquiring_ica",  "016955")
    return [
        _p(f"PROCESSOR      : {name}"),
        _p(f"PROCESSOR ID   : {pid}"),
        _p(f"CURRENCY       : 840 U.S. Dollar"),
        _p(f"SETTLEMENT ICA : {ica}"),
        _blank(),
    ]


def _txn_table_header() -> List[str]:
    return [
        _p("TRANSACTIONS"),
        _p(f"{'':20}FINANCIAL{'':8}INTERCHANGE"),
        _p(f"{'':10}NUMBER{'':12}NUMBER"),
        _p(f"{'':10}APPROVED DENIALS  AMOUNT{'':4}TRANS{'':3}AMOUNT"),
        _p(f"{'':30}{'':15}COUNTS{'':2}FINANCIAL % BASED  NONFIN  NONBILL"),
        _p("-" * 90),
    ]


def _card_type_rows(
    label: str,
    txns: List[Transaction],
    interchange_rate: float = 0.0018,
) -> Tuple[List[str], int, int, int]:
    """Generate transaction rows for one card type. Returns (lines, approved, denied, total_paise)."""
    if not txns:
        return [], 0, 0, 0

    approved = [t for t in txns if t.switch_status == "00"]
    denied   = [t for t in txns if t.switch_status != "00"]

    # Split into CASH WD SAV / CASH WD DDA / BALANCE INQ (80 / 15 / 5)
    n_sav  = max(1, int(len(approved) * 0.80))
    n_dda  = max(0, int(len(approved) * 0.15))
    n_bi   = max(0, len(approved) - n_sav - n_dda)

    sav_paise = sum(t.amount for t in approved[:n_sav])
    dda_paise = sum(t.amount for t in approved[n_sav:n_sav + n_dda])
    # Balance inquiries have no financial amount
    total_paise = sav_paise + dda_paise

    intchg_sav = int(sav_paise * interchange_rate)
    intchg_dda = int(dda_paise * interchange_rate)
    intchg_bi  = n_bi * 25   # flat 25 paise per BI

    lines = [_p(f"@ {label}")]

    def row(txn_type, n_appr, n_den, amt_p, intchg_p):
        den_s  = f"{n_den:>3}" if n_den else "   "
        amt_s  = f"{_fmt(amt_p):>12}" if amt_p else f"{'0.00':>12}"
        fee_s  = f"{_fmt(intchg_p):>10}"
        return _p(f"{'':2}{txn_type:<16}{n_appr:>4} {den_s}  {amt_s}{'':3}{n_appr + n_den:>5}{'':4}{fee_s} CR")

    if n_sav:
        lines.append(row("CASH WD SAV", n_sav, len(denied), sav_paise, intchg_sav))
    if n_dda:
        lines.append(row("CASH WD DDA", n_dda, 0, dda_paise, intchg_dda))
    if n_bi:
        lines.append(row("BALANCE INQ", n_bi, 0, 0, intchg_bi))

    tot_appr = len(approved)
    tot_den  = len(denied)
    tot_fee  = intchg_sav + intchg_dda + intchg_bi
    lines.append(_p(
        f"{'':2}{'TOTALS':<16}{tot_appr:>4} {tot_den:>3}  {_fmt(total_paise):>12}{'DB' if total_paise else '  '}"
        f"{'':3}{tot_appr + tot_den:>5}{'':4}{_fmt(tot_fee):>10} CR"
    ))
    lines.append(_p("-" * 90))
    return lines, tot_appr, tot_den, total_paise


def _page_acq_txns(
    work_date: str, config: dict, report_id: str,
    mc_txns: List[Transaction], nfs_txns: List[Transaction],
) -> List[str]:
    lines = _section_hdr(report_id, "ACQUIRING PROCESSOR", "", work_date, 1)
    lines += _processor_block(config)
    lines += _txn_table_header()

    # CIRRUS = NFS transactions
    cirrus_rows, ca, cd, cp = _card_type_rows("CIRRUS", nfs_txns)
    lines += cirrus_rows

    # MASTERCARD = MC transactions
    mc_rows, ma, md, mp = _card_type_rows("MASTERCARD", mc_txns)
    lines += mc_rows

    # MAESTRO ATM = small slice of MC (15%)
    maestro_txns = mc_txns[:max(0, int(len(mc_txns) * 0.15))]
    mao_rows, maa, mad, map_ = _card_type_rows("MAESTRO ATM", maestro_txns)
    lines += mao_rows

    # Grand totals
    gt_appr   = ca + ma + maa
    gt_den    = cd + md + mad
    gt_paise  = cp + mp + map_
    gt_intchg = int(gt_paise * 0.0018)
    lines += [
        _p("@ GRAND TOTALS:"),
        _p(f"{'':2}TOTALS{'':10}{gt_appr:>4} {gt_den:>3}  {_fmt(gt_paise):>12}DB{'':3}"
           f"{gt_appr + gt_den:>5}{'':4}{_fmt(gt_intchg):>10} CR"),
        _blank(),
    ]
    return lines


def _page_settlement(
    work_date: str, config: dict, report_id: str, page: int,
    role: str, total_paise: int, intchg_paise: int,
) -> List[str]:
    lines = _section_hdr(report_id, f"{role} PROCESSOR", "", work_date, page)
    lines += _processor_block(config)
    n_txns = max(1, total_paise // 150000)   # rough estimate

    lines += [
        _p(f"{'':25}DEBITS{'':15}CREDITS"),
        _p(f"{'':15}NUMBER{'':5}AMOUNT{'':10}NUMBER{'':5}AMOUNT"),
        _blank(),
        _p(f"FINANCIAL"),
        _p(f"SETTLEMENT{'':10}{'':8}0.00{'':12}{n_txns:>5}{'':3}{_fmt(total_paise):>14}"),
        _blank(),
        _p(f"@ EXCEPTION ITEMS"),
        _p(f"  SAME DAY (CODE 09){'':30}0.00{'':20}0.00"),
        _p(f"  NON-SAME DAY{'':36}0.00{'':20}0.00"),
        _p(f"  SETTLEMENT{'':38}0.00{'':20}0.00"),
        _p(f"  NON-SAME DAY, NON-FINANCIAL{'':20}0.00{'':20}0.00"),
        _blank(),
        _p(f"→ TOTAL FINANCIAL SETTLEMENT{'':20}{_fmt(total_paise):>14}"),
        _blank(),
        _p(f"@ TOTAL FEES"),
        _p(f"  SETTLEMENT{'':10}{'':8}0.00{'':12}{'':8}{_fmt(intchg_paise):>12}"),
        _blank(),
        _p(f"@ INTERCHANGE"),
        _p(f"  SETTLEMENT{'':50}{_fmt(total_paise + intchg_paise):>14}"),
        _p(f"  - ACCESS CHARGE (ATM SURCHRG){'':30}0.00"),
        _p(f"  - ACCESS CHARGE (POS SURCHRG){'':30}0.00"),
        _p(f"  - ACCESS CHARGE (ATM SUR) REV{'':30}0.00"),
        _p(f"  - ACCESS CHARGE (POS SUR) REV{'':30}0.00"),
        _blank(),
        _p(f"@ SURCHARGE FREE"),
        _blank(),
    ]
    return lines


def _page_country356(
    work_date: str, config: dict, report_id: str,
    inr_txns: List[Transaction], page: int,
) -> List[str]:
    """Country 356 (INR) issuing transactions page."""
    lines = [
        _p(f"{report_id}"),
        _p(f"MASTERCARD DEBIT SWITCH"),
        _p(f"DAILY CONTROL REPORT  ISSUING PROCESSOR"),
        _p(f"COUNTRY: 356{'':40}WORK OF: {work_date}{'':15}PAGE:{page:3d}"),
        _blank(),
    ]
    lines += _processor_block(config)
    lines += _txn_table_header()

    rows, appr, den, paise = _card_type_rows("CIRRUS", inr_txns, interchange_rate=0.002)
    lines += rows
    lines += [
        _p(f"@ GRAND TOTALS:"),
        _p(f"{'':2}TOTALS{'':10}{appr:>4} {den:>3}  {_fmt(paise):>12}DB{'':3}"
           f"{appr + den:>5}{'':4}{_fmt(int(paise * 0.002)):>10} CR"),
        _blank(),
    ]
    return lines


def _page_country356_settlement(
    work_date: str, config: dict, report_id: str,
    total_paise: int, page: int,
) -> List[str]:
    lines = [
        _p(f"{report_id}"),
        _p(f"MASTERCARD DEBIT SWITCH"),
        _p(f"DAILY CONTROL REPORT  ISSUING PROCESSOR"),
        _p(f"COUNTRY: 356{'':40}WORK OF: {work_date}{'':15}PAGE:{page:3d}"),
        _blank(),
    ]
    lines += _processor_block(config)
    n_txns = max(1, total_paise // 150000)
    lines += [
        _p(f"{'':25}DEBITS{'':15}CREDITS"),
        _p(f"{'':15}NUMBER{'':5}AMOUNT{'':10}NUMBER{'':5}AMOUNT"),
        _blank(),
        _p(f"FINANCIAL"),
        _p(f"SETTLEMENT{'':10}{n_txns:>5}{'':3}{_fmt(total_paise):>14}{'':20}0.00"),
        _blank(),
        _p(f"@ EXCEPTION ITEMS"),
        _p(f"  SAME DAY (CODE 89){'':30}0.00{'':20}0.00"),
        _p(f"  NON-SAME DAY{'':36}0.00{'':20}0.00"),
        _blank(),
        _p(f"→ TOTAL FINANCIAL SETTLEMENT{'':20}{_fmt(total_paise):>14}"),
        _blank(),
        _p(f"@ TOTAL FEES"),
        _p(f"  SETTLEMENT{'':50}{_fmt(int(total_paise * 0.002)):>12}"),
        _blank(),
        _p(f"→ NET DUE TO/FROM MC{'':28}{_fmt(total_paise + int(total_paise * 0.002)):>14}"),
        _blank(),
    ]
    return lines


def generate(
    transactions: List[Transaction],
    config: dict,
    business_date: str,   # YYYYMMDD
    output_path: str,
) -> None:
    """Write MCI.AR daily control report. Returns nothing (always written)."""
    yy = business_date[2:4]
    mm = business_date[4:6]
    dd = business_date[6:8]
    work_date = f"{mm}/{dd}/{yy}"
    now       = datetime.now()
    ts        = now.strftime("%y%m%d%H%M%S")

    report_id = "1SWCHD363"
    ctrl_rec  = f"5200{ts}P"

    # Partition transactions
    mc_txns  = [t for t in transactions if t.network.upper() == "MC"  and t.in_switch]
    nfs_txns = [t for t in transactions if t.network.upper() == "NFS" and t.in_switch]
    all_txns = mc_txns + nfs_txns

    mc_paise  = sum(t.amount for t in mc_txns  if t.switch_status == "00")
    nfs_paise = sum(t.amount for t in nfs_txns if t.switch_status == "00")
    total_acq_paise = mc_paise + nfs_paise
    acq_intchg      = int(total_acq_paise * 0.0018)

    # For INR country section, use all switch-present transactions
    inr_txns  = [t for t in all_txns if t.switch_status == "00"]
    inr_paise = sum(t.amount for t in inr_txns)

    all_lines = [ctrl_rec, ""]

    # Page 1 — ACQ transactions
    all_lines += _page_acq_txns(work_date, config, report_id, mc_txns, nfs_txns)

    # Page 2 — ACQ financial settlement
    all_lines += _page_settlement(work_date, config, report_id, 2, "ACQUIRING", total_acq_paise, acq_intchg)

    # Page 3 — ISS transactions (mirror of ACQ for on-us)
    all_lines += _page_acq_txns(work_date, config, report_id, mc_txns, nfs_txns)
    # Patch page number (last occurrence of PAGE: 1 → PAGE: 3) handled by section_hdr
    # Re-build page 3 explicitly:
    all_lines[-len(_page_acq_txns(work_date, config, report_id, mc_txns, nfs_txns)):] = []
    p3 = _section_hdr(report_id, "ISSUING PROCESSOR", "", work_date, 3)
    p3 += _processor_block(config)
    p3 += _txn_table_header()
    cirrus_rows, ca, cd, cp = _card_type_rows("CIRRUS", nfs_txns)
    mc_rows, ma, md, mp     = _card_type_rows("MASTERCARD", mc_txns)
    gt_paise = cp + mp
    gt_intchg = int(gt_paise * 0.0018)
    p3 += cirrus_rows + mc_rows
    p3 += [
        _p("@ GRAND TOTALS:"),
        _p(f"{'':2}TOTALS{'':10}{ca + ma:>4} {cd + md:>3}  {_fmt(gt_paise):>12}DB{'':3}"
           f"{ca + ma + cd + md:>5}{'':4}{_fmt(gt_intchg):>10} CR"),
        _blank(),
    ]
    all_lines += p3

    # Page 4 — ISS financial settlement
    all_lines += _page_settlement(work_date, config, report_id, 4, "ISSUING", gt_paise, gt_intchg)

    # Page 8 — Country 356 ISS transactions
    all_lines += _page_country356(work_date, config, report_id, inr_txns, 8)

    # Page 9 — Country 356 ISS settlement
    all_lines += _page_country356_settlement(work_date, config, report_id, inr_paise, 9)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(all_lines) + "\n")
