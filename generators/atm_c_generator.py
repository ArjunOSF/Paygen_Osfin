"""
atm_c_generator.py
===================
CBS Finacle ATM_C customer transaction file.
Format: fixed-width 186 chars per record.

Field positions (0-indexed, converted from 1-indexed spec):
  pos 0      len 1   BANK_CODE
  pos 1      len 5   BRANCH_CODE
  pos 6      len 9   JOURNAL_NO        (sequential, zero-padded)
  pos 15     len 8   TRAN_DATE         DDMMYYYY
  pos 23     len 8   POSTING_DATE      DDMMYYYY
  pos 31     len 14  (gap — space-filled)
  pos 45     len 12  SEQ_NO            PRIMARY JOIN → TLF/PTLF/EJ
  pos 57     len 12  RR_NO             SECONDARY JOIN → MC DE37, Visa ARN, RuPay nRRN
  pos 69     len 17  ACCT_NO
  pos 86     len 6   TRAN_CODE
  pos 92     len 1   CR_DR             D=debit C=credit
  pos 93     len 18  TRAN_AMT          right-aligned, zero-padded paise
  pos 111    len 16  STATION_ID        terminal → TLF.TerminalID, EJ.ATM_ID
  pos 127    len 50  STMT_NARRT
  pos 177    len 1   (gap)
  pos 178    len 2   ATM_POS_FLAG      CA=ATM, CP=POS
  pos 180    len 6   (gap to 186)
Total: 186 chars
"""

from typing import List
from data_generator import Transaction

RECORD_LEN = 186


def _place(buf: list, value: str, start: int, length: int) -> None:
    value = str(value)[:length].ljust(length)
    for i, ch in enumerate(value):
        if start + i < RECORD_LEN:
            buf[start + i] = ch


def _build_record(txn: Transaction, journal_no: int, config: dict, atm_pos_flag: str) -> str:
    buf = list(" " * RECORD_LEN)

    _place(buf, config.get("bank_code", "1"),        0,   1)
    _place(buf, config.get("branch_code", "00001"),  1,   5)
    _place(buf, str(journal_no).zfill(9),            6,   9)
    _place(buf, txn.date_ddmmyyyy,                  15,   8)
    _place(buf, txn.date_ddmmyyyy,                  23,   8)   # posting date = tran date
    # pos 31–44: gap (14 chars, space-filled)
    _place(buf, txn.seq_no,                         45,  12)   # PRIMARY JOIN KEY
    _place(buf, txn.rrn,                            57,  12)   # SECONDARY JOIN KEY → MC DE37
    _place(buf, txn.account_no,                     69,  17)
    _place(buf, config.get("tran_code", "020045"),  86,   6)
    _place(buf, "D",                                92,   1)   # CR_DR = Debit
    _place(buf, str(txn.amount).rjust(18, "0"),     93,  18)   # right-aligned paise
    _place(buf, txn.terminal_id,                   111,  16)   # STATION_ID = terminal
    narrative = f"ATM TXN {txn.seq_no} {txn.approval_code}"
    _place(buf, narrative,                         127,  50)
    # pos 177: gap (1 char)
    _place(buf, atm_pos_flag,                      178,   2)   # CA or CP
    # pos 180–185: gap (6 chars)

    return "".join(buf)


def generate(
    transactions: List[Transaction],
    config: dict,
    channel: str,           # ATM | POS
    output_path: str,
) -> int:
    """Write ATM_C fixed-width file. Returns count of records written."""
    atm_pos_flag = "CA" if channel.upper() == "ATM" else "CP"
    written = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for i, txn in enumerate(transactions):
            if not txn.in_cbs:
                continue
            record = _build_record(txn, journal_no=1000 + i, config=config, atm_pos_flag=atm_pos_flag)
            assert len(record) == RECORD_LEN, f"ATM_C record length {len(record)} != {RECORD_LEN}"
            f.write(record + "\n")
            written += 1
    return written
