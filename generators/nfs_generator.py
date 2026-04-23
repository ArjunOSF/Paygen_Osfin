"""
nfs_generator.py
================
NPCI NFS (National Financial Switch) clearing file generator.
Two file types: ISS (issuer) and ACQ (acquirer).

All positions are 1-indexed in the spec — converted to 0-indexed here.

NFS ISS — fixed-width 407 chars per record:
  Key join fields:
    Transaction Serial Number (pos 10, len 12) → ATM_C.RR_NO
    STAN                      (pos 50, len 12) → ATM_C.SEQ_NO  (PRIMARY)
    Actual Transaction Amount (pos 237, len 15) → ATM_C.TRAN_AMT

NFS ACQ — fixed-width 274 chars per record:
  Same join fields, shorter record (no issuer settlement block).
"""

from typing import List
from data_generator import Transaction

ISS_LEN = 407
ACQ_LEN = 274


def _place(buf: list, value: str, start: int, length: int) -> None:
    value = str(value)[:length].ljust(length)
    for i, ch in enumerate(value):
        if start + i < len(buf):
            buf[start + i] = ch


def _place_r(buf: list, value: str, start: int, length: int) -> None:
    """Right-align numeric value, zero-pad."""
    value = str(value)[:length].zfill(length)
    for i, ch in enumerate(value):
        if start + i < len(buf):
            buf[start + i] = ch


def _build_iss_record(txn: Transaction, config: dict) -> str:
    """Build NFS ISS record — 407 chars. Spec positions are 1-indexed."""
    buf = list(" " * ISS_LEN)
    participant_id = config.get("nfs_participant_id", "001")

    # All spec positions converted: pos_spec - 1 = 0-indexed
    _place  (buf, participant_id,                0,  3)   # Participant ID (1–3)
    _place  (buf, "31",                          3,  2)   # Transaction Type (4–5): 31=ATM withdrawal
    # pos 6–9: gap
    _place  (buf, txn.rrn,                       9, 12)   # Transaction Serial Number (10–21) → ATM_C.RR_NO
    resp = "00" if txn.switch_status == "00" else "05"
    _place  (buf, resp,                         21,  2)   # Response Code (22–23)
    _place  (buf, txn.pan.ljust(19),            23, 19)   # PAN Number (24–42)
    # pos 43–49: gap
    _place  (buf, txn.seq_no,                   49, 12)   # STAN (50–61) → ATM_C.SEQ_NO  PRIMARY JOIN
    _place  (buf, txn.date_yymmdd,              61,  6)   # Transaction Date (62–67)
    _place  (buf, txn.time_hhmmss,              67,  6)   # Transaction Time (68–73)
    _place  (buf, txn.mcc,                      73,  4)   # MCC (74–77): 6011=ATM
    # pos 78–98: gap
    _place  (buf, txn.terminal_id[:8],          98,  8)   # Card Acceptor Terminal ID (99–106)
    # pos 107–146: gap
    acq_id = config.get("acq_inst_id", "00000001234")[:11].ljust(11)
    _place  (buf, acq_id,                      146, 11)   # Acquirer ID (147–157)
    # pos 158–221: gap
    _place_r(buf, txn.amount,                  221, 15)   # Transaction Amount (222–236)
    _place_r(buf, txn.amount,                  236, 15)   # Actual Transaction Amount (237–251) → ATM_C.TRAN_AMT
    _place_r(buf, 0,                           251, 15)   # Transaction Activity Fee (252–266)
    _place  (buf, "356",                       266,  3)   # Issuer Settlement Currency (267–269)
    _place_r(buf, txn.amount,                  269, 15)   # Issuer Settlement Amount (270–284)
    _place_r(buf, 0,                           284, 15)   # Issuer Settlement Fee (285–299)
    # pos 300–407: gap / reserved

    return "".join(buf)


def _build_acq_record(txn: Transaction, config: dict) -> str:
    """Build NFS ACQ record — 274 chars. Spec positions are 1-indexed."""
    buf = list(" " * ACQ_LEN)
    participant_id = config.get("nfs_participant_id", "001")

    _place  (buf, participant_id,                0,  3)   # Participant ID (1–3)
    _place  (buf, "31",                          3,  2)   # Transaction Type (4–5)
    _place  (buf, txn.rrn,                       9, 12)   # Transaction Serial Number (10–21) → ATM_C.RR_NO
    resp = "00" if txn.switch_status == "00" else "05"
    _place  (buf, resp,                         21,  2)   # Response Code (22–23)
    _place  (buf, txn.pan.ljust(19),            23, 19)   # PAN Number (24–42)
    _place  (buf, txn.seq_no,                   49, 12)   # STAN (50–61) → ATM_C.SEQ_NO  PRIMARY JOIN
    _place  (buf, txn.date_yymmdd,              61,  6)   # Transaction Date (62–67)
    _place  (buf, txn.time_hhmmss,              67,  6)   # Transaction Time (68–73)
    _place  (buf, txn.mcc,                      73,  4)   # MCC (74–77)
    _place  (buf, txn.terminal_id[:8],          98,  8)   # Card Acceptor Terminal ID (99–106)
    acq_id = config.get("acq_inst_id", "00000001234")[:11].ljust(11)
    _place  (buf, acq_id,                      146, 11)   # Acquirer ID (147–157)
    _place_r(buf, txn.amount,                  166, 15)   # Transaction Amount (167–181)
    _place_r(buf, txn.amount,                  181, 15)   # Actual Transaction Amount (182–196) → ATM_C.TRAN_AMT
    _place_r(buf, 0,                           196, 15)   # Transaction Activity Fee (197–211)
    _place  (buf, "356",                       211,  3)   # Acquirer Settlement Currency (212–214)
    _place_r(buf, txn.amount,                  214, 15)   # Acquirer Settlement Amount (215–229)
    _place_r(buf, 0,                           229, 15)   # Acquirer Settlement Fee (230–244)

    return "".join(buf)


def generate(
    transactions: List[Transaction],
    config: dict,
    file_category: str,   # "ISS" | "ACQ"
    output_path: str,
) -> int:
    """Write NFS fixed-width file. Returns count of records written."""
    is_iss = file_category.upper() == "ISS"
    record_len = ISS_LEN if is_iss else ACQ_LEN
    build_fn = _build_iss_record if is_iss else _build_acq_record

    written = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for txn in transactions:
            if not txn.in_network:
                continue
            record = build_fn(txn, config)
            assert len(record) == record_len, \
                f"NFS {file_category} record length {len(record)} != {record_len}"
            f.write(record + "\n")
            written += 1
    return written
