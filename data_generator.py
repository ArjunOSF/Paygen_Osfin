"""
data_generator.py
=================
Core transaction data model. Generates a shared list of Transaction objects
so every file generator for a scenario uses the same PAN, SEQ_NO, RRN,
amount, terminal ID, and date — enabling real join key consistency.
"""

import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional


# ---------------------------------------------------------------------------
# Luhn / PAN generation (ported from ipm_generator_v3.py)
# ---------------------------------------------------------------------------

def luhn_complete(partial: str) -> str:
    digits = [int(d) for d in partial + "0"]
    digits.reverse()
    total = sum(
        d * 2 - 9 if d * 2 > 9 else d * 2 if i % 2 == 1 else d
        for i, d in enumerate(digits)
    )
    return partial + str((10 - total % 10) % 10)


def gen_pan(network: str, rng: random.Random) -> str:
    """Generate 16-digit Luhn-valid PAN with correct network prefix."""
    prefix_map = {
        "MC":    "5" + str(rng.randint(1, 5)),
        "VISA":  "4",
        "RUPAY": "6",
        "NFS":   "6",
    }
    prefix = prefix_map.get(network.upper(), "5" + str(rng.randint(1, 5)))
    partial = prefix + "".join(str(rng.randint(0, 9)) for _ in range(14 - len(prefix)))
    return luhn_complete(partial)


def gen_approval_code(rng: random.Random) -> str:
    return "".join(rng.choices(string.digits, k=6))


def gen_account_no(pan: str, rng: random.Random) -> str:
    """Generate a plausible 17-char account number derived from PAN tail."""
    suffix = pan[-6:]
    prefix = str(rng.randint(10000, 99999))
    acct = (prefix + suffix + "000000").zfill(17)[:17]
    return acct


# ---------------------------------------------------------------------------
# Transaction dataclass
# ---------------------------------------------------------------------------

@dataclass
class Transaction:
    pan: str            # 16-digit Luhn-valid
    seq_no: str         # 12-digit sequential — primary join key across TLF/PTLF/ATM_C/EJ
    rrn: str            # 12-digit sequential — secondary join key → ATM_C.RR_NO, MC DE37, Visa ARN, RuPay nRRN
    amount: int         # minor units (paise for INR)
    terminal_id: str    # 16-char — truncate to 10 for T464, 8 for T112 DE41
    date_yymmdd: str    # YYMMDD for TLF/PTLF/network files
    date_ddmmyyyy: str  # DDMMYYYY for ATM_C
    time_hhmmss: str    # HHMMSS
    approval_code: str  # 6-digit — MC DE38, Visa pos 152–157, PTLF.ApprovalCode
    mcc: str            # 4-digit MCC
    network: str        # MC | VISA | RUPAY
    account_no: str     # 17-char CBS account number

    # Layer presence flags — set by scenario_engine
    in_switch: bool = True
    in_cbs: bool = True
    in_network: bool = True
    in_ej: bool = True
    ej_success: bool = True
    switch_status: str = "00"   # "00"=success, "57"=fail in TLF/PTLF authx.Status

    # Reversal fields (CHANGE 8)
    is_reversal: bool = False          # True → this record is a reversal/refund
    original_seq_no: str = ""          # SEQ_NO of the original txn being reversed
    original_rrn: str = ""             # RRN of the original txn being reversed
    reversal_day_offset: int = 0       # 0=same-day, 1=next-day (T+1)


# ---------------------------------------------------------------------------
# Batch generator
# ---------------------------------------------------------------------------

def generate_transactions(
    count: int,
    network: str,
    business_date: str,       # YYYYMMDD
    config: dict,
    seed: Optional[int] = None,
) -> List[Transaction]:
    """
    Generate `count` Transaction objects with sequential SEQ_NO and RRN.
    All fields are consistent across generators — do not re-randomise per file.
    """
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))

    base_dt = datetime.strptime(business_date, "%Y%m%d")
    date_yymmdd = business_date[2:]         # YYMMDD
    date_ddmmyyyy = base_dt.strftime("%d%m%Y")  # DDMMYYYY

    amount_min, amount_max = config.get("amount_range", [100000, 500000])
    terminal_prefix = config.get("terminal_id_prefix", "S5NL")

    # Build a small pool of terminals (same as acq_atm_data_new.py pattern)
    num_terminals = config.get("num_terminals", 6)
    terminals = [f"{terminal_prefix}{str(i).zfill(8-len(terminal_prefix))}"[:16] for i in range(1, num_terminals + 1)]

    # Start SEQ_NO and RRN at predictable offsets so multiple runs don't collide
    seq_start = int(rng.randint(100000000000, 899999999999))
    rrn_start  = int(rng.randint(100000000000, 899999999999))

    mccs = ["6011", "5999", "5812", "4814", "5411", "4722"]

    transactions = []
    for i in range(count):
        seq_no = str(seq_start + i).zfill(12)[-12:]
        rrn    = str(rrn_start  + i).zfill(12)[-12:]

        pan     = gen_pan(network, rng)
        amount  = rng.randint(amount_min, amount_max) // 100 * 100   # round to nearest rupee
        term    = rng.choice(terminals)
        mcc     = rng.choice(mccs)
        approval = gen_approval_code(rng)

        # Spread transaction times across the business day
        offset_sec  = rng.randint(0, 86399)
        txn_dt      = base_dt + timedelta(seconds=offset_sec)
        time_hhmmss = txn_dt.strftime("%H%M%S")

        acct = gen_account_no(pan, rng)

        transactions.append(Transaction(
            pan=pan,
            seq_no=seq_no,
            rrn=rrn,
            amount=amount,
            terminal_id=term,
            date_yymmdd=date_yymmdd,
            date_ddmmyyyy=date_ddmmyyyy,
            time_hhmmss=time_hhmmss,
            approval_code=approval,
            mcc=mcc,
            network=network,
            account_no=acct,
        ))

    return transactions
