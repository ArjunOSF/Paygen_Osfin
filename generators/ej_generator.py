"""
ej_generator.py
===============
Hyosung ATM Electronic Journal file.
Format: CSV pipe-delimited (|).

Key recon fields (knowledge repo section 4.0):
  TRXN_NO              → TLF.SequenceNumber, ATM_C.SEQ_NO  (PRIMARY JOIN)
  REF_NO               → ATM_C.RR_NO  (SECONDARY JOIN)
  ATM_ID               → TLF.TerminalID, ATM_C.STATION_ID
  CARD_NO              → PAN
  TRXN_FINAL_STATUS    → "Success" or "Fail"
  TRAN_AMOUNT          → actual dispensed → must match ATM_C.TRAN_AMT
"""

import csv
from typing import List
from data_generator import Transaction

FIELDNAMES = [
    "TRXN_NO", "REF_NO", "ATM_ID", "CARD_NO", "ACCOUNT_NUMBER",
    "AMOUNT", "TRAN_AMOUNT", "TRXN_FINAL_STATUS", "RESP",
    "TRXN_TYPE", "CARD_TYPE", "AVAIL_BALANCE",
]

CARD_TYPE_MAP = {
    "MC":    "MC",
    "VISA":  "Visa",
    "RUPAY": "RuPay",
    "NFS":   "RuPay",
}


def generate(
    transactions: List[Transaction],
    config: dict,
    output_path: str,
) -> int:
    """Write EJ pipe-delimited CSV. Returns count of records written."""
    written = 0
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, delimiter="|", lineterminator="\n")
        writer.writeheader()
        for txn in transactions:
            if not txn.in_ej:
                continue
            status = "Success" if txn.ej_success else "Fail"
            resp   = "00" if txn.ej_success else "099"
            # TRAN_AMOUNT = 0 if not dispensed (Fail), else equals AMOUNT
            tran_amount = txn.amount if txn.ej_success else 0
            writer.writerow({
                "TRXN_NO":           txn.seq_no,          # PRIMARY JOIN KEY
                "REF_NO":            txn.rrn,             # SECONDARY JOIN KEY → ATM_C.RR_NO
                "ATM_ID":            txn.terminal_id,
                "CARD_NO":           txn.pan,
                "ACCOUNT_NUMBER":    txn.account_no,
                "AMOUNT":            txn.amount,
                "TRAN_AMOUNT":       tran_amount,
                "TRXN_FINAL_STATUS": status,
                "RESP":              resp,
                "TRXN_TYPE":         "Withdrawal",
                "CARD_TYPE":         CARD_TYPE_MAP.get(txn.network.upper(), "MC"),
                "AVAIL_BALANCE":     "",
            })
            written += 1
    return written
