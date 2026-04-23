"""
rupay_863_generator.py
======================
RuPay 863 RAW Data File — fixed-width format.
Confirmed from real sample (HDR260101161300260101IDFC7510001P01.00).

Spec source: RuPay .dat file structure (NPCI clearing spec).
All positions are 1-indexed; converted to 0-indexed here.

Header record (38 chars):
  pos  1– 3  HDR identifier        "HDR"
  pos  4– 9  Generated Date        YYMMDD
  pos 10–15  Generated Time        HHMMSS
  pos 16–21  Settlement Date       YYMMDD
  pos 22–32  Participant ID        11 chars
  pos 33–33  File Category         "P"=POS, "A"=ATM
  pos 34–38  Version               "01.00"

Data record (up to 340 chars, space-padded):
  pos   1– 2  Message_Type_Function  "01"=SMS, "02"=DMS, "04"=Reversal
  pos   3– 5  Product_Id             "POS" or "GDN" (ATM)
  pos   6– 7  Transaction_Type       "25"=Purchase SMS, "05"=Balance Enquiry
  pos   8– 9  From_Account_Type      "00"=default
  pos  10–11  To_Account_Type        "00"
  pos  12–12  Action_Code            "A"=Approved, "D"=Decline
  pos  13–14  Response_Code          "00"=approved, "05"=declined
  pos  15–33  PAN_Number             19 chars, zero-padded right
  pos  34–39  Approval_Number        6 chars  → ATM_C.RR_NO (first 6)
  pos  40–51  Retrieval_Reference_Number  12 chars → ATM_C.RR_NO  PRIMARY
  pos  52–58  Transaction_Date       MMDDYYY  (7 chars)
  pos  59–64  Transaction_Time       HHMMSS
  pos  65–68  Merchant_Category_Code 4 chars
  pos  69–83  Card_Acceptor_ID       15 chars
  pos  84–91  Card_Acceptor_Terminal_ID  8 chars
  pos  92–131 Card_Acceptor_Location 40 chars
  pos 132–142 Acquirer_ID            11 chars
  pos 143–145 Transaction_Currency   "356"
  pos 146–160 Transaction_Amount     15 chars, zero-padded → ATM_C.TRAN_AMT
  pos 161–163 Cardholder_Billing_Currency  "356"
  pos 164–178 Cardholder_Billing_Amount    15 chars
  pos 179–180 PAN_Entry_Mode         "51"=chip, "07"=contactless
  pos 181–181 PIN_Entry_Capability   "1"
  pos 182–183 POS_Condition_Code     "00"
  pos 184–186 Acquirer_Country_Code  "356"
  pos 187–340 (remaining fields — space-filled)

Footer (26 chars):
  pos  1– 3  TRL identifier
  pos  4–11  Number of records (8 chars, zero-padded)
  pos 12–26  Run Total Amount (15 chars, zero-padded)

Key recon joins:
  RRN  (pos 40–51, 12 chars) → ATM_C.RR_NO   (SECONDARY JOIN)
  Amount (pos 146–160, 15 chars) → ATM_C.TRAN_AMT
"""

from datetime import datetime
from typing import List
from data_generator import Transaction

REC_LEN = 340


def _place(buf: list, value: str, start: int, length: int) -> None:
    value = str(value)[:length].ljust(length)
    for i, ch in enumerate(value):
        if start + i < len(buf):
            buf[start + i] = ch


def _place_r(buf: list, value, start: int, length: int) -> None:
    value = str(value)[:length].zfill(length)
    for i, ch in enumerate(value):
        if start + i < len(buf):
            buf[start + i] = ch


def _build_header(business_date: str, participant_id: str, file_category: str = "P") -> str:
    now = datetime.now()
    gen_date = now.strftime("%y%m%d")
    gen_time = now.strftime("%H%M%S")
    yy = business_date[2:4]
    mm = business_date[4:6]
    dd = business_date[6:8]
    settl_date = yy + mm + dd
    pid = participant_id[:11].ljust(11)
    return f"HDR{gen_date}{gen_time}{settl_date}{pid}{file_category}01.00"


def _build_record(txn: Transaction, config: dict) -> str:
    buf = list(" " * REC_LEN)

    action = "A" if txn.switch_status == "00" else "D"
    resp   = "00" if txn.switch_status == "00" else "05"

    # 0-indexed (spec position - 1)
    _place  (buf, "01",                       0,  2)   # Message_Type_Function
    _place  (buf, "POS",                      2,  3)   # Product_Id
    _place  (buf, "25",                       5,  2)   # Transaction_Type (Purchase SMS)
    _place  (buf, "00",                       7,  2)   # From_Account_Type
    _place  (buf, "00",                       9,  2)   # To_Account_Type
    _place  (buf, action,                    11,  1)   # Action_Code
    _place  (buf, resp,                      12,  2)   # Response_Code
    _place  (buf, txn.pan.ljust(19),         14, 19)   # PAN (pos 15–33)
    _place  (buf, txn.approval_code[:6],     33,  6)   # Approval_Number
    _place  (buf, txn.rrn,                   39, 12)   # RRN → ATM_C.RR_NO  JOIN KEY

    # Transaction Date: MMDDYYY format (pos 52–58, 7 chars)
    yy = txn.date_yymmdd[:2]
    mm = txn.date_yymmdd[2:4]
    dd = txn.date_yymmdd[4:6]
    _place  (buf, f"{mm}{dd}20{yy}",         51,  7)   # Transaction_Date MMDDYYYY

    _place  (buf, txn.time_hhmmss,           58,  6)   # Transaction_Time
    _place  (buf, txn.mcc,                   64,  4)   # MCC
    acq_id  = config.get("acq_inst_id", "00000001234")[:15].ljust(15)
    _place  (buf, acq_id,                    68, 15)   # Card_Acceptor_ID
    _place  (buf, txn.terminal_id[:8],       83,  8)   # Terminal_ID
    _place  (buf, txn.terminal_id[:40],      91, 40)   # Location (terminal ID as location)
    pid     = config.get("acq_inst_id", "00000001234")[:11].ljust(11)
    _place  (buf, pid,                      131, 11)   # Acquirer_ID
    _place  (buf, "356",                    142,  3)   # Transaction_Currency
    _place_r(buf, txn.amount,              145, 15)   # Transaction_Amount → ATM_C.TRAN_AMT
    _place  (buf, "356",                    160,  3)   # Billing_Currency
    _place_r(buf, txn.amount,              163, 15)   # Billing_Amount
    _place  (buf, "51",                    178,  2)   # PAN_Entry_Mode (chip)
    _place  (buf, "1",                     180,  1)   # PIN_Entry_Capability
    _place  (buf, "00",                    181,  2)   # POS_Condition_Code
    _place  (buf, "356",                   183,  3)   # Acquirer_Country_Code

    return "".join(buf)


def generate(
    transactions: List[Transaction],
    config: dict,
    output_path: str,
    file_category: str = "P",   # "P"=POS, "A"=ATM
) -> int:
    """Write RuPay 863 raw file. Returns count of records written."""
    business_date = transactions[0].date_yymmdd if transactions else datetime.now().strftime("%y%m%d")
    # Convert YYMMDD to YYYYMMDD for _build_header
    bdate_full = "20" + business_date[:6] if len(business_date) == 6 else business_date

    participant_id = config.get("rupay_member_id", "IDFC7510001")

    written = 0
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(_build_header(bdate_full, participant_id, file_category) + "\n")

        for txn in transactions:
            if not txn.in_network:
                continue
            record = _build_record(txn, config)
            assert len(record) == REC_LEN, f"RuPay 863 record length {len(record)} != {REC_LEN}"
            f.write(record + "\n")
            written += 1

        # Footer
        total_amount = sum(t.amount for t in transactions if t.in_network)
        f.write(f"TRL{written:08d}{total_amount:015d}\n")

    return written
