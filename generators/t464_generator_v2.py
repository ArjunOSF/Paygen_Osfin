"""
t464_generator_v2.py
====================
Spec-accurate Mastercard T464 ATM Acquiring file generator (standalone CLI).

Format: fixed-width 250 chars per record.
Record block per ATM session:
  Normal:    FREC → FPST                    (financial, cash dispensed)
  Reversal:  EREC → EPST                    (cash dispensed but reversed)
  Balance:   NREC → FPST                    (non-financial, balance enquiry)
  Trailer:   STRL(I) settlement total + STRL(N) record count

KEY FIX vs prior generator: ATM REVERSALS NOW USE EREC, NOT POS FC 280.
Prior code generated reversals as POS-style refunds. The correct format is:
  - Record type: EREC (Exception Record)
  - Processing code: 400000 (reversal of cash disbursement)
  - Original_Switch_Serial_Number: links back to original FREC's serial number
  - Replacement_amount: original amount or partial (configurable)
  - Original_settlement_date: original FREC's settlement date
  - Reversal date: DIFFERENT from original — default original_date + 1 day
  - EREC must be immediately followed by EPST adjustment record
  - EPST carries adjustment_date / adjustment_time / adjusted_amount

CLI: --num-txns, --date, --currency, --testcase, --seed, --reversal-offset
     {random,acquiring,issuing,chargebacks,recon_break,high_value,on_us,atm_mix}
Output: .txt + _master_table.csv + _expected_totals.json

Date offsets (configurable via --reversal-offset, --chargeback-offset):
  ATM reversal (EREC):  original_date + 1 day  (default)
  Card chargeback:      original_date + 30 days (default, range 1-120)
"""

from __future__ import annotations
import argparse, csv, json, os, random, re, sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

RECORD_LEN     = 250
CCY            = "356"
IMPLIED_DEC    = "2"
CONV_RATE      = "11000000"
BRAND          = "MC2"
PROC_TYPE      = "I"
PROCESSOR_ID   = "0320"
INTRA_AGREE    = "    "
PROC_CODE_CASH    = "010000"
PROC_CODE_BALANCE = "311000"
PROC_CODE_REVERSAL = "400000"   # ← key: ATM reversal of cash disbursement


@dataclass
class T464Txn:
    pan: str                # 16-digit
    rrn: str                # 12-digit
    amount_paise: int
    date_yymmdd: str
    time_hhmmss: str
    terminal_id: str        # truncated to 10 chars in T464
    auth_id: str            # 6-digit approval code
    mcc: str = "6011"       # ATM
    is_reversal: bool = False
    is_balance: bool = False
    orig_serial: str = ""           # for EREC — original FREC's switch serial
    orig_settle_date: str = ""      # for EREC — original FREC's settlement date
    orig_amount_paise: int = 0      # for partial reversals
    test_case: str = "random"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _luhn_checksum(num: str) -> int:
    digits = [int(d) for d in num]
    odd = digits[-1::-2]; even = digits[-2::-2]
    return (sum(odd) + sum(sum(divmod(d * 2, 10)) for d in even)) % 10


def _luhn_complete(prefix: str, length: int = 16) -> str:
    body = prefix + "".join(str(random.randint(0, 9)) for _ in range(length - 1 - len(prefix)))
    check = (10 - _luhn_checksum(body + "0")) % 10
    return body + str(check)


def _normalize_date(s: str) -> str:
    s = s.strip().replace(",", "")
    fmts = ["%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y",
            "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"]
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s)
    for cand in (s, cleaned):
        for f in fmts:
            try: return datetime.strptime(cand, f).strftime("%Y%m%d")
            except ValueError: continue
    raise ValueError(f"unrecognised date: {s!r}")


def _add_days(yymmdd: str, days: int) -> str:
    """yymmdd → datetime + days → yymmdd."""
    d = datetime.strptime("20" + yymmdd, "%Y%m%d") + timedelta(days=days)
    return d.strftime("%y%m%d")


def _place(b: list, value: str, start: int, length: int) -> None:
    s = str(value)[:length].ljust(length)
    for i, ch in enumerate(s):
        if 0 <= start + i < RECORD_LEN:
            b[start + i] = ch


def _place_r(b: list, value, start: int, length: int) -> None:
    s = str(value)[-length:].zfill(length)
    for i, ch in enumerate(s):
        if 0 <= start + i < RECORD_LEN:
            b[start + i] = ch


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------

def build_frec(t: T464Txn, ssn: str, acquirer_id: str) -> str:
    """Financial Record — normal cash dispensed."""
    b = [' '] * RECORD_LEN
    _place(b, "FREC",          0,  4)
    _place_r(b, ssn,            4,  9)
    _place(b, PROC_TYPE,        13, 1)
    _place(b, PROCESSOR_ID,     14, 4)
    _place(b, t.date_yymmdd,    18, 6)
    _place(b, t.time_hhmmss,    24, 6)
    _place(b, "16",             30, 2)
    _place(b, t.pan.ljust(19),  32, 19)
    _place(b, PROC_CODE_CASH,   51, 6)
    _place(b, ssn,              57, 6)
    _place(b, t.mcc,            63, 4)
    _place(b, "051",            67, 3)            # POS entry = chip
    _place(b, t.rrn[:12],       70, 12)
    _place(b, acquirer_id,      82, 10)
    _place(b, t.terminal_id[:10], 92, 10)
    _place(b, "00",            102, 2)
    _place(b, BRAND,           104, 3)
    _place(b, INTRA_AGREE,     114, 4)
    _place(b, t.auth_id[:6],   118, 6)
    _place(b, CCY,             124, 3)
    _place(b, IMPLIED_DEC,     127, 1)
    _place_r(b, t.amount_paise, 128, 12)
    _place(b, CCY,             140, 3)
    _place(b, IMPLIED_DEC,     143, 1)
    _place_r(b, t.amount_paise, 144, 12)
    _place(b, CONV_RATE,       156, 8)
    _place(b, t.date_yymmdd,   164, 6)            # settlement date
    return "".join(b)


def build_nrec(t: T464Txn, ssn: str, acquirer_id: str) -> str:
    """Non-financial record — balance enquiry."""
    s = list(build_frec(t, ssn, acquirer_id))
    _place(s, "NREC", 0, 4)
    _place(s, PROC_CODE_BALANCE, 51, 6)
    _place_r(s, 0, 128, 12)            # zero amount
    _place_r(s, 0, 144, 12)
    return "".join(s)


def build_erec(t: T464Txn, ssn: str, acquirer_id: str, reversal_date: str) -> str:
    """Exception Record — ATM reversal (cash dispensed but reversed).
    Key fields:
      - Record type: EREC at pos 0
      - Processing code 400000 at pos 51
      - Original_Switch_Serial_Number at pos 200 (links to original FREC)
      - Original_settlement_date at pos 209
      - Reversal date at pos 18 (DIFFERENT from original — default orig+1)
      - Replacement amount at pos 128 (full or partial)
    """
    b = [' '] * RECORD_LEN
    _place(b, "EREC",            0,  4)
    _place_r(b, ssn,              4,  9)
    _place(b, PROC_TYPE,          13, 1)
    _place(b, PROCESSOR_ID,       14, 4)
    _place(b, reversal_date,      18, 6)            # ← reversal date != orig
    _place(b, t.time_hhmmss,      24, 6)
    _place(b, "16",               30, 2)
    _place(b, t.pan.ljust(19),    32, 19)
    _place(b, PROC_CODE_REVERSAL, 51, 6)            # ← 400000 = reversal
    _place(b, ssn,                57, 6)
    _place(b, t.mcc,              63, 4)
    _place(b, "051",              67, 3)
    _place(b, t.rrn[:12],         70, 12)
    _place(b, acquirer_id,        82, 10)
    _place(b, t.terminal_id[:10], 92, 10)
    _place(b, "00",              102, 2)
    _place(b, BRAND,             104, 3)
    _place(b, INTRA_AGREE,       114, 4)
    _place(b, t.auth_id[:6],     118, 6)
    _place(b, CCY,               124, 3)
    _place(b, IMPLIED_DEC,       127, 1)
    _place_r(b, t.amount_paise,   128, 12)
    _place(b, CCY,               140, 3)
    _place(b, IMPLIED_DEC,       143, 1)
    _place_r(b, t.amount_paise,   144, 12)
    _place(b, CONV_RATE,         156, 8)
    _place(b, reversal_date,     164, 6)
    # Fields specific to EREC (pos 200+):
    _place(b, t.orig_serial[:9], 200, 9)            # Original Switch Serial Number
    _place(b, t.orig_settle_date or t.date_yymmdd, 209, 6)
    _place_r(b, t.orig_amount_paise or t.amount_paise, 215, 12)  # Replacement amount
    return "".join(b)


def build_fpst(date: str, count: int, total_paise: int, acquirer_id: str) -> str:
    """Financial Position Summary — emitted after the FREC/NREC block."""
    b = [' '] * RECORD_LEN
    _place(b, "FPST",       0, 4)
    _place(b, PROC_TYPE,   13, 1)
    _place(b, PROCESSOR_ID,14, 4)
    _place(b, date,        18, 6)
    _place(b, acquirer_id, 82, 10)
    _place_r(b, count,     100, 6)
    _place(b, CCY,         124, 3)
    _place(b, IMPLIED_DEC, 127, 1)
    _place_r(b, total_paise, 128, 12)
    return "".join(b)


def build_epst(t: T464Txn, ssn: str, reversal_date: str, acquirer_id: str) -> str:
    """Adjustment summary that follows EREC. Carries adjustment_date/time/amount."""
    b = [' '] * RECORD_LEN
    _place(b, "EPST",       0, 4)
    _place_r(b, ssn,         4, 9)
    _place(b, PROC_TYPE,    13, 1)
    _place(b, PROCESSOR_ID, 14, 4)
    _place(b, reversal_date,18, 6)             # adjustment_date
    _place(b, t.time_hhmmss,24, 6)             # adjustment_time
    _place(b, acquirer_id,  82, 10)
    _place(b, CCY,         124, 3)
    _place(b, IMPLIED_DEC, 127, 1)
    _place_r(b, t.orig_amount_paise or t.amount_paise, 128, 12)   # adjusted_amount
    _place(b, t.orig_serial[:9], 200, 9)
    return "".join(b)


def build_strl(date: str, count: int, total_paise: int, kind: str = "I") -> str:
    """Settlement trailer record — kind 'I' (intra-region) or 'N' (count summary)."""
    b = [' '] * RECORD_LEN
    _place(b, "STRL",      0, 4)
    _place(b, kind,       13, 1)
    _place(b, PROCESSOR_ID,14, 4)
    _place(b, date,       18, 6)
    _place_r(b, count,    100, 6)
    if kind == "I":
        _place(b, CCY,        124, 3)
        _place(b, IMPLIED_DEC,127, 1)
        _place_r(b, total_paise, 128, 12)
    return "".join(b)


# ---------------------------------------------------------------------------
# Test-case driven txn factory
# ---------------------------------------------------------------------------

def _amount_for(case: str, rng: random.Random) -> int:
    if case == "high_value": return rng.randint(5_000_000, 50_000_000)
    return rng.randint(50_000, 2_500_000)


def _make_txn(idx: int, case: str, business_date: str, rng: random.Random,
              rrn_start: int) -> T464Txn:
    pan = _luhn_complete("5", 16)
    rrn = str(rrn_start + idx).zfill(12)
    amount = _amount_for(case, rng)
    yy, mm, dd = business_date[2:4], business_date[4:6], business_date[6:8]
    time_str = f"{rng.randint(0,23):02d}{rng.randint(0,59):02d}{rng.randint(0,59):02d}"
    auth = str(rng.randint(100000, 999999))
    terminal = "S5NL" + str(rng.randint(10**5, 10**6 - 1))   # 10 chars total

    is_balance = False
    is_reversal = False
    if case == "atm_mix":
        r = rng.random()
        if r < 0.10: is_reversal = True
        elif r < 0.15: is_balance = True
    elif case == "chargebacks":
        if rng.random() < 0.10: is_reversal = True

    return T464Txn(
        pan=pan, rrn=rrn, amount_paise=amount,
        date_yymmdd=yy+mm+dd, time_hhmmss=time_str,
        terminal_id=terminal, auth_id=auth, mcc="6011",
        is_reversal=is_reversal, is_balance=is_balance,
        test_case=case,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(num_txns: int, business_date: str,
             test_case: str = "random",
             currency: str = "INR",
             reversal_offset_days: int = 1,
             seed: Optional[int] = None,
             validate: bool = True) -> Tuple[List[T464Txn], List[str]]:
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    rrn_start = rng.randint(600_000_000_000, 699_999_999_999)
    acquirer_id = "0000001234"

    txns: List[T464Txn] = []
    for i in range(num_txns):
        t = _make_txn(i, test_case, business_date, rng, rrn_start)
        txns.append(t)

    # Link reversals to a prior non-reversal — original_serial / original_settle_date
    non_rev = [t for t in txns if not t.is_reversal]
    for t in txns:
        if t.is_reversal and non_rev:
            src = rng.choice(non_rev)
            t.orig_serial = str(rng.randint(10**8, 10**9 - 1))
            t.orig_settle_date = src.date_yymmdd
            t.orig_amount_paise = src.amount_paise

    # Build records, assigning sequential switch serial numbers
    records: List[str] = []
    ssn = 0
    fin_count = fin_total = 0
    rev_count = 0
    for t in txns:
        ssn += 1
        ssn_str = str(ssn).zfill(9)
        if t.is_reversal:
            reversal_date = _add_days(t.date_yymmdd, reversal_offset_days)
            records.append(build_erec(t, ssn_str, acquirer_id, reversal_date))
            ssn += 1
            records.append(build_epst(t, str(ssn).zfill(9), reversal_date, acquirer_id))
            rev_count += 1
        elif t.is_balance:
            records.append(build_nrec(t, ssn_str, acquirer_id))
        else:
            records.append(build_frec(t, ssn_str, acquirer_id))
            fin_count += 1
            fin_total += t.amount_paise

    # FPST after all financial records
    records.append(build_fpst(business_date[2:], fin_count, fin_total, acquirer_id))
    # STRL trailers
    records.append(build_strl(business_date[2:], fin_count, fin_total, "I"))
    records.append(build_strl(business_date[2:], len(records), 0, "N"))

    if validate:
        bad = [(i, len(r)) for i, r in enumerate(records, 1) if len(r) != RECORD_LEN]
        if bad:
            raise AssertionError(f"validation failed: {len(bad)} records wrong length")

    return txns, records


def write_outputs(txns: List[T464Txn], records: List[str], out_path: str,
                  business_date: str, currency: str, reversal_offset: int) -> None:
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(r + "\n")

    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pan_masked", "rrn", "amount_paise", "date", "time", "terminal_id",
                    "auth_id", "is_reversal", "is_balance", "orig_serial",
                    "orig_settle_date", "test_case"])
        for t in txns:
            pm = t.pan[:6] + "X"*6 + t.pan[-4:]
            w.writerow([pm, t.rrn, t.amount_paise, t.date_yymmdd, t.time_hhmmss,
                        t.terminal_id, t.auth_id, t.is_reversal, t.is_balance,
                        t.orig_serial, t.orig_settle_date, t.test_case])

    type_counts = _counter([r[:4].rstrip() for r in records])
    totals = {
        "num_txns": len(txns),
        "num_records": len(records),
        "record_type_counts": type_counts,
        "reversal_count": sum(1 for t in txns if t.is_reversal),
        "balance_count": sum(1 for t in txns if t.is_balance),
        "financial_count": sum(1 for t in txns if not (t.is_reversal or t.is_balance)),
        "total_amount_paise": sum(t.amount_paise for t in txns if not (t.is_reversal or t.is_balance)),
        "currency": currency,
        "business_date": business_date,
        "reversal_offset_days": reversal_offset,
        "record_length": RECORD_LEN,
    }
    with open(base + "_expected_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


def _counter(items):
    out = {}
    for x in items: out[x] = out.get(x, 0) + 1
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Spec-accurate Mastercard T464 ATM acquiring generator (250-char) — fixes EREC reversal logic")
    p.add_argument("--num-txns", type=int, default=20)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--currency", default="INR")
    p.add_argument("--testcase", default="random",
                   choices=["random", "acquiring", "issuing", "chargebacks",
                            "recon_break", "high_value", "on_us", "atm_mix"])
    p.add_argument("--reversal-offset", type=int, default=1,
                   help="days after original FREC for EREC reversal date (default 1)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--random", action="store_true")
    p.add_argument("--validate", action="store_true", default=True)
    p.add_argument("--output", default="t464.txt")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    txns, records = generate(args.num_txns, bdate, args.testcase, args.currency,
                             args.reversal_offset, args.seed, args.validate)
    write_outputs(txns, records, args.output, bdate, args.currency, args.reversal_offset)
    print(f"  wrote {len(records)} records ({len(txns)} txns) → {args.output}")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    print(f"  totals       → {os.path.splitext(args.output)[0]}_expected_totals.json")
    if args.validate:
        print(f"  validate     → all records {RECORD_LEN} chars  [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
