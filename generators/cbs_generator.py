"""
cbs_generator.py
================
CBS (Core Banking System) file generator — Mastercard + Visa combined.

Source: real CBSMCW.txt sample (Finacle / pipe-delimited internal ledger format)

File structure:
  Header: FH{YYYYMMDD}{6-digit sequence}        e.g. FH20260325000002
  Detail records: 17 pipe-delimited fields per transaction

Detail record fields (0-indexed):
   0  transaction_code   5-digit internal CBS code
   1  amount             rupees with 2 decimal places (e.g. 957.92)
   2  dr_cr              D = debit, C = credit
   3  pan_masked         "554534*******204" (first 6 + 7 stars + last 3)
   4  rrn                12-digit RRN — KEY LINK to T112/EPIN
   5  stan               14-digit System Trace Audit Number
   6  date               DD-MM-YYYY
   7  time               HH:MM:SS
   8  tran_type          PRDR/PRCR/OWDR/OWCR/CWDR/CWRR
   9  source             MDS (Mastercard Debit Switch) or VDS (Visa Debit Switch)
  10  filler             00000000
  11  sequence           9-digit internal sequence
  12  posting_date       DD-MM-YYYY
  13  flag1              0
  14  flag2              0
  15  flag3              0
  16  issuer_flag        I (issuer) or D

Network differences:
  source field:  MDS for Mastercard, VDS for Visa
  PAN prefix:    5 for Mastercard, 4 for Visa

Tran types:
  PRDR  Purchase Debit            (card at merchant)
  PRCR  Purchase Credit           (refund to customer)
  OWDR  Own-bank Withdrawal Debit (ATM cash)
  OWCR  Own-bank Withdrawal Credit (ATM reversal)
  CWDR  Cash Withdrawal Debit     (another bank's ATM)
  CWRR  Cash Withdrawal Reversal
"""

from __future__ import annotations
import argparse, csv, json, os, random, sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

NETWORK_SOURCE = {"MC": "MDS", "VISA": "VDS"}
PAN_PREFIX     = {"MC": "5", "VISA": "4"}


@dataclass
class CbsTxn:
    transaction_code: str    # 5 digits
    amount_paise: int        # internal storage; converted to rupees on emit
    dr_cr: str               # D/C
    pan: str                 # full PAN (will be masked)
    rrn: str                 # 12-digit
    stan: str                # 14-digit
    date_ddmmyyyy: str       # DD-MM-YYYY
    time_hhmmss: str         # HH:MM:SS
    tran_type: str           # PRDR/PRCR/OWDR/OWCR/CWDR/CWRR
    source: str              # MDS/VDS
    sequence: str            # 9-digit
    posting_date: str        # DD-MM-YYYY
    issuer_flag: str = "I"   # I or D
    network: str = "MC"
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
    fmts = ["%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y",
            "%d-%m-%y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"]
    import re
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s)
    for cand in (s, cleaned):
        for f in fmts:
            try: return datetime.strptime(cand, f).strftime("%Y%m%d")
            except ValueError: continue
    raise ValueError(f"unrecognised date: {s!r}")


def _mask_pan(pan: str) -> str:
    """Convert 16-digit PAN to '554534*******204' format (first 6 + 7 stars + last 3)."""
    return f"{pan[:6]}{'*'*7}{pan[-3:]}"


def _amount_rupees(paise: int) -> str:
    return f"{paise/100:.2f}"


def _amount_for(case: str, rng: random.Random) -> int:
    if case == "high_value": return rng.randint(5_000_000, 50_000_000)
    if case == "chargebacks": return rng.randint(10_000, 500_000)
    return rng.randint(50_000, 2_500_000)


def _ddmmyyyy(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}-{yyyymmdd[4:6]}-{yyyymmdd[:4]}"


# Internal CBS transaction codes — sample from real file
_TXN_CODES = ["21561", "41373", "20112", "40336", "43411", "80126", "80108",
              "40114", "80132", "21371", "21006", "20101", "21293", "21811",
              "42381", "20129", "40149", "60281", "21013", "21503", "20126",
              "42403"]


def _make_txn(idx: int, case: str, business_date: str, network: str,
              rng: random.Random, seq_start: int, stan_start: int) -> CbsTxn:
    pan = _luhn_complete(PAN_PREFIX[network], 16)

    # Pick tran type per test case
    tran_type = "PRDR"
    dr_cr = "D"
    issuer = "I"

    if case == "atm_mix":
        # Mix of ATM and POS
        choice = rng.choices(
            ["PRDR", "OWDR", "CWDR"],
            weights=[60, 25, 15], k=1
        )[0]
        tran_type = choice
        dr_cr = "D"
    elif case == "chargebacks":
        # 5% reversals + 5% refunds
        r = rng.random()
        if r < 0.05:
            tran_type = rng.choice(["OWCR", "CWRR"]); dr_cr = "C"
        elif r < 0.10:
            tran_type = "PRCR"; dr_cr = "C"
        else:
            tran_type = "PRDR"; dr_cr = "D"
    elif case == "on_us":
        tran_type = "OWDR" if rng.random() < 0.5 else "PRDR"
        dr_cr = "D"
    elif case == "issuing":
        # Issuer side — mostly debits
        issuer = "I"
        tran_type = "PRDR"; dr_cr = "D"
    elif case == "acquiring":
        issuer = "D"  # acquirer side — D flag
        tran_type = "PRDR"; dr_cr = "D"
    # random/recon_break/high_value default to PRDR

    amount = _amount_for(case, rng)
    rrn = str(seq_start + idx).zfill(12)
    stan = str(stan_start + idx).zfill(14)
    seq = str(rng.randint(100_000_000, 999_999_999))

    hh = rng.randint(0, 23); mm = rng.randint(0, 59); ss = rng.randint(0, 59)
    time_str = f"{hh:02d}:{mm:02d}:{ss:02d}"

    return CbsTxn(
        transaction_code=rng.choice(_TXN_CODES),
        amount_paise=amount, dr_cr=dr_cr, pan=pan, rrn=rrn, stan=stan,
        date_ddmmyyyy=_ddmmyyyy(business_date), time_hhmmss=time_str,
        tran_type=tran_type, source=NETWORK_SOURCE[network],
        sequence=seq, posting_date=_ddmmyyyy(business_date),
        issuer_flag=issuer, network=network, test_case=case,
    )


# ---------------------------------------------------------------------------
# Record formatting
# ---------------------------------------------------------------------------

def format_record(t: CbsTxn) -> str:
    return "|".join([
        t.transaction_code,
        _amount_rupees(t.amount_paise),
        t.dr_cr,
        _mask_pan(t.pan),
        t.rrn,
        t.stan,
        t.date_ddmmyyyy,
        t.time_hhmmss,
        t.tran_type,
        t.source,
        "00000000",
        t.sequence,
        t.posting_date,
        "0", "0", "0",
        t.issuer_flag,
    ])


def format_header(business_date: str, file_seq: int = 2) -> str:
    return f"FH{business_date}{file_seq:06d}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(num_txns: int, business_date: str, network: str = "MC",
             test_case: str = "random",
             seed: Optional[int] = None) -> Tuple[List[CbsTxn], List[str]]:
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    seq_start = rng.randint(600_000_000_000, 699_999_999_999)
    stan_start = rng.randint(10**13, 10**14 - 1)
    network = network.upper()
    if network not in NETWORK_SOURCE:
        raise ValueError(f"network must be MC or VISA, got {network!r}")

    txns = [_make_txn(i, test_case, business_date, network, rng, seq_start, stan_start)
            for i in range(num_txns)]
    records = [format_record(t) for t in txns]
    return txns, records


def write_outputs(txns: List[CbsTxn], records: List[str], out_path: str,
                  business_date: str) -> None:
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(format_header(business_date) + "\n")
        for r in records:
            f.write(r + "\n")

    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["transaction_code", "amount_paise", "dr_cr", "pan_masked",
                    "rrn", "stan", "date", "time", "tran_type", "source",
                    "sequence", "issuer_flag", "network", "test_case"])
        for t in txns:
            w.writerow([t.transaction_code, t.amount_paise, t.dr_cr, _mask_pan(t.pan),
                        t.rrn, t.stan, t.date_ddmmyyyy, t.time_hhmmss,
                        t.tran_type, t.source, t.sequence, t.issuer_flag,
                        t.network, t.test_case])


def _counter(items):
    out = {}
    for x in items: out[x] = out.get(x, 0) + 1
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="CBS pipe-delimited generator (MC + Visa)")
    p.add_argument("--network", choices=["MC", "VISA"], default="MC",
                   help="MC = Mastercard (MDS), VISA = Visa (VDS)")
    p.add_argument("--num-txns", type=int, default=10)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--testcase", default="random",
                   choices=["random", "acquiring", "issuing", "chargebacks",
                            "recon_break", "high_value", "on_us", "atm_mix"])
    p.add_argument("--seed", type=int, default=None,
                   help="reproducibility seed — RRN must match T112/EPIN with same seed")
    p.add_argument("--random", action="store_true")
    p.add_argument("--output", default="cbs.txt")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    txns, records = generate(args.num_txns, bdate, args.network, args.testcase, args.seed)
    write_outputs(txns, records, args.output, bdate)

    tt = _counter([t.tran_type for t in txns])
    print(f"  wrote {len(records)} records → {args.output}")
    print(f"  network: {args.network} ({NETWORK_SOURCE[args.network.upper()]})  "
          f"tran_types: {tt}")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
