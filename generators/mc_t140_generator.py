"""
mc_t140_generator.py
====================
Mastercard T140 Settlement Advice — mimics real MC report format.

Sections generated:
  1IP727010-AA  Acknowledgement (one per cycle — "NO DATA TO REPORT" for cycle 1)
  1IP727020-AA  Notification   (IRD breakdown + BSI subtotal per cycle)
  1IP728010-AA  Net Recon      (per-file net + cycle summary + clearing day total)

Links to T112:
  T140 CYCLE_TOTAL amount = Sum of DE4 in T112 for that cycle
  T140 FILE_TOTAL         = nostro movement (DR for issuing/acquiring)

File ID format: 001/{YYMMDD}/{MEMBER_ID_11}/{CYCLE}{SEQ}
  e.g. 001/230819/00000021577/01101
"""

from datetime import datetime, timedelta
from typing import List
from data_generator import Transaction

LINE_WIDTH = 133


def _pad(text: str, width: int = LINE_WIDTH) -> str:
    return text.ljust(width)


def _center(text: str, width: int = LINE_WIDTH) -> str:
    return text.center(width)


def _blank() -> str:
    return _pad("")


def _fmt_amt(paise: int) -> str:
    return f"{paise / 100:,.2f}"


def _section_1ip727010(run_date: str, cycle: int, business_date_iso: str, member_id_11: str) -> List[str]:
    """Acknowledgement section — always NO DATA TO REPORT for generated files."""
    cycle_str = f"{cycle:03d}"
    lines = [
        _pad(f"1IP727010-AA{'':43}MASTERCARD WORLDWIDE{'':39}RUN DATE: {run_date}"),
        _pad(f"{'':48}CLEARING CYCLE {cycle_str} - ACKNOWLEDGEMENT{'':30}PAGE NO:         1"),
        _pad(f"{'':62}{business_date_iso}{'':61}"),
        _pad(f" MEMBER ID: {member_id_11}{'':110}"),
        _pad(f"{'':53}NO DATA TO REPORT{'':61}"),
    ]
    return lines


def _section_1ip727020_p1(
    run_date: str, cycle: int, business_date_iso: str,
    member_id_11: str, file_id: str,
    net_txns: List[Transaction],
) -> List[str]:
    """Notification — IRD breakdown page."""
    cycle_str = f"{cycle:03d}"
    total_count  = len(net_txns)
    total_paise  = sum(t.amount for t in net_txns)
    fee_paise    = int(total_paise * 0.0131)    # ~1.31% interchange (PE rate)

    # Split into PE (electronic) and PF (full mag-stripe) — 80/20
    pe_count = max(1, int(total_count * 0.8))
    pf_count = total_count - pe_count
    pe_paise = int(total_paise * 0.8)
    pf_paise = total_paise - pe_paise
    pe_fee   = int(fee_paise * 0.8)
    pf_fee   = fee_paise - pe_fee

    def row(func, proc, ird, cnt, amt_p, amt_dr_cr, fee_p, fee_dr_cr):
        amt_s = f"{_fmt_amt(amt_p)} {amt_dr_cr}"
        fee_s = f"{_fmt_amt(fee_p)} {fee_dr_cr}"
        return _pad(
            f" {func:<12} {proc:<15} {ird:<3} {cnt:>8} {amt_s:>26} 356-INR {fee_s:>23} 356-INR"
        )

    sep = _pad(f" {'-'*12} {'-'*15} {'-'*3} {'-'*8} {'-'*26} {'-'*7} {'-'*23} {'-'*7}")

    lines = [
        _pad(f"1IP727020-AA{'':43}MASTERCARD WORLDWIDE{'':39}RUN DATE: {run_date}"),
        _pad(f" ACCEPTANCE BRAND: MCC{'':26}CLEARING CYCLE {cycle_str} - NOTIFICATION{'':34}PAGE NO:         1"),
        _pad(f" BUSINESS SERVICE LEVEL: INTRACOUNTRY{'':25}{business_date_iso}{'':61}"),
        _pad(f" BUSINESS SERVICE ID: 356001{'':105}"),
        _pad(f" FILE ID: {file_id}{'':113}"),
        _pad(f" MEMBER ID: {member_id_11}{'':110}"),
        _blank(),
        _pad(f" MASTERCARD SETTLED{'':52}RECON{'':25}FEE{'':40}"),
        _pad(f"{'':73}CURR{'':25}CURR{'':30}"),
        _pad(f" TRANS. FUNC. PROC.CODE{'':7}IRD{'':3}COUNTS{'':15}RECON AMOUNT{'':4}CODE{'':7}TRANS FEE{'':9}CODE{'':27}"),
        sep,
        row("FIRST PRES.", "PURCHASE   ORIG", "PE", pe_count, pe_paise, "DR", pe_fee, "CR"),
        row("",            "PURCHASE   ORIG", "PF", pf_count, pf_paise, "DR", pf_fee, "CR"),
        sep,
        _pad(
            f" FIRST PRES.  TOTAL{'':17}{total_count:>8} {_fmt_amt(total_paise):>24} DR 356-INR"
            f" {_fmt_amt(fee_paise):>21} CR 356-INR"
        ),
        _blank(),
        _pad(f" INTRACOUNTRY{'':120}"),
        _pad(f" MASTERCARD SETTLED{'':113}"),
        _pad(
            f" BUSINESS SERVICE ID SUBTOTAL{'':<11}{total_count:>8}"
            f" {_fmt_amt(total_paise):>24} DR 356-INR {_fmt_amt(fee_paise):>21} CR 356-INR"
        ),
    ]
    return lines


def _section_1ip727020_p2(
    run_date: str, cycle: int, business_date_iso: str,
    member_id_11: str, file_id: str,
    total_paise: int, fee_paise: int,
) -> List[str]:
    """Notification — BSI summary table page."""
    lines = [
        _pad(f"1IP727020-AA{'':43}MASTERCARD WORLDWIDE{'':39}RUN DATE: {run_date}"),
        _pad(f"{'':48}CLEARING CYCLE {cycle:03d} - NOTIFICATION{'':34}PAGE NO:         1"),
        _pad(f"{'':62}{business_date_iso}{'':61}"),
        _blank(),
        _pad(f" ACCEPTANCE BRAND : MCC{'':109}"),
        _pad(f" BUSINESS SERVICE LEVEL :INTRACOUNTRY{'':95}"),
        _pad(f" MEMBER ID: {member_id_11}{'':110}"),
        _pad(f" CURRENCY CODE : 356-INR{'':108}"),
        _blank(),
        _pad(f" BUSINESS{'':123}"),
        _pad(f" SERVICE{'':27}ORIG/{'':90}"),
        _pad(f"     ID{'':12}FILE ID{'':21}RVSL{'':10}RECON. AMOUNT{'':19}TRANSACTION FEE{'':19}"),
        _pad(f" {'-'*8} {'-'*28} {'-'*4}{'':4}{'-'*19}{'':14}{'-'*19}{'':15}"),
        _pad(
            f" 356001   {file_id:<28} ORIG{'':16}{_fmt_amt(total_paise):>16} DR"
            f"{'':23}{_fmt_amt(fee_paise):>16} CR{'':15}"
        ),
        _blank(),
        _pad(
            f"{'':27}GRAND TOTAL{'':19}{_fmt_amt(total_paise):>16} DR"
            f"{'':23}{_fmt_amt(fee_paise):>16} CR{'':15}"
        ),
        _blank(),
    ]
    return lines


def _section_1ip728010_cycle(
    run_date: str, run_time: str, cycle: int, business_date_iso: str,
    member_id_11: str, file_id: str, total_paise: int,
) -> List[str]:
    """Net recon per file — cycle level."""
    lines = [
        _pad(f"1IP728010-AA{'':43}MASTERCARD WORLDWIDE{'':39}RUN DATE: {run_date}"),
        _pad(f" ACCEPTANCE BRAND: MCC{'':36}CLEARING CYCLE {cycle:03d}{'':43}RUN TIME: {run_time}"),
        _pad(f" BUSINESS SERVICE LEVEL: INTRACOUNTRY{'':25}{business_date_iso}{'':36}PAGE NO:         1"),
        _pad(f" MEMBER ID: {member_id_11}{'':110}"),
        _pad(f" CURRENCY CODE :  356 - INR{'':105}"),
        _blank(),
        _pad(f" BUSINESS{'':57}RECON.{'':58}"),
        _pad(f" SERVICE{'':15}FILE ID{'':23}FILE ID{'':23}NET RECON{'':12}CURR.{'':22}"),
        _pad(f"    ID{'':14}TO MASTERCARD{'':15}FROM MASTERCARD{'':17}CURRENCY AMOUNT{'':7}CODE{'':25}"),
        _pad(f" {'_'*8}{'':3}{'_'*28}{'':3}{'_'*28}{'':3}{'_'*23}{'':3}{'_'*7}{'':22}"),
        _pad(
            f"  356001{'':32}{file_id:<28}{'':14}{_fmt_amt(total_paise):>16} DR   356-INR{'':22}"
        ),
        _blank(),
        _pad(
            f"  TOTAL{'':50}{_fmt_amt(total_paise):>16} DR   356-INR{'':22}"
        ),
        _blank(),
    ]
    return lines


def _section_1ip728010_summary(
    run_date: str, run_time: str, business_date_iso: str,
    member_id_11: str, cycles: list,
) -> List[str]:
    """Cycle summary + clearing day total."""
    grand_total = sum(p for _, _, p in cycles)

    lines = [
        _pad(f"1IP728010-AA{'':43}MASTERCARD WORLDWIDE{'':39}RUN DATE: {run_date}"),
        _pad(f" ACCEPTANCE BRAND: MCC{'':36}CLEARING CYCLE {'':3}SUMMARY{'':39}RUN TIME: {run_time}"),
        _pad(f" BUSINESS SERVICE LEVEL: INTRACOUNTRY{'':25}{business_date_iso}{'':36}PAGE NO:         2"),
        _pad(f" MEMBER ID: {member_id_11}{'':110}"),
        _pad(f" CURRENCY CODE :  356 - INR{'':105}"),
        _blank(),
        _pad(f"{'':60}RECON.{'':58}"),
        _pad(f"  CYCLE{'':13}FILE ID{'':23}FILE ID{'':23}NET RECON{'':12}CURR.{'':22}"),
        _pad(f" ACTIVITY{'':11}TO MASTERCARD{'':15}FROM MASTERCARD{'':17}CURRENCY AMOUNT{'':7}CODE{'':25}"),
        _pad(f" {'_'*8}{'':3}{'_'*28}{'':3}{'_'*28}{'':3}{'_'*23}{'':3}{'_'*7}{'':22}"),
    ]
    for cycle, file_id, paise in cycles:
        lines.append(_pad(
            f"  CYCLE {cycle:02d}{'':28}{file_id:<28}{'':14}{_fmt_amt(paise):>16} DR   356-INR{'':22}"
        ))
    lines += [
        _blank(),
        _pad(
            f"  TOTAL{'':50}{_fmt_amt(grand_total):>16} DR   356-INR{'':22}"
        ),
        _blank(),
        # Clearing day total
        _pad(f"1IP728010-AA{'':43}MASTERCARD WORLDWIDE{'':39}RUN DATE: {run_date}"),
        _pad(f"{'':58}CLEARING DAY TOTAL{'':27}RUN TIME: {run_time}"),
        _pad(f" MEMBER ID: {member_id_11}{'':42}{business_date_iso}{'':36}PAGE NO:         3"),
        _blank(),
        _pad(f" CLEARING DAY TOTAL AS OF CLEARING CYCLE  {len(cycles)}{'':89}"),
        _blank(),
        _pad(f"{'':14}RECONCILIATION{'':10}NET RECONCILIATION{'':88}"),
        _pad(f"{'':14}CURRENCY  CODE{'':11}CURRENCY AMOUNT{'':88}"),
        _pad(f"{'':14}{'-'*14}{'':10}{'-'*18}{'':88}"),
        _pad(f"{'':16}356-INR{'':17}{_fmt_amt(grand_total):>16} DR{'':70}"),
        _pad(f"{'':62}***END OF REPORT***{'':51}"),
        "",
    ]
    return lines


def generate(
    transactions: List[Transaction],
    config: dict,
    business_date: str,   # YYYYMMDD
    output_path: str,
) -> None:
    """Write T140 in real Mastercard report format."""
    member_id    = config.get("member_id", "021577")
    member_id_11 = member_id.zfill(11)
    net_txns     = [t for t in transactions if t.in_network]
    total_paise  = sum(t.amount for t in net_txns)
    fee_paise    = int(total_paise * 0.0131)

    # Date formatting
    yy   = business_date[2:4]
    mm   = business_date[4:6]
    dd   = business_date[6:8]
    yymmdd          = yy + mm + dd
    run_date        = f"{mm}/{dd}/{yy}"
    business_date_iso = f"{business_date[:4]}-{mm}-{dd}"
    run_time        = datetime.now().strftime("%H.%M.%S")

    # Two clearing cycles: 60% / 40%
    c1_paise  = int(total_paise * 0.6)
    c1_txns   = net_txns[:max(1, int(len(net_txns) * 0.6))]
    c2_paise  = total_paise - c1_paise
    c2_txns   = net_txns[len(c1_txns):]

    # File IDs
    fid1 = f"001/{yymmdd}/{member_id_11}/01101"
    fid2 = f"001/{yymmdd}/{member_id_11}/02201"

    all_lines = []

    # --- Cycle 1 ---
    all_lines += _section_1ip727010(run_date, 1, business_date_iso, member_id_11)
    if c1_txns:
        all_lines += _section_1ip727020_p1(run_date, 1, business_date_iso, member_id_11, fid1, c1_txns)
        all_lines += _section_1ip727020_p2(run_date, 1, business_date_iso, member_id_11, fid1, c1_paise, int(c1_paise * 0.0131))
        all_lines += _section_1ip728010_cycle(run_date, run_time, 1, business_date_iso, member_id_11, fid1, c1_paise)

    # --- Cycle 2 ---
    all_lines += _section_1ip727010(run_date, 2, business_date_iso, member_id_11)
    if c2_txns:
        all_lines += _section_1ip727020_p1(run_date, 2, business_date_iso, member_id_11, fid2, c2_txns)
        all_lines += _section_1ip727020_p2(run_date, 2, business_date_iso, member_id_11, fid2, c2_paise, int(c2_paise * 0.0131))
        all_lines += _section_1ip728010_cycle(run_date, run_time, 2, business_date_iso, member_id_11, fid2, c2_paise)

    # --- Summary + Clearing Day Total ---
    cycles = [(1, fid1, c1_paise), (2, fid2, c2_paise)]
    all_lines += _section_1ip728010_summary(run_date, run_time, business_date_iso, member_id_11, cycles)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(all_lines) + "\n")
