"""
mc_t464_generator.py
====================
Mastercard T464 ATM Acquiring file.
Ported from t464_generator_v2.py and adapted to accept Transaction objects.

Format: fixed-width 250 chars per record.
Record block per transaction: FREC/NREC/EREC → FPST/EPST → STRL(I) → STRL(N)

Key recon rule (knowledge repo section 5.8):
  Sum of FREC amounts = FPST settlement amount
  FREC count > FPST count → cash dispensed but settlement not received → raise claim

Adaptations vs original t464_generator_v2.py:
  - build_frec_nrec / build_erec accept transaction.pan, transaction.amount, transaction.rrn
  - terminal_id = transaction.terminal_id[:10]
  - in_network=False → generates EREC (exception/failed) records instead of FREC
"""

import random
import string
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from data_generator import Transaction

# ---------------------------------------------------------------------------
# Constants (from t464_generator_v2.py)
# ---------------------------------------------------------------------------

RECORD_LEN   = 250
CCY_TXN      = "356"
CCY_SETTLE   = "356"
IMPLIED_DEC  = "2"
CONV_RATE    = "11000000"
BRAND        = "MC2"
SVC_LEVEL    = "   "
PROC_TYPE    = "I"
PROCESSOR_ID = "0320"
ATM_MCCS     = ["6011", "6010", "6012", "6099"]
ATM_POS_ENTRIES = ["051", "011", "021"]
PROC_CODE_CASH    = "011000"
PROC_CODE_BALANCE = "311000"
INTRA_AGREE  = "C356"

BANK_BRANCHES = [
    ("ICICI TEST BANK      ", "MUMBAI          ", "IND", "ICICI TEST BANK"),
    ("HDFC LAB BRANCH      ", "NEW DELHI       ", "IND", "HDFC LAB BRANCH"),
    ("SBI TEST MAIN        ", "CHENNAI         ", "IND", "SBI TEST MAIN  "),
    ("AXIS TEST CENTRE     ", "KOLKATA         ", "IND", "AXIS TEST CENTRE"),
    ("KOTAK TEST BRANCH    ", "PUNE            ", "IND", "KOTAK TEST BRNCH"),
    ("YES BANK TEST        ", "HYDERABAD       ", "IND", "YES BANK TEST  "),
]


# ---------------------------------------------------------------------------
# Helpers (from t464_generator_v2.py — copied exactly)
# ---------------------------------------------------------------------------

def _place(b: list, value: str, start: int, length: int) -> None:
    value = str(value)[:length].ljust(length)
    for i, ch in enumerate(value):
        if start + i < RECORD_LEN:
            b[start + i] = ch

def zn(n, w):  return str(int(abs(n))).zfill(w)[:w]
def sp(s, w):  return str(s).ljust(w)[:w]
def rn(s, w):  return str(s).rjust(w)[:w]


# ---------------------------------------------------------------------------
# Record builders — adapted to accept Transaction fields
# ---------------------------------------------------------------------------

def build_frec_nrec(
    is_financial: bool,
    txn_pan: str,
    txn_amount: int,
    txn_rrn: str,        # 12-char → used as reference number
    txn_date: str,       # DDMMYY
    txn_time: str,       # HHMMSS
    terminal_id: str,    # 10-char
    auth_id: str,
    mcc: str,
    access_fee: int,
    interchange: int,
    rng: random.Random,
) -> str:
    tag = "FREC" if is_financial else "NREC"
    pan_len   = str(len(txn_pan.strip())).zfill(2)
    pan_field = txn_pan.ljust(19)[:19]
    amt_s  = zn(txn_amount, 12)
    fee_s  = zn(access_fee, 8)
    int_s  = zn(interchange, 10)
    set_s  = zn(txn_amount, 12)
    ref_num = txn_rrn[:8].ljust(12)           # rrn used as reference
    ssn     = "".join(str(rng.randint(0, 9)) for _ in range(9))
    trace   = "".join(str(rng.randint(0, 9)) for _ in range(6))
    acquirer_id = "1935632019"               # consistent acquirer

    b = list(" " * RECORD_LEN)
    _place(b, tag,          0,   4)
    _place(b, ssn,          4,   9)
    _place(b, PROC_TYPE,   13,   1)
    _place(b, PROCESSOR_ID,14,   4)
    _place(b, txn_date,    18,   6)
    _place(b, txn_time,    24,   6)
    _place(b, pan_len,     30,   2)
    _place(b, pan_field,   32,  19)
    _place(b, PROC_CODE_CASH if is_financial else PROC_CODE_BALANCE, 51, 6)
    _place(b, trace,       57,   6)
    _place(b, mcc,         63,   4)
    _place(b, "051",       67,   3)           # POS entry = chip
    _place(b, ref_num,     70,  12)           # Reference = rrn-derived
    _place(b, acquirer_id, 82,  10)
    _place(b, terminal_id, 92,  10)           # terminal_id[:10]
    _place(b, "00",       102,   2)
    _place(b, BRAND,      104,   3)
    _place(b, "       ",  107,   7)
    _place(b, INTRA_AGREE,114,   4)
    _place(b, auth_id,    118,   6)           # approval code
    _place(b, CCY_TXN,    124,   3)
    _place(b, IMPLIED_DEC,127,   1)
    _place(b, amt_s,      128,  12)
    _place(b, "D",        140,   1)
    _place(b, "000000000000",141,12)
    _place(b, "D",        153,   1)
    _place(b, fee_s,      154,   8)
    _place(b, "D",        162,   1)
    _place(b, CCY_SETTLE, 163,   3)
    _place(b, IMPLIED_DEC,166,   1)
    _place(b, CONV_RATE,  167,   8)
    _place(b, set_s,      175,  12)
    _place(b, "D",        187,   1)
    _place(b, int_s,      188,  10)
    _place(b, "D",        198,   1)
    _place(b, SVC_LEVEL,  199,   3)
    _place(b, "  ",       202,   2)
    _place(b, "          ",204, 10)
    _place(b, "4",        214,   1)
    _place(b, "N",        215,   1)
    _place(b, "N",        216,   1)
    _place(b, "N",        217,   1)
    _place(b, " ",        218,   1)
    _place(b, amt_s,      219,  12)
    _place(b, "            ",231,12)
    _place(b, "000000",   243,   6)
    _place(b, " ",        249,   1)
    return "".join(b)


def build_erec(
    txn_pan: str, txn_amount: int, txn_rrn: str,
    txn_date: str, txn_time: str, terminal_id: str,
    auth_id: str, mcc: str, interchange: int, rng: random.Random,
) -> str:
    pan_len    = str(len(txn_pan.strip())).zfill(2)
    pan_field  = txn_pan.ljust(19)[:19]
    orig_amt_s = zn(txn_amount, 12)
    repl_amt_s = zn(max(0, txn_amount - 1000), 12)
    int_s      = zn(interchange, 10)
    ref_num    = txn_rrn[:8].ljust(12)
    ssn        = "".join(str(rng.randint(0, 9)) for _ in range(9))
    orig_ssn   = "".join(str(rng.randint(0, 9)) for _ in range(9))
    orig_trace = "".join(str(rng.randint(0, 9)) for _ in range(6))
    acquirer_id = "1935632019"

    b = list(" " * RECORD_LEN)
    _place(b, "EREC",      0,   4)
    _place(b, orig_ssn,    4,   9)
    _place(b, PROC_TYPE,  13,   1)
    _place(b, PROCESSOR_ID,14,  4)
    _place(b, txn_date,   18,   6)
    _place(b, txn_time,   24,   6)
    _place(b, pan_len,    30,   2)
    _place(b, pan_field,  32,  19)
    _place(b, PROC_CODE_CASH, 51, 6)
    _place(b, orig_trace, 57,   6)
    _place(b, mcc,        63,   4)
    _place(b, "051",      67,   3)
    _place(b, ref_num,    70,  12)
    _place(b, acquirer_id,82,  10)
    _place(b, terminal_id,92,  10)
    _place(b, "00",      102,   2)
    _place(b, BRAND,     104,   3)
    _place(b, "       ", 107,   7)
    _place(b, INTRA_AGREE,114,  4)
    _place(b, auth_id,   118,   6)
    _place(b, CCY_TXN,   124,   3)
    _place(b, IMPLIED_DEC,127,  1)
    _place(b, orig_amt_s,128,  12)
    _place(b, "D",       140,   1)
    _place(b, "000000000000",141,12)
    _place(b, "D",       153,   1)
    _place(b, "00000000",154,   8)
    _place(b, "D",       162,   1)
    _place(b, CCY_SETTLE,163,   3)
    _place(b, IMPLIED_DEC,166,  1)
    _place(b, CONV_RATE, 167,   8)
    _place(b, orig_amt_s,175,  12)
    _place(b, "D",       187,   1)
    _place(b, int_s,     188,  10)
    _place(b, "D",       198,   1)
    _place(b, SVC_LEVEL, 199,   3)
    _place(b, "  ",      202,   2)
    _place(b, "  ",      204,   2)
    _place(b, repl_amt_s,206,  12)
    _place(b, "D",       218,   1)
    _place(b, repl_amt_s,219,  12)
    _place(b, "D",       231,   1)
    settle_date = txn_date
    _place(b, settle_date,232,  6)
    _place(b, "4",       238,   1)
    _place(b, "N",       239,   1)
    _place(b, "N",       240,   1)
    _place(b, "N",       241,   1)
    _place(b, "N",       242,   1)
    _place(b, "000000",  243,   6)
    _place(b, " ",       249,   1)
    return "".join(b)


def build_fpst(ssn_ref: str, bank: tuple, amount: int, proc_id_6: str, rng: random.Random) -> str:
    bank_name = sp(bank[0], 22)
    city      = sp(bank[1], 16)
    country   = sp(bank[2], 3)
    merch_id  = sp(bank[3], 15)
    amt_s     = zn(amount, 12)
    issuer_id = "".join(str(rng.randint(0, 9)) for _ in range(10))
    pos_data  = "10100100015"

    b = list(" " * RECORD_LEN)
    _place(b, "FPST",     0,   4)
    _place(b, ssn_ref,    4,   9)
    _place(b, pos_data,  13,  11)
    _place(b, issuer_id, 24,  10)
    _place(b, "19",      34,   2)
    _place(b, "0" * 19,  36,  19)
    _place(b, " " * 9,   55,   9)
    _place(b, "19",      64,   2)
    _place(b, "0" * 19,  66,  19)
    _place(b, " " * 9,   85,   9)
    _place(b, bank_name, 94,  22)
    _place(b, city,      116, 16)
    _place(b, country,   132,  3)
    _place(b, "N",       135,  1)
    _place(b, "N",       136,  1)
    _place(b, merch_id,  137, 15)
    _place(b, amt_s,     152, 12)
    _place(b, CCY_TXN,   164,  3)
    _place(b, "D",       167,  1)
    _place(b, IMPLIED_DEC,168, 1)
    _place(b, "   ",     169,  3)
    _place(b, proc_id_6, 172,  6)
    _place(b, " " * 11,  178, 11)
    _place(b, " " * 11,  189, 11)
    _place(b, " " * 15,  200, 15)
    _place(b, " " * 35,  215, 35)
    return "".join(b)


def build_strl(income: bool, amount: int, fee: int) -> str:
    ccy_type = "I" if income else "N"
    b = list(" " * RECORD_LEN)
    _place(b, "STRL",    0,  4)
    _place(b, "000",     4,  3)
    _place(b, "    ",    7,  4)
    _place(b, "356",    11,  3)
    _place(b, ccy_type, 14,  1)
    if income and amount > 0:
        _place(b, zn(amount, 14),  26, 14)
        _place(b, zn(1, 14),       40, 14)
        _place(b, zn(fee, 14),    112, 14)
        net_s = zn(amount + fee, 6)
        _place(b, f"00000000{net_s}D", 157, 16)
        _place(b, zn(amount, 18), 232, 18)
    else:
        _place(b, f"{'0'*15}D", 157, 16)
    return "".join(b)


def build_fhdr(business_date: str, processor_id_10: str) -> str:
    settle_date = business_date[2:]
    b = list(" " * RECORD_LEN)
    _place(b, "FHDR",         0,  4)
    _place(b, settle_date,    4,  6)
    _place(b, processor_id_10,10, 10)
    _place(b, "250",          20,  3)
    _place(b, "P",            23,  1)
    _place(b, "VERSION 16",   24, 10)
    return "".join(b)


def build_shdr() -> str:
    b = list(" " * RECORD_LEN)
    _place(b, "SHDR",  0,  4)
    _place(b, "000",   4,  3)
    _place(b, "    ",  7,  4)
    _place(b, "356",  11,  3)
    _place(b, "2",    14,  1)
    _place(b, "I",    15,  1)
    return "".join(b)


def build_ftrl(processor_id_10: str, total_recon_count: int) -> str:
    b = list(" " * RECORD_LEN)
    _place(b, "FTRL",               0,  4)
    _place(b, processor_id_10,      4, 10)
    _place(b, zn(total_recon_count, 11), 14, 11)
    return "".join(b)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate(
    transactions: List[Transaction],
    config: dict,
    business_date: str,
    output_path: str,
    seed: Optional[int] = None,
) -> int:
    """Write T464 fixed-width file. Returns count of FREC records written."""
    processor_id_10 = config.get("processor_id_10", "2123900000")
    proc_id_6       = processor_id_10[:6]
    rng             = random.Random(seed or 42)

    total_records = 0
    frec_count    = 0
    erec_count    = 0

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(build_fhdr(business_date, processor_id_10) + "\n"); total_records += 1
        f.write(build_shdr() + "\n");                               total_records += 1

        for i, txn in enumerate(transactions):
            # Date/time in DDMMYY / HHMMSS
            txn_date = txn.date_yymmdd[4:6] + txn.date_yymmdd[2:4] + txn.date_yymmdd[0:2]  # DDMMYY
            txn_time = txn.time_hhmmss
            terminal = txn.terminal_id[:10].ljust(10)
            auth_id  = txn.approval_code
            mcc      = txn.mcc if len(txn.mcc) == 4 else "6011"
            bank     = BANK_BRANCHES[i % len(BANK_BRANCHES)]
            amount   = txn.amount
            access_fee  = 0
            interchange = int(amount * 0.007)  # ~0.7% interchange
            ssn_ref = "".join(str(rng.randint(0, 9)) for _ in range(9))

            if not txn.in_network:
                # Network missing → EREC (exception record)
                f.write(build_erec(
                    txn.pan, amount, txn.rrn, txn_date, txn_time,
                    terminal, auth_id, mcc, interchange, rng,
                ) + "\n"); total_records += 1
                f.write(build_strl(True,  amount, access_fee) + "\n"); total_records += 1
                f.write(build_strl(False, amount, access_fee) + "\n"); total_records += 1
                erec_count += 1
            else:
                # FREC (financial transaction)
                f.write(build_frec_nrec(
                    True, txn.pan, amount, txn.rrn,
                    txn_date, txn_time, terminal, auth_id, mcc,
                    access_fee, interchange, rng,
                ) + "\n"); total_records += 1
                f.write(build_fpst(ssn_ref, bank, amount, proc_id_6, rng) + "\n"); total_records += 1
                f.write(build_strl(True,  amount, access_fee) + "\n"); total_records += 1
                f.write(build_strl(False, amount, access_fee) + "\n"); total_records += 1
                frec_count += 1

        f.write(build_ftrl(processor_id_10, total_records + 1) + "\n")
        total_records += 1

    return frec_count
