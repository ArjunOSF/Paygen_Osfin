"""
visa_epin_generator.py
======================
Visa BASE2 EPIN file generator.
Format: fixed-width 168 chars per TCR record.

Each transaction generates:
  TCR 0 — core transaction data (168 chars)
  TCR 5 — interchange fee data (168 chars)

Field positions from knowledge repo section 6.3 (1-indexed → 0-indexed):

TCR 0 (168 chars):
  pos 0–1    len 2   Transaction Code   "05"=purchase, "07"=ATM cash
  pos 2      len 1   TC Qualifier       "0"
  pos 3      len 1   TCR number         "0"
  pos 4–19   len 16  Account Number     PAN → ATM_C via account lookup
  pos 20–26  len 7   (gap)
  pos 26–48  len 23  ARN                Acquirer Reference Number (contains RRN)
  pos 49–57  len 8   (gap)
  pos 57–60  len 4   Purchase Date      MMDD
  pos 61–72  len 12  Destination Amount paise, zero-padded → ATM_C.TRAN_AMT
  pos 73–75  len 3   Destination Ccy    "356"=INR
  pos 76–87  len 12  Source Amount      same as destination (domestic)
  pos 88–90  len 3   Source Currency    "356"
  pos 91–115 len 25  Merchant Name
  pos 116–128 len 13 Merchant City
  pos 129–132 len 4  (gap)
  pos 132–135 len 4  MCC
  pos 136–146 len 11 (gap)
  pos 146    len 1   Usage Code         "1"=original, "9"=representment
  pos 147–148 len 2  Reason Code        "00"
  pos 149    len 1   Settlement Flag    "8"=domestic, "0"=international
  pos 150    len 1   (gap)
  pos 151–156 len 6  Authorization Code → PTLF.ApprovalCode
  pos 157–160 len 4  (gap)
  pos 161–162 len 2  POS Entry Mode     "05"=chip
  pos 163–167 len 5  (gap / Central Processing Date YDDD)

TCR 5 (168 chars) — minimal interchange fee data:
  pos 0–1    len 2   Transaction Code   "05"/"07" (same as TCR 0)
  pos 2      len 1   TC Qualifier       "0"
  pos 3      len 1   TCR number         "5"
  pos 4–18   len 15  Transaction Identifier
  pos 91–105 len 15  Interchange Fee Amount (minor units × 1000)
  rest: spaces
"""

from typing import List
from data_generator import Transaction

RECORD_LEN = 168


def _place(buf: list, value: str, start: int, length: int) -> None:
    value = str(value)[:length].ljust(length)
    for i, ch in enumerate(value):
        if start + i < RECORD_LEN:
            buf[start + i] = ch


def _build_arn(txn_rrn: str, acquiring_bin: str, date_mmdd: str) -> str:
    """
    Construct a 23-char ARN (Acquirer Reference Number).
    Format: AcquirerBIN(6) + date(4) + RRN(12) + check(1) = 23 chars.
    The RRN is embedded so ATM_C.RR_NO → Visa ARN join works.
    """
    acq_bin = acquiring_bin[:6].zfill(6)
    arn = (acq_bin + date_mmdd + txn_rrn + "0")[:23]
    return arn.ljust(23)


def _build_tcr0(txn: Transaction, tx_code: str, acquiring_bin: str) -> str:
    """Build TCR 0 — 168-char core transaction record."""
    buf = list(" " * RECORD_LEN)

    date_mmdd = txn.date_yymmdd[2:]                  # MMDD from YYMMDD
    arn       = _build_arn(txn.rrn, acquiring_bin, date_mmdd)

    _place(buf, tx_code,              0,  2)          # Transaction Code
    _place(buf, "0",                  2,  1)          # TC Qualifier
    _place(buf, "0",                  3,  1)          # TCR number = 0
    _place(buf, txn.pan,              4, 16)          # Account Number (PAN)
    # pos 20–25: gap (7 chars)
    _place(buf, arn,                 26, 23)          # ARN — contains RRN
    # pos 49–56: gap (8 chars)
    _place(buf, date_mmdd,           57,  4)          # Purchase Date MMDD
    _place(buf, str(txn.amount).zfill(12), 61, 12)   # Destination Amount (paise)
    _place(buf, "356",               73,  3)          # Destination Currency INR
    _place(buf, str(txn.amount).zfill(12), 76, 12)   # Source Amount (same = domestic)
    _place(buf, "356",               88,  3)          # Source Currency INR
    _place(buf, "TEST MERCHANT     ", 91, 25)         # Merchant Name
    _place(buf, "MUMBAI       ",     116, 13)         # Merchant City
    # pos 129–131: gap
    _place(buf, txn.mcc,            132,  4)          # MCC
    # pos 136–145: gap
    _place(buf, "1",                146,  1)          # Usage Code = original
    _place(buf, "00",               147,  2)          # Reason Code
    _place(buf, "8",                149,  1)          # Settlement Flag = domestic
    # pos 150: gap
    _place(buf, txn.approval_code,  151,  6)          # Authorization Code → PTLF.ApprovalCode
    # pos 157–160: gap
    _place(buf, "05",               161,  2)          # POS Entry Mode = chip
    # pos 163–167: gap (central processing date)

    return "".join(buf)


def _build_tcr5(txn: Transaction, tx_code: str) -> str:
    """Build TCR 5 — 168-char interchange fee record."""
    buf = list(" " * RECORD_LEN)

    interchange_fee = int(txn.amount * 0.007 * 1000)  # ~0.7% in units × 1000
    txn_id = (txn.rrn + txn.approval_code + "000")[:15]

    _place(buf, tx_code,              0,  2)
    _place(buf, "0",                  2,  1)
    _place(buf, "5",                  3,  1)          # TCR number = 5
    _place(buf, txn_id,               4, 15)          # Transaction Identifier
    _place(buf, str(interchange_fee).zfill(15), 91, 15)  # Interchange Fee Amount

    return "".join(buf)


def generate(
    transactions: List[Transaction],
    config: dict,
    channel: str,           # ATM | POS
    output_path: str,
) -> int:
    """Write Visa EPIN fixed-width file (TCR 0 + TCR 5 per transaction). Returns record count."""
    acquiring_bin = config.get("acquiring_ica", "008653")

    # Transaction code: "07"=ATM cash disbursement, "05"=POS purchase
    tx_code = "07" if channel.upper() == "ATM" else "05"

    written = 0
    with open(output_path, "w", encoding="utf-8") as f:
        for txn in transactions:
            if not txn.in_network:
                continue
            tcr0 = _build_tcr0(txn, tx_code, acquiring_bin)
            tcr5 = _build_tcr5(txn, tx_code)
            assert len(tcr0) == RECORD_LEN, f"EPIN TCR0 length {len(tcr0)} != {RECORD_LEN}"
            assert len(tcr5) == RECORD_LEN, f"EPIN TCR5 length {len(tcr5)} != {RECORD_LEN}"
            f.write(tcr0 + "\n")
            f.write(tcr5 + "\n")
            written += 1

    return written
