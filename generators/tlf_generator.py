"""
tlf_generator.py
================
BASE24 ATM Transaction Log File (TLF).
Format: fixed-width 574 chars per record.

Field positions (0-indexed, from knowledge repo section 2.1):

headx block (pos 0–88, 89 chars):
  pos 0      len 19  DateTime           (space-filled — actual Julian format not spec'd)
  pos 19     len 2   RecordType         "01"=customer txn
  pos 21     len 12  (gap)
  pos 33     len 16  TerminalID         → ATM_C.STATION_ID, EJ.ATM_ID
  pos 49     len 8   (gap)
  pos 57     len 19  CardPAN            → network PAN fields
  pos 76     len 3   (gap)
  pos 79     len 4   BranchID
  pos 83     len 4   RegionID
  pos 87     len 2   (gap)

authx block (pos 89–573, 485 chars):
  pos 89     len 2   (gap)
  pos 91     len 4   MessageType        "0210"=auth resp, "0420"=reversal
  pos 95     len 2   Status             "00"=success, "57"=fail
  pos 97     len 59  (gap)
  pos 156    len 6   TranDate           YYMMDD
  pos 162    len 8   TranTime           HHMMSSHH
  pos 170    len 6   PostDate           YYMMDD (= TranDate for simplicity)
  pos 176    len 12  (gap)
  pos 188    len 12  SequenceNumber     PRIMARY JOIN → ATM_C.SEQ_NO, EJ.TRXN_NO
  pos 200    len 7   (gap)
  pos 207    len 11  AcqInstIDNum
  pos 218    len 11  RcvInstIDNum
  pos 229    len 2   TranCode           "10"=withdrawal
  pos 231    len 44  (gap)
  pos 275    len 19  Amt1               amount in paise, zero-padded
  pos 294    len 19  Amt2               same as Amt1
  pos 313    len 31  (gap)
  pos 344    len 2   RespCode           "00" or "57"
  pos 346    len 65  (gap)
  pos 411    len 12  OriginalSeqNum     for reversals (space otherwise)
  pos 423    len 60  (gap)
  pos 483    len 2   ReversalReason     "00" normally
  pos 485    len 18  (gap)
  pos 503    len 6   AuthIDResp         transaction.approval_code → ATM_C.RR_NO
  pos 509    len 1   RefreshImpactInd   "1"=impact CBS
  pos 510    len 64  (gap)
Total: 574 chars
"""

from typing import List
from data_generator import Transaction

RECORD_LEN = 574


def _place(buf: list, value: str, start: int, length: int) -> None:
    value = str(value)[:length].ljust(length)
    for i, ch in enumerate(value):
        if start + i < RECORD_LEN:
            buf[start + i] = ch


def _build_record(txn: Transaction, config: dict) -> str:
    buf = list(" " * RECORD_LEN)

    # headx (0–88)
    _place(buf, "01",                      19,  2)   # RecordType
    _place(buf, txn.terminal_id,           33, 16)   # TerminalID
    _place(buf, txn.pan.ljust(19),         57, 19)   # CardPAN
    _place(buf, config.get("branch_id", "0001"), 79,  4)
    _place(buf, config.get("region_id",  "0001"), 83,  4)

    # authx (89–573)
    # Reversal (CHANGE 8): MTI 0420 for reversal records, 0210 otherwise
    msg_type = "0420" if txn.is_reversal else "0210"
    _place(buf, msg_type,                  91,  4)   # MessageType
    _place(buf, txn.switch_status,         95,  2)   # Status
    _place(buf, txn.date_yymmdd,          156,  6)   # TranDate YYMMDD
    time8 = (txn.time_hhmmss + "00")[:8]
    _place(buf, time8,                    162,  8)   # TranTime HHMMSSHH
    _place(buf, txn.date_yymmdd,          170,  6)   # PostDate
    _place(buf, txn.seq_no,               188, 12)   # SequenceNumber — PRIMARY JOIN KEY
    _place(buf, config.get("acq_inst_id", "00000001234"), 207, 11)
    _place(buf, config.get("rcv_inst_id", "00000005678"), 218, 11)
    _place(buf, "10",                     229,  2)   # TranCode = withdrawal
    amt_str = str(txn.amount).zfill(19)
    _place(buf, amt_str,                  275, 19)   # Amt1
    _place(buf, amt_str,                  294, 19)   # Amt2
    resp = txn.switch_status  # "00" or "57"
    _place(buf, resp,                     344,  2)   # RespCode
    # Reversal (CHANGE 8): OriginalSeqNum at pos 411 links to original txn's SEQ_NO
    if txn.is_reversal and txn.original_seq_no:
        _place(buf, txn.original_seq_no,  411, 12)
        _place(buf, "01",                 483,  2)   # ReversalReason "01"=cust cancellation
    else:
        _place(buf, "00",                 483,  2)   # ReversalReason (non-reversal)
    _place(buf, txn.approval_code,        503,  6)   # AuthIDResp → ATM_C.RR_NO
    _place(buf, "1",                      509,  1)   # RefreshImpactInd

    return "".join(buf)


def generate(
    transactions: List[Transaction],
    config: dict,
    output_path: str,
) -> int:
    """Write TLF fixed-width file. Returns count of records written."""
    written = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for txn in transactions:
            if not txn.in_switch:
                continue
            record = _build_record(txn, config)
            assert len(record) == RECORD_LEN, f"TLF record length {len(record)} != {RECORD_LEN}"
            f.write(record + "\n")
            written += 1
    return written
