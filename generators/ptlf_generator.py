"""
ptlf_generator.py
=================
BASE24 POS Transaction Log File (PTLF).
Format: fixed-width 2610 chars per record.

Field positions (0-indexed, from knowledge repo section 2.2):

headx block (pos 0–182, 183 chars):
  pos 0      len 19  DateTime           (space-filled)
  pos 19     len 2   RecordType         "01"=customer txn
  pos 21     len 8   (gap)
  pos 29     len 19  CardNumber         PAN → network, ATM_C via account lookup
  pos 48     len 19  (gap)
  pos 67     len 19  RetailerID         merchant ID
  pos 86     len 16  RetailerTerminalID terminal → ATM_C.STATION_ID
  pos 102    len 54  (gap)
  pos 156    len 1   RecFormat          "5"=financial transaction
  pos 157    len 25  (gap)
  pos 182    len 1   UserDataFieldFlag  "0"

authx block (pos 183–865, 683 chars):
  pos 183    len 4   AuthType           "0210"=auth resp, "0412"=chargeback, "0420"=reversal
  pos 187    len 2   Status             "00"=success, "57"=fail
  pos 189    len 2   IssuerCode         "00"=on-us, "30"=not-on-us (issuing)
  pos 191    len 59  (gap)
  pos 250    len 6   TranDate           YYMMDD
  pos 256    len 14  (gap)
  pos 270    len 6   AcqSettlDate       YYMMDD (= TranDate)
  pos 276    len 6   (gap)
  pos 282    len 12  SequenceNumber     PRIMARY JOIN → ATM_C.SEQ_NO
  pos 294    len 119 (gap)
  pos 413    len 4   RetailerSICCode    MCC
  pos 417    len 8   (gap)
  pos 425    len 2   TranCodeTC         "10"=purchase
  pos 427    len 4   (gap)
  pos 431    len 2   CardType           "MC"/"VI"/"RD"
  pos 433    len 19  (gap)
  pos 452    len 3   ResponseCode       "000"=approved, "057"=declined
  pos 455    len 19  Amount1            paise, zero-padded
  pos 474    len 19  Amount2            cashback/chargeback (zeros)
  pos 493    len 115 (gap)
  pos 608    len 8   ApprovalCode       → MC DE38, Visa pos 152–157
  pos 616    len 37  (gap)
  pos 653    len 2   ReasonForChargeback "00" normally
  pos 655    len 3   (gap)
  pos 658    len 3   PANEntryMode       "051"=chip, "021"=mag, "071"=contactless
  pos 661    len 45  (gap)
  pos 706    len 1   RefreshImpactInd   "1"
  pos 707    len 59  (gap)
  pos 766    len 12  OriginalSeqNum     for reversals (space otherwise)
  pos 778    len 88  (gap to pos 865)

pos 866–2609: (1744 chars — extended/EMV data, space-filled)
Total: 2610 chars
"""

from typing import List
from data_generator import Transaction

RECORD_LEN = 2610

CARD_TYPE_MAP = {
    "MC":    "MC",
    "VISA":  "VI",
    "RUPAY": "RD",
    "NFS":   "RD",
}


def _place(buf: list, value: str, start: int, length: int) -> None:
    value = str(value)[:length].ljust(length)
    for i, ch in enumerate(value):
        if start + i < RECORD_LEN:
            buf[start + i] = ch


def _build_record(txn: Transaction, config: dict, issuer_code: str, pos_type: str = "PHYSICAL") -> str:
    buf = list(" " * RECORD_LEN)

    # headx
    _place(buf, "01",                    19,  2)   # RecordType
    _place(buf, txn.pan.ljust(19),       29, 19)   # CardNumber
    merchant_id = f"MERCH{txn.terminal_id[:11]}"
    _place(buf, merchant_id,             67, 19)   # RetailerID
    _place(buf, txn.terminal_id,         86, 16)   # RetailerTerminalID
    _place(buf, "5",                    156,  1)   # RecFormat = financial
    _place(buf, "0",                    182,  1)   # UserDataFieldFlag

    # authx
    _place(buf, "0210",                 183,  4)   # AuthType
    _place(buf, txn.switch_status,      187,  2)   # Status "00" or "57"
    _place(buf, issuer_code,            189,  2)   # IssuerCode "00"=on-us, "30"=issuing
    _place(buf, txn.date_yymmdd,        250,  6)   # TranDate
    _place(buf, txn.date_yymmdd,        270,  6)   # AcqSettlDate
    _place(buf, txn.seq_no,             282, 12)   # SequenceNumber — PRIMARY JOIN KEY
    _place(buf, txn.mcc,                413,  4)   # RetailerSICCode (MCC)
    _place(buf, "10",                   425,  2)   # TranCodeTC = purchase
    _place(buf, CARD_TYPE_MAP.get(txn.network.upper(), "MC"), 431, 2)  # CardType
    resp_code = "000" if txn.switch_status == "00" else "057"
    _place(buf, resp_code,              452,  3)   # ResponseCode
    amt_str = str(txn.amount).zfill(19)
    _place(buf, amt_str,                455, 19)   # Amount1
    _place(buf, "0000000000000000000",  474, 19)   # Amount2 (no cashback)
    _place(buf, txn.approval_code.ljust(8), 608, 8)  # ApprovalCode → MC DE38
    _place(buf, "00",                   653,  2)   # ReasonForChargeback

    # PANEntryMode: 012=e-commerce, 051=chip, 071=contactless
    pan_entry = "012" if pos_type.upper() == "ECOM" else "051"
    _place(buf, pan_entry,              658,  3)   # PANEntryMode

    _place(buf, "1",                    706,  1)   # RefreshImpactInd

    return "".join(buf)


def generate(
    transactions: List[Transaction],
    config: dict,
    role: str,              # "ON-US" | "ISSUING"
    output_path: str,
    pos_type: str = "PHYSICAL",   # "PHYSICAL" | "ECOM"
) -> int:
    """Write PTLF fixed-width file. Returns count of records written."""
    issuer_code = "00" if role.upper() in ("ON-US", "ONUS") else "30"
    written = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for txn in transactions:
            if not txn.in_switch:
                continue
            record = _build_record(txn, config, issuer_code, pos_type)
            assert len(record) == RECORD_LEN, f"PTLF record length {len(record)} != {RECORD_LEN}"
            f.write(record + "\n")
            written += 1
    return written
