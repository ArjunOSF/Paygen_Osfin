"""
fss_ej_generator.py
===================
FSS Cards ATM Electronic Journal — free-text receipt format.
Confirmed from real IDFC ATM sample (IN201511, date 31/03/26).

Key recon fields:
  TXN NO → txn.seq_no  (PRIMARY JOIN → ATM_C.SEQ_NO)
  RRN    → txn.rrn     (SECONDARY JOIN → ATM_C.RR_NO)
"""

from typing import List
from data_generator import Transaction

SEP = "-" * 40


def _mask_pan(pan: str) -> str:
    if len(pan) >= 10:
        return pan[:6] + "*" * (len(pan) - 10) + pan[-4:]
    return pan


def _fmt_rupees(paise: int) -> str:
    return f"RS.{paise / 100:.2f}"


def _build_block(txn: Transaction, date_ddmmyy: str) -> str:
    pan_masked = _mask_pan(txn.pan)
    hh, mm = txn.time_hhmmss[:2], txn.time_hhmmss[2:4]
    time_str = f"{hh}:{mm}"

    lines = [
        "DATE        TIME    ATM ID",
        f"{date_ddmmyy}    {time_str}   {txn.terminal_id}",
        "",
        f"    CARD NUMBER:  {pan_masked}",
        "",
        f"    TXN NO:       {txn.seq_no}",
        "",
        "    #SAVINGS",
        "",
    ]

    if txn.ej_success:
        amt_str = _fmt_rupees(txn.amount)
        lines += [
            f"    WITHDRAWAL       {amt_str}",
            "    FROM A/C:",
            "    RESPONSE CODE    000",
            "    YOUR TXN IS SUCCESSFUL",
            "",
            f"    RRN.             {txn.rrn}",
            "",
            "    GO CASH FREE!USE DEBIT CARDS",
            "    NEVER SHARE YOUR CARD DETAILS",
            "    AND PIN WITH ANYONE",
        ]
    else:
        lines += [
            "    SORRY UNABLE TO PROCESS",
            "",
            "    RESPONSE CODE    100",
            "",
            f"    RRN.             {txn.rrn}",
        ]

    lines += [
        "",
        "    **TRANSACTION END**",
        SEP,
        "",
    ]
    return "\n".join(lines)


def generate(
    transactions: List[Transaction],
    config: dict,
    output_path: str,
) -> int:
    """Write FSS EJ free-text receipt file. Returns count of blocks written."""
    written = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for txn in transactions:
            if not txn.in_ej:
                continue
            yy = txn.date_yymmdd[:2]
            mm = txn.date_yymmdd[2:4]
            dd = txn.date_yymmdd[4:6]
            date_ddmmyy = f"{dd}/{mm}/{yy}"
            f.write(_build_block(txn, date_ddmmyy))
            written += 1
    return written
