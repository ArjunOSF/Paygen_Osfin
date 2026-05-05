"""
fig_b2c_generator.py
====================
FIG B2C TRAXN Report — AEPS / Micro-ATM transactions.
Source: real sample (comma-delimited CSV, MD5 trailer).

File structure:
  Header row (21 columns)
  N detail rows
  CHECKSUM|<32-hex-MD5-of-content-above>

Header columns (21):
  Source RRN, Gateway RRN, CBS Journal number, Transaction_time, branch_id,
  From_Account, To_Account, UID Number, Customer Name, AMOUNT, Commission Amount,
  Transaction Type, Status, Response_code, NPCI_Response_code, Narration,
  TERMINAL_ID, BC_ID, Acquirer Issuer bank, Original_STAN, Agent ID

Key fields:
  Gateway RRN  PRIMARY JOIN to NTSL via gateway RRN
  Source RRN   internal source reference
  CBS Journal  links to CBS file
  Status       S = Successful, F = Failed
  Response     000 = approved, 8787 = sample failure code

Transaction types:
  AEPS Offus Withdrawal-Iss / -Acq
  AEPS Offus Deposit-Iss / -Acq
  AEPS Offus CDA-Iss / -Acq    (Cash Deposit to Account)

Narration format:
  MATM-NFS/AEPS/CW/register/{Gateway_RRN}/Self,register,NA,{BC_name}     (withdrawal)
  MATM-NFS/AEPS/CD/register/{Gateway_RRN}/{Branch}/{...},...,{bank}      (deposit)
"""

from __future__ import annotations
import argparse, csv, hashlib, io, json, os, random, re, sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

HEADERS = [
    "Source RRN", "Gateway RRN", "CBS Journal number", "Transaction_time",
    "branch_id", "From_Account", "To_Account", "UID Number", "Customer Name",
    "AMOUNT", "Commission Amount", "Transaction Type", "Status",
    "Response_code", "NPCI_Response_code", "Narration", "TERMINAL_ID",
    "BC_ID", "Acquirer Issuer bank", "Original_STAN", "Agent ID",
]

TXN_TYPES = [
    "AEPS Offus Withdrawal-Iss",
    "AEPS Offus Withdrawal-Acq",
    "AEPS Offus Deposit-Iss",
    "AEPS Offus Deposit-Acq",
    "AEPS Offus CDA-Iss",
    "AEPS Offus CDA-Acq",
]

ACQ_BANKS = [
    "NSDL Payments Bank Limited",
    "Airtel Payments Bank",
    "India Post Payments Bank Limited",
    "Union Bank of India",
    "IDFC First Bank",
    "Fino Payments Bank",
    "Paytm Payments Bank",
]


@dataclass
class FigTxn:
    source_rrn: str          # 12-digit
    gateway_rrn: str         # 12-digit  ← KEY JOIN
    cbs_journal: str         # 7-digit
    txn_time: str            # DD-MM-YYYY HH:MM
    from_account: str
    to_account: str
    amount_rupees: float     # rupees with 2 decimals
    txn_type: str
    status: str              # S / F
    response_code: str       # 000 / 8787 etc
    gateway_rrn_in_narration: str
    terminal_id: str
    bc_id: str
    bank: str
    is_failure: bool


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


def _ddmmyyyy(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}-{yyyymmdd[4:6]}-{yyyymmdd[:4]}"


def _make_narration(t: FigTxn) -> str:
    """Build narration string per transaction type."""
    if "Withdrawal" in t.txn_type:
        return f"MATM-NFS/AEPS/CW/register/{t.gateway_rrn_in_narration}/Self"
    if "Deposit" in t.txn_type:
        return f"MATM-NFS/AEPS/CD/register/{t.gateway_rrn_in_narration}/Boring Road Branch/{t.from_account}/Self"
    if "CDA" in t.txn_type:
        return f"MATM-NFS/AEPS/CDA/register/{t.gateway_rrn_in_narration}/{t.bc_id}"
    return f"MATM-NFS/AEPS/register/{t.gateway_rrn_in_narration}"


def _make_txn(idx: int, business_date: str, rng: random.Random,
              source_start: int, gateway_start: int) -> FigTxn:
    src_rrn = str(source_start + idx).zfill(12)
    gw_rrn  = str(gateway_start + idx).zfill(12)
    cbs_j   = str(rng.randint(1_000_000, 9_999_999))
    hh = rng.randint(0, 23); mm = rng.randint(0, 59)
    time_str = f"{_ddmmyyyy(business_date)} {hh:02d}:{mm:02d}"
    from_acc = str(rng.randint(10_000_000_000, 99_999_999_999))
    to_acc   = "98112102011"
    amount   = rng.choice([200, 500, 1000, 1500, 2000, 2500, 3000, 5000, 10000])
    amount_r = float(amount)

    txn_type = rng.choice(TXN_TYPES)
    bank     = rng.choice(ACQ_BANKS)

    # 5% failure rate
    is_fail  = rng.random() < 0.05
    status   = "F" if is_fail else "S"
    resp     = "8787" if is_fail else "000"

    return FigTxn(
        source_rrn=src_rrn, gateway_rrn=gw_rrn, cbs_journal=cbs_j,
        txn_time=time_str, from_account=from_acc, to_account=to_acc,
        amount_rupees=amount_r, txn_type=txn_type,
        status=status, response_code=resp,
        gateway_rrn_in_narration=gw_rrn,
        terminal_id="register",
        bc_id=f"BC{rng.randint(10000, 99999)}",
        bank=bank, is_failure=is_fail,
    )


def _row_for_csv(t: FigTxn) -> List[str]:
    """Return CSV row matching HEADERS order. Many fields stay empty per real sample."""
    narration_full = _make_narration(t)
    # Real narration is split across 4 columns separated by commas — Narration / TERMINAL_ID / BC_ID / Bank
    # In real file: "MATM-NFS/AEPS/CW/register/<GW>/Self,register,NA,<bank>"
    # → narration is up to "/Self", terminal is "register", bc_id is "NA", bank is the bank name
    return [
        t.source_rrn,
        t.gateway_rrn,
        t.cbs_journal,
        t.txn_time,
        "",                                    # branch_id (empty in real sample)
        t.from_account,
        t.to_account,
        "",                                    # UID Number (empty)
        "",                                    # Customer Name (empty)
        f"{t.amount_rupees:.2f}",
        "",                                    # Commission Amount
        t.txn_type,
        t.status,
        t.response_code,
        "",                                    # NPCI_Response_code (empty)
        narration_full,                        # Narration
        t.terminal_id,                         # TERMINAL_ID = "register"
        "NA",                                  # BC_ID
        t.bank,                                # Acquirer Issuer bank
        "",                                    # Original_STAN
        "",                                    # Agent ID
    ]


def generate(num_txns: int, business_date: str,
             seed: Optional[int] = None) -> Tuple[List[FigTxn], str]:
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    source_start  = rng.randint(600_000_000_000, 699_999_999_999)
    gateway_start = rng.randint(600_000_000_000, 699_999_999_999)

    txns = [_make_txn(i, business_date, rng, source_start, gateway_start)
            for i in range(num_txns)]

    # Build CSV content in memory
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    w.writerow(HEADERS)
    for t in txns:
        w.writerow(_row_for_csv(t))
    content = buf.getvalue()

    # Append CHECKSUM line — MD5 of all content above (header + rows)
    md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
    full = content + f"CHECKSUM|{md5}\n"

    return txns, full


def write_outputs(txns: List[FigTxn], content: str, out_path: str,
                  business_date: str) -> None:
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source_rrn", "gateway_rrn", "cbs_journal", "txn_time",
                    "amount_rupees", "txn_type", "status", "response_code",
                    "bc_id", "bank", "is_failure"])
        for t in txns:
            w.writerow([t.source_rrn, t.gateway_rrn, t.cbs_journal, t.txn_time,
                        t.amount_rupees, t.txn_type, t.status, t.response_code,
                        t.bc_id, t.bank, t.is_failure])

    totals = {
        "business_date": business_date,
        "num_txns": len(txns),
        "successful_count": sum(1 for t in txns if t.status == "S"),
        "failed_count": sum(1 for t in txns if t.status == "F"),
        "total_amount_rupees": round(sum(t.amount_rupees for t in txns), 2),
        "txn_type_counts": _counter([t.txn_type for t in txns]),
        "checksum_line": "CHECKSUM|<md5>",
    }
    with open(base + "_expected_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


def _counter(xs):
    out = {}
    for x in xs: out[x] = out.get(x, 0) + 1
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="FIG B2C TRAXN report (AEPS/MATM CSV with MD5 trailer)")
    p.add_argument("--num-txns", type=int, default=100)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--random", action="store_true")
    p.add_argument("--validate", action="store_true", default=True)
    p.add_argument("--output", default="fig_b2c.csv")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    txns, content = generate(args.num_txns, bdate, args.seed)
    write_outputs(txns, content, args.output, bdate)

    print(f"  wrote {len(txns)} txns + 1 header + 1 CHECKSUM → {args.output}")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    print(f"  totals       → {os.path.splitext(args.output)[0]}_expected_totals.json")

    if args.validate:
        # Re-read and verify checksum
        with open(args.output) as f:
            data = f.read()
        idx = data.rfind("CHECKSUM|")
        if idx == -1:
            print("  validate     → CHECKSUM line missing  [FAIL]")
            return 2
        body = data[:idx]
        claimed = data[idx + len("CHECKSUM|"):].strip()
        actual = hashlib.md5(body.encode("utf-8")).hexdigest()
        ok = (claimed == actual)
        print(f"  validate     → MD5 checksum {'matches' if ok else 'MISMATCH'}  [{'OK' if ok else 'FAIL'}]")
        if not ok: return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
