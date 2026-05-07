"""
fss_gl_out_generator.py
=======================
FSS GL OUT (General Ledger Output) file generator.

Source: real FSS_GL_OUT.txt sample (30,233 records, IDFC switch output)

The GL output file from the FSS ATM switch — every transaction posts as a GL
entry against specific GL accounts. Used for ATM reconciliation and settlement.
Contains both Visa and Mastercard transactions.

File structure:
  3 header records (one per GL account) + N detail records
  Header (H): 6 pipe-delimited fields
  Detail (D): 23 pipe-delimited fields

Three GL accounts in real file:
  0000098094102017  VISA ATM PAYABLE A/C       (~265 records, ~1%)
  0000098095102016  VISA POS PAYABLE A/C       (~29,340 records, ~97%)
  0000098106102018  VISA ACQUIRING RECEIVABLE  (~625 records, ~2%)

Tran type distribution (real file):
  PRDR  Purchase Debit          26,309 (87%)
  OWDR  Own ATM Withdrawal         604 (2%)
  PRCR  Purchase Refund            597 (2%)
  CWDR  Cash Withdrawal Other     252
  OWCR  Own ATM Reversal           19
  CWRR  Cash Withdrawal Reversal    3
  (blank — generated cash posting) 2446 (8%)

Header (H) format:
  H|{date DDMMYYYY}|{GL_account}|{description}|{total_amount 17ch}|{DR or CR}

Detail (D) format (23 fields):
   0  D
   1  GL account
   2  branch_code "10201"
   3  user_id
   4  value_date DDMMYYYY
   5  posting_date DDMMYYYY
   6  DR or CR
   7  amount (17 chars zero-padded with 2 decimals)
   8  sequence_ref (9 digits)
   9  narration (free text — REF/ATM-VISA/<terminal>/<rrn>/<date>)
  10  filler
  11  filler
  12  filler
  13  network (VISA / blank)
  14  tran_type (PRDR/OWDR/PRCR/CWDR/OWCR/CWRR/blank)
  15  terminal_id (e.g. EN424011)
  16  rrn (12-digit) — KEY LINK to CBS/T112/EPIN
  17  time HHMMSS
  18-22  empty

CONTROL RULE: sum of all D amounts for each GL account = H record total amount
(with signs taken into account). The generator computes header totals from
generated detail records.
"""

from __future__ import annotations
import argparse, csv, json, os, random, sys
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

GL_ACCOUNTS = {
    "ATM": ("0000098094102017", "VISA ATM PAYABLE A/C",              "CR"),
    "POS": ("0000098095102016", "VISA POS PAYABLE A/C",              "CR"),
    "ACQ": ("0000098106102018", "VISA ACQUIRING RECEIVABLE",         "DR"),
    "NFS": ("0000098133102016", "NFS ISSUING PAYABLE-ATM/MATM BGL",  "CR"),
}

# Real-file ratios (rounded). Generator uses these by default; --testcase can shift.
DEFAULT_GL_RATIOS  = {"POS": 0.97, "ACQ": 0.02, "ATM": 0.01}

DEFAULT_TXN_RATIOS = {
    "PRDR": 0.87, "OWDR": 0.02, "PRCR": 0.02,
    "CWDR": 0.01, "OWCR": 0.001, "CWRR": 0.0005,
    "":     0.08,                     # blank tran_type for generated cash postings
}


@dataclass
class GlRec:
    gl_account: str
    branch_code: str
    user_id: str
    value_date: str          # DDMMYYYY
    posting_date: str        # DDMMYYYY
    dr_cr: str               # DR / CR
    amount_paise: int
    sequence_ref: str        # 9 digits
    narration: str
    network: str             # "VISA" or ""
    tran_type: str           # PRDR/OWDR/PRCR/CWDR/OWCR/CWRR / ""
    terminal_id: str
    rrn: str                 # 12-digit
    time_hhmmss: str         # HHMMSS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _ddmmyyyy(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}{yyyymmdd[4:6]}{yyyymmdd[:4]}"


def _amount_17(paise: int) -> str:
    """Format paise → 17-char zero-padded amount with 2 decimals (e.g. '00000000005000.00')."""
    rupees = paise / 100
    return f"{rupees:017.2f}"


def _amount_for(case: str, rng: random.Random, tran_type: str) -> int:
    if case == "high_value":
        return rng.randint(5_000_000, 50_000_000)
    if tran_type in ("OWDR", "OWCR", "CWDR", "CWRR"):
        # ATM amounts: rounded multiples of 100
        return rng.randint(50, 5000) * 10000
    return rng.randint(50_000, 2_500_000)


def _weighted_choice(rng: random.Random, weights: Dict[str, float]) -> str:
    keys = list(weights.keys()); vals = list(weights.values())
    return rng.choices(keys, weights=vals, k=1)[0]


def _gen_terminal_id(rng: random.Random, network: str) -> str:
    """Visa terminals look like EN801915 or S1CPN465 in the real file."""
    prefix = rng.choice(["EN", "S1", "S5", "PMT"])
    suffix = "".join(str(rng.randint(0, 9)) for _ in range(6))
    return f"{prefix}{suffix}"


def _make_narration(tran_type: str, terminal: str, rrn: str, date_ddmmyyyy: str) -> str:
    if not tran_type:
        return "GENERATED CASH POSTING"
    if tran_type in ("OWDR", "CWDR"):
        return f"/CASH WITHDRAWAL/{rrn}/{terminal}"
    if tran_type in ("OWCR", "CWRR"):
        return "ORIGINAL WITHDRAWAL  RVRSL"
    if tran_type == "PRCR":
        return "GENERATED CORR O/S CASH POSTING"
    # PRDR
    return f" REF/ATM-VISA/MERCHANT/{rrn}/{date_ddmmyyyy[:6]}"


# ---------------------------------------------------------------------------
# Record building
# ---------------------------------------------------------------------------

def _make_record(idx: int, business_date_yyyymmdd: str, network: str,
                 case: str, rng: random.Random,
                 rrn_start: int, gl_choice: str) -> GlRec:
    gl_account, _, _ = GL_ACCOUNTS[gl_choice]
    date_ddmmyyyy = _ddmmyyyy(business_date_yyyymmdd)

    # Choose tran type per test case
    if case == "atm_mix":
        weights = {"PRDR": 0.30, "OWDR": 0.30, "CWDR": 0.20, "PRCR": 0.10,
                   "OWCR": 0.05, "CWRR": 0.03, "": 0.02}
    elif case == "chargebacks":
        weights = {"PRDR": 0.50, "PRCR": 0.20, "OWCR": 0.10, "CWRR": 0.05,
                   "OWDR": 0.10, "CWDR": 0.05, "": 0.0}
    elif case in ("recon_break", "high_value"):
        weights = DEFAULT_TXN_RATIOS
    else:
        weights = DEFAULT_TXN_RATIOS

    tran_type = _weighted_choice(rng, weights)
    dr_cr = "DR" if tran_type in ("PRDR", "OWDR", "CWDR", "") else "CR"

    rrn = str(rrn_start + idx).zfill(12)
    terminal = _gen_terminal_id(rng, network)
    amount = _amount_for(case, rng, tran_type)
    seq = str(rng.randint(100_000_000, 999_999_999))
    hh = rng.randint(0, 23); mm = rng.randint(0, 59); ss = rng.randint(0, 59)
    time_str = f"{hh:02d}{mm:02d}{ss:02d}"
    narration = _make_narration(tran_type, terminal, rrn, date_ddmmyyyy)
    net = network.upper() if tran_type else ""

    return GlRec(
        gl_account=gl_account, branch_code="10201", user_id="9900001",
        value_date=date_ddmmyyyy, posting_date=date_ddmmyyyy,
        dr_cr=dr_cr, amount_paise=amount,
        sequence_ref=seq, narration=narration,
        network=net, tran_type=tran_type, terminal_id=terminal,
        rrn=rrn, time_hhmmss=time_str,
    )


def format_header(date_ddmmyyyy: str, gl_account: str, description: str,
                  total_paise: int, dr_cr: str) -> str:
    return f"H|{date_ddmmyyyy}|{gl_account}|{description}|{_amount_17(total_paise)}|{dr_cr}"


def format_detail(r: GlRec) -> str:
    return "|".join([
        "D", r.gl_account, r.branch_code, r.user_id,
        r.value_date, r.posting_date, r.dr_cr,
        _amount_17(r.amount_paise), r.sequence_ref,
        r.narration, "", "", "",
        r.network, r.tran_type, r.terminal_id, r.rrn, r.time_hhmmss,
        "", "", "", "", "",
    ])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(num_txns: int, business_date: str, network: str = "VISA",
             test_case: str = "random",
             seed: Optional[int] = None) -> Tuple[List[GlRec], List[str]]:
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    rrn_start = rng.randint(600_000_000_000, 699_999_999_999)

    # NFS issuer recon: single GL (98133102016), all CWDR/CWRR
    if network.upper() == "NFS":
        gl_choices = ["NFS"] * num_txns
    else:
        gl_choices = rng.choices(
            ["POS", "ACQ", "ATM"],
            weights=[DEFAULT_GL_RATIOS["POS"], DEFAULT_GL_RATIOS["ACQ"],
                     DEFAULT_GL_RATIOS["ATM"]],
            k=num_txns,
        )

    records: List[GlRec] = []
    for i in range(num_txns):
        records.append(_make_record(i, business_date, network, test_case,
                                    rng, rrn_start, gl_choices[i]))

    # Compute per-GL totals for headers
    totals: Dict[str, int] = {gl: 0 for gl in GL_ACCOUNTS}
    for r in records:
        for key, (acct, _, _) in GL_ACCOUNTS.items():
            if r.gl_account == acct:
                if network.upper() == "NFS":
                    # NFS spec: header total = sum of all detail amounts (no netting)
                    totals[key] += r.amount_paise
                else:
                    _, _, gl_dir = GL_ACCOUNTS[key]
                    signed = r.amount_paise if r.dr_cr == gl_dir else -r.amount_paise
                    totals[key] += signed
                break

    # Build output: only headers for GLs that received records
    date_ddmmyyyy = _ddmmyyyy(business_date)
    output_lines: List[str] = []
    active_gls = ["NFS"] if network.upper() == "NFS" else ["ATM", "POS", "ACQ"]
    for key in active_gls:
        acct, desc, dr_cr = GL_ACCOUNTS[key]
        net_total = abs(totals[key])
        output_lines.append(format_header(date_ddmmyyyy, acct, desc, net_total, dr_cr))

    for r in records:
        output_lines.append(format_detail(r))

    return records, output_lines


def write_outputs(records: List[GlRec], output_lines: List[str], out_path: str) -> None:
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        for l in output_lines:
            f.write(l + "\n")

    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["gl_account", "value_date", "dr_cr", "amount_paise", "sequence_ref",
                    "network", "tran_type", "terminal_id", "rrn", "time"])
        for r in records:
            w.writerow([r.gl_account, r.value_date, r.dr_cr, r.amount_paise,
                        r.sequence_ref, r.network, r.tran_type, r.terminal_id,
                        r.rrn, r.time_hhmmss])

    # Totals JSON
    totals_per_gl: Dict[str, Dict[str, int]] = {}
    for key, (acct, desc, dr_cr) in GL_ACCOUNTS.items():
        recs = [r for r in records if r.gl_account == acct]
        dr_sum = sum(r.amount_paise for r in recs if r.dr_cr == "DR")
        cr_sum = sum(r.amount_paise for r in recs if r.dr_cr == "CR")
        net    = (dr_sum - cr_sum) if dr_cr == "DR" else (cr_sum - dr_sum)
        totals_per_gl[acct] = {
            "description": desc, "direction": dr_cr,
            "record_count": len(recs),
            "dr_sum_paise": dr_sum, "cr_sum_paise": cr_sum,
            "net_total_paise": net,
        }

    summary = {
        "num_records": len(records),
        "total_amount_paise": sum(r.amount_paise for r in records),
        "tran_type_counts": _counter([r.tran_type for r in records]),
        "gl_breakdown": totals_per_gl,
    }
    with open(base + "_totals.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


def _counter(items):
    out = {}
    for x in items: out[x] = out.get(x, 0) + 1
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="FSS GL OUT generator (3 GL headers + N detail records)")
    p.add_argument("--num-txns", type=int, default=100)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--network", choices=["VISA", "MC", "NFS", "RUPAY", "BOTH"], default="VISA")
    p.add_argument("--testcase", default="random",
                   choices=["random", "acquiring", "issuing", "chargebacks",
                            "recon_break", "high_value", "on_us", "atm_mix"])
    p.add_argument("--seed", type=int, default=None,
                   help="reproducibility — RRN must match CBS/T112/EPIN with same seed")
    p.add_argument("--random", action="store_true")
    p.add_argument("--output", default="fss_gl_out.txt")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    records, lines = generate(args.num_txns, bdate, args.network, args.testcase, args.seed)
    write_outputs(records, lines, args.output)

    tt = _counter([r.tran_type for r in records])
    print(f"  wrote {len(lines)} lines (3 headers + {len(records)} details) → {args.output}")
    print(f"  network: {args.network}  tran_types: {tt}")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    print(f"  totals       → {os.path.splitext(args.output)[0]}_totals.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
