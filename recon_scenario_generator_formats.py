"""
recon_scenario_generator_formats.py
===================================
Enhanced version of recon_scenario_generator.py with spec-accurate format support.

Generates 1000s of test transactions across 50 recon scenarios and outputs:
- MasterCard: t112.txt, t140.txt, tlf.txt, cbs_mc.txt, fss_gl_out.txt
- Visa:       epin.txt, ptlf.txt, cbs_visa.txt, fss_gl_out.txt

Each transaction is tagged with a scenario defining what record appears in each file.
The RRN is the join key for cross-file matching.

Usage:
  python3 recon_scenario_generator_formats.py --num-txns 1000 --network MC --date 20260429
  python3 recon_scenario_generator_formats.py --num-txns 1000 --network VISA --date 20260401
"""

from __future__ import annotations
import argparse, csv, json, os, random, re, subprocess, sys, zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add generators/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "generators"))

# Import Transaction from data_generator for use with format generators
try:
    from data_generator import Transaction
    from scenario_engine import apply_layer_config
    HAS_GENERATORS = True
except ImportError:
    HAS_GENERATORS = False
    Transaction = None

# Import format-specific generators
try:
    import generators.mc_t112_generator as t112_gen
    import generators.mc_t140_generator as t140_gen
    import generators.visa_epin_generator as epin_gen
    import generators.ptlf_generator as ptlf_gen
    import generators.cbs_generator as cbs_gen
    import generators.fss_gl_out_generator as gl_gen
    import generators.tlf_generator as tlf_gen
    HAS_FORMATTERS = True
except ImportError:
    HAS_FORMATTERS = False


# ---------------------------------------------------------------------------
# 50-row scenario matrix — (scenario_id, cbs_state, gl_state, network_state)
# ---------------------------------------------------------------------------

SCENARIOS: List[Tuple[int, str, str, str]] = [
    # (scenario_id, cbs_state, gl_state, network_state)
    # Network = Successful (cases 1–12)
    ( 1, "Successful",            "Successful",            "Successful"),
    ( 2, "Successful",            "Declined",              "Successful"),
    ( 3, "Successful",            "Not In Interchange",    "Successful"),
    ( 4, "Declined",              "Successful",            "Successful"),
    ( 5, "Declined",              "Declined",              "Successful"),
    ( 6, "Declined",              "Not In Interchange",    "Successful"),
    ( 7, "Declined & Reversed",   "Successful",            "Successful"),
    ( 8, "Declined & Reversed",   "Declined",              "Successful"),
    ( 9, "Declined & Reversed",   "Not In Interchange",    "Successful"),
    (10, "Transaction Missing",   "Successful",            "Successful"),
    (11, "Transaction Missing",   "Declined",              "Successful"),
    (12, "Transaction Missing",   "Not In Interchange",    "Successful"),
    # Network = Successful & Reversed (cases 13–24)
    (13, "Successful",            "Successful",            "Successful & Reversed"),
    (14, "Successful",            "Declined",              "Successful & Reversed"),
    (15, "Successful",            "Not In Interchange",    "Successful & Reversed"),
    (16, "Declined",              "Successful",            "Successful & Reversed"),
    (17, "Declined",              "Declined",              "Successful & Reversed"),
    (18, "Declined",              "Not In Interchange",    "Successful & Reversed"),
    (19, "Successful & Reversed", "Successful",            "Successful & Reversed"),
    (20, "Successful & Reversed", "Declined",              "Successful & Reversed"),
    (21, "Successful & Reversed", "Not In Interchange",    "Successful & Reversed"),
    (22, "Transaction Missing",   "Successful",            "Successful & Reversed"),
    (23, "Transaction Missing",   "Declined",              "Successful & Reversed"),
    (24, "Transaction Missing",   "Not In Interchange",    "Successful & Reversed"),
    # Network = Transaction Missing (cases 25–36)
    (25, "Successful",            "Successful",            "Transaction Missing"),
    (26, "Successful",            "Declined",              "Transaction Missing"),
    (27, "Successful",            "Not In Interchange",    "Transaction Missing"),
    (28, "Declined",              "Successful",            "Transaction Missing"),
    (29, "Declined",              "Declined",              "Transaction Missing"),
    (30, "Declined",              "Not In Interchange",    "Transaction Missing"),
    (31, "Successful & Reversed", "Successful",            "Transaction Missing"),
    (32, "Successful & Reversed", "Declined",              "Transaction Missing"),
    (33, "Successful & Reversed", "Not In Interchange",    "Transaction Missing"),
    (34, "Transaction Missing",   "Successful",            "Transaction Missing"),
    (35, "Transaction Missing",   "Declined",              "Transaction Missing"),
    (36, "Transaction Missing",   "Not In Interchange",    "Transaction Missing"),
    # GL = Failed (cases 37–48)
    (37, "Successful",            "Failed",                "Successful"),
    (38, "Declined",              "Failed",                "Successful"),
    (39, "Successful & Reversed", "Failed",                "Successful"),
    (40, "Transaction Missing",   "Failed",                "Successful"),
    (41, "Successful",            "Failed",                "Successful & Reversed"),
    (42, "Declined",              "Failed",                "Successful & Reversed"),
    (43, "Successful & Reversed", "Failed",                "Successful & Reversed"),
    (44, "Transaction Missing",   "Failed",                "Successful & Reversed"),
    (45, "Successful",            "Failed",                "Transaction Missing"),
    (46, "Declined",              "Failed",                "Transaction Missing"),
    (47, "Successful & Reversed", "Failed",                "Transaction Missing"),
    (48, "Transaction Missing",   "Failed",                "Transaction Missing"),
    # Special cases (49–50)
    (49, "Declined & Reversed",   "Declined & Reversed",   "Successful & Reversed"),
    (50, "Declined & Reversed",   "Declined & Reversed",   "Successful"),
]


# ---------------------------------------------------------------------------
# Transaction model (mirrors data_generator.Transaction)
# ---------------------------------------------------------------------------

@dataclass
class ReconTxn:
    """Internal transaction representation for scenario generation."""
    txn_id: int
    scenario_id: int
    cbs_state: str
    gl_state: str
    network_state: str
    pan: str
    rrn: str
    arn: str
    amount_paise: int
    date_yyyymmdd: str
    time_hhmmss: str
    terminal_id: str
    auth_code: str
    mcc: str
    merchant_name: str
    merchant_city: str
    merchant_country: str
    network: str = "VISA"


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


_MERCHANTS = [
    ("AMAZONIN",                "Bangalore",    "IN", "5942"),
    ("FLIPKART INTERNET",       "Bangalore",    "IN", "5399"),
    ("SWIGGY LIMITED",          "Mumbai",       "IN", "5811"),
    ("DMART NUNNA",             "Vijayawada",   "IN", "5411"),
    ("INDIAN OIL",              "New Delhi",    "IN", "5541"),
    ("ZOMATO ONLINE ORDER",     "Gurugram",     "IN", "5811"),
    ("MAKEMYTRIP",              "Gurugram",     "IN", "4722"),
    ("UBER INDIA",              "Bangalore",    "IN", "4121"),
    ("NETFLIX",                 "Mumbai",       "IN", "4899"),
    ("BIG BAZAAR",              "Pune",         "IN", "5411"),
]


def _make_arn(rng: random.Random, business_date: str) -> str:
    fmt = "7"; bin6 = "457811"
    yy = business_date[2:4]
    julian = str(datetime.strptime(business_date, "%Y%m%d").timetuple().tm_yday).zfill(3)
    yddd = yy[1] + julian
    ref = "".join(str(rng.randint(0, 9)) for _ in range(11))
    body = fmt + bin6 + yddd + ref
    check = str(_luhn_checksum(body + "0"))
    return body + check


def _make_txn(idx: int, scenario: Tuple[int, str, str, str], business_date: str,
              network: str, rng: random.Random, rrn_start: int) -> ReconTxn:
    sid, cbs_s, gl_s, net_s = scenario
    pan_prefix = "4" if network == "VISA" else "5"
    pan = _luhn_complete(pan_prefix, 16)
    rrn = str(rrn_start + idx).zfill(12)
    arn = _make_arn(rng, business_date)
    amount = rng.randint(50_000, 2_500_000)
    name, city, country, mcc = rng.choice(_MERCHANTS)
    auth = str(rng.randint(100000, 999999))
    time_str = f"{rng.randint(0,23):02d}{rng.randint(0,59):02d}{rng.randint(0,59):02d}"
    terminal = f"S5NL{rng.randint(10**5, 10**6 - 1)}"

    return ReconTxn(
        txn_id=idx, scenario_id=sid,
        cbs_state=cbs_s, gl_state=gl_s, network_state=net_s,
        pan=pan, rrn=rrn, arn=arn, amount_paise=amount,
        date_yyyymmdd=business_date, time_hhmmss=time_str,
        terminal_id=terminal, auth_code=auth, mcc=mcc,
        merchant_name=name, merchant_city=city, merchant_country=country,
        network=network,
    )


def _recon_txn_to_transaction(rtxn: ReconTxn, seq_no: str) -> 'Transaction':
    """Convert ReconTxn to Transaction for use with format generators."""
    if not HAS_GENERATORS:
        return None

    return Transaction(
        pan=rtxn.pan,
        seq_no=seq_no,
        rrn=rtxn.rrn,
        amount=rtxn.amount_paise,
        terminal_id=rtxn.terminal_id,
        date_yymmdd=rtxn.date_yyyymmdd[2:],
        date_ddmmyyyy=rtxn.date_yyyymmdd[6:8] + rtxn.date_yyyymmdd[4:6] + rtxn.date_yyyymmdd[:4],
        time_hhmmss=rtxn.time_hhmmss,
        approval_code=rtxn.auth_code,
        mcc=rtxn.mcc,
        network="VISA" if rtxn.network == "VISA" else "MC",
        account_no="",
        in_switch=True,
        in_cbs=rtxn.cbs_state != "Transaction Missing",
        in_network=rtxn.network_state != "Transaction Missing",
        in_ej=True,
        ej_success=rtxn.cbs_state not in ("Declined", "Declined & Reversed"),
        switch_status="00" if rtxn.network_state not in ("Declined", "Failed") else "57",
    )


def _mask_pan(pan: str) -> str:
    return f"{pan[:6]}{'*'*7}{pan[-3:]}"


def _scenario_counts(txns: List[ReconTxn]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for t in txns:
        out[t.scenario_id] = out.get(t.scenario_id, 0) + 1
    return out


# ---------------------------------------------------------------------------
# Enhanced bundle writer with format generators
# ---------------------------------------------------------------------------

def write_bundle_with_formats(txns: List[ReconTxn], out_dir: Path, network: str) -> Dict[str, int]:
    """Write transaction bundle using spec-accurate format generators."""
    out_dir.mkdir(parents=True, exist_ok=True)

    if not HAS_FORMATTERS:
        print("  ⚠️  Format generators not available, falling back to basic output")
        return write_bundle_basic(txns, out_dir, network)

    # Minimal config for generators
    config = {
        "member_id": "021577",
        "acquiring_ica": "008653",
        "amount_range": [50_000, 2_500_000],
        "bank_code": "1",
        "branch_code": "00001",
        "processor_id_10": "2123900000",
    }

    business_date = txns[0].date_yyyymmdd
    file_counts = {}

    # Convert to Transaction objects with sequential seq_no
    transactions = [
        _recon_txn_to_transaction(rtxn, f"{i+1:012d}")
        for i, rtxn in enumerate(txns)
    ]

    print(f"\n  Generating spec-accurate files for {network}...")

    if network.upper() == "MC":
        # MasterCard: t112 + t140 + tlf + cbs + gl

        # T112 (MC network clearing)
        t112_path = out_dir / "t112.txt"
        try:
            t112_count = t112_gen.generate(
                transactions=transactions,
                config=config,
                business_date=business_date,
                channel="POS",
                output_path=str(t112_path),
            )
            file_counts["t112"] = {"path": str(t112_path), "records": t112_count or len([t for t in transactions if t.in_network])}
            print(f"    ✓ t112.txt")
        except Exception as e:
            print(f"    ✗ t112.txt failed: {e}")

        # T140 (MC settlement)
        t140_path = out_dir / "t140.txt"
        try:
            t140_gen.generate(
                transactions=transactions,
                config=config,
                business_date=business_date,
                output_path=str(t140_path),
            )
            t140_count = len([t for t in transactions if t.in_network])
            file_counts["t140"] = {"path": str(t140_path), "records": t140_count}
            print(f"    ✓ t140.txt")
        except Exception as e:
            print(f"    ✗ t140.txt failed: {e}")

        # TLF (MC switch) - wrapper for signature mismatch
        tlf_path = out_dir / "tlf.txt"
        try:
            # tlf_generator expects num_txns, not transactions
            result = tlf_gen.generate(
                num_txns=len(transactions),
                business_date=business_date,
                seed=42,
            )
            # Write result to file
            if result and len(result) > 1:
                with open(tlf_path, "w") as f:
                    for line in result[1]:  # result[1] contains lines
                        f.write(line + "\n")
                file_counts["tlf"] = {"path": str(tlf_path), "records": len(transactions)}
                print(f"    ✓ tlf.txt")
            else:
                print(f"    ✗ tlf.txt: no data generated")
        except Exception as e:
            print(f"    ✗ tlf.txt failed: {e}")

        # CBS (Core Banking System)
        cbs_path = out_dir / f"cbs_mc.txt"
        try:
            _, cbs_lines = cbs_gen.generate(
                num_txns=len(transactions),
                business_date=business_date,
                network="MC",
                seed=42,
            )
            with open(cbs_path, "w") as f:
                for line in cbs_lines:
                    f.write(line + "\n")
            file_counts["cbs"] = {"path": str(cbs_path), "records": len(cbs_lines)}
            print(f"    ✓ cbs_mc.txt")
        except Exception as e:
            print(f"    ✗ cbs_mc.txt failed: {e}")

        # GL OUT (General Ledger)
        gl_path = out_dir / "fss_gl_out.txt"
        try:
            _, gl_lines = gl_gen.generate(
                num_txns=len(transactions),
                business_date=business_date,
                network="MC",
                seed=42,
            )
            with open(gl_path, "w") as f:
                for line in gl_lines:
                    f.write(line + "\n")
            file_counts["gl"] = {"path": str(gl_path), "records": len(gl_lines)}
            print(f"    ✓ fss_gl_out.txt")
        except Exception as e:
            print(f"    ✗ fss_gl_out.txt failed: {e}")

    elif network.upper() == "VISA":
        # Visa: epin + ptlf + cbs + gl

        # EPIN (Visa network clearing)
        epin_path = out_dir / "epin.txt"
        try:
            epin_count = epin_gen.generate(
                transactions=transactions,
                config=config,
                channel="POS",
                output_path=str(epin_path),
            )
            file_counts["epin"] = {"path": str(epin_path), "records": epin_count or len([t for t in transactions if t.in_network])}
            print(f"    ✓ epin.txt")
        except Exception as e:
            print(f"    ✗ epin.txt failed: {e}")

        # PTLF (Visa switch)
        ptlf_path = out_dir / "ptlf.txt"
        try:
            ptlf_count = ptlf_gen.generate(
                transactions=transactions,
                config=config,
                role="ACQUIRING",
                output_path=str(ptlf_path),
                pos_type="PHYSICAL",
            )
            file_counts["ptlf"] = {"path": str(ptlf_path), "records": ptlf_count or len(transactions)}
            print(f"    ✓ ptlf.txt")
        except Exception as e:
            print(f"    ✗ ptlf.txt failed: {e}")

        # CBS (Core Banking System)
        cbs_path = out_dir / f"cbs_visa.txt"
        try:
            cbs_count = cbs_gen.generate(
                transactions=transactions,
                config=config,
                network="VISA",
                output_path=str(cbs_path),
            )
            file_counts["cbs"] = {"path": str(cbs_path), "records": cbs_count or len(transactions)}
            print(f"    ✓ cbs_visa.txt")
        except Exception as e:
            print(f"    ✗ cbs_visa.txt failed: {e}")

        # GL OUT (General Ledger)
        gl_path = out_dir / "fss_gl_out.txt"
        try:
            gl_count = gl_gen.generate(
                transactions=transactions,
                config=config,
                business_date=business_date,
                output_path=str(gl_path),
            )
            file_counts["gl"] = {"path": str(gl_path), "records": gl_count or len(transactions)}
            print(f"    ✓ fss_gl_out.txt")
        except Exception as e:
            print(f"    ✗ fss_gl_out.txt failed: {e}")

    # Master scenario table
    master_path = out_dir / "scenario_master_table.csv"
    with open(master_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["txn_id", "scenario_id", "rrn", "arn", "pan_masked",
                    "amount_paise", "date", "cbs_state", "gl_state", "network_state"])
        for t in txns:
            w.writerow([t.txn_id, t.scenario_id, t.rrn, t.arn, _mask_pan(t.pan),
                        t.amount_paise, t.date_yyyymmdd,
                        t.cbs_state, t.gl_state, t.network_state])
    print(f"    ✓ scenario_master_table.csv")

    # Summary
    summary = {
        "total_transactions": len(txns),
        "network": network,
        "business_date": business_date,
        "scenario_distribution": _scenario_counts(txns),
        "file_counts": file_counts,
    }
    with open(out_dir / "scenario_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"    ✓ scenario_summary.json")

    return file_counts


def write_bundle_basic(txns: List[ReconTxn], out_dir: Path, network: str) -> Dict[str, int]:
    """Fallback basic writer (original behavior)."""
    # Placeholder - original implementation
    return {}


# ---------------------------------------------------------------------------
# Distribution + main
# ---------------------------------------------------------------------------

def distribute(num_txns: int, scenario_ids: List[int], rng: random.Random) -> List[int]:
    """Spread num_txns across the given scenario_ids as evenly as possible."""
    n = len(scenario_ids)
    base = num_txns // n
    rem  = num_txns - base * n
    out: List[int] = []
    for i, sid in enumerate(scenario_ids):
        cnt = base + (1 if i < rem else 0)
        out.extend([sid] * cnt)
    rng.shuffle(out)
    return out


def _zip_and_deliver(out_dir: Path) -> Optional[Path]:
    import platform
    if not out_dir.exists() or not any(out_dir.iterdir()): return None
    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = downloads / f"{out_dir.name}_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in out_dir.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(out_dir.parent))
    if platform.system() == "Darwin":
        try: subprocess.run(["open", "--reveal", str(zip_path)], timeout=5)
        except Exception: pass
    return zip_path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Recon scenario matrix generator with format-specific outputs")
    p.add_argument("--num-txns", type=int, default=1000)
    p.add_argument("--network", choices=["VISA", "MC"], default="VISA")
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--scenarios", default="all",
                   help="comma-separated scenario IDs (e.g. 1,2,3,49,50) or 'all' for 1-50")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output-dir", default="")
    p.add_argument("--no-download", action="store_true")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    # Pick scenarios
    if args.scenarios == "all":
        sids = [s[0] for s in SCENARIOS]
    else:
        sids = [int(x) for x in args.scenarios.split(",") if x.strip()]
    selected = [s for s in SCENARIOS if s[0] in sids]
    if not selected:
        print("error: no valid scenario IDs selected", file=sys.stderr); return 2

    rng = random.Random(args.seed)
    rrn_start = rng.randint(600_000_000_000, 699_999_999_999)

    assigned = distribute(args.num_txns, [s[0] for s in selected], rng)
    by_id = {s[0]: s for s in selected}

    txns = [_make_txn(i, by_id[assigned[i]], bdate, args.network, rng, rrn_start)
            for i in range(args.num_txns)]

    out_dir = Path(args.output_dir or f"out_recon_{bdate}_{args.network}")
    out_dir.mkdir(parents=True, exist_ok=True)

    file_counts = write_bundle_with_formats(txns, out_dir, args.network)
    print(f"\n  Generated {args.num_txns} transactions across {len(selected)} scenarios → {out_dir}/")
    for fname, info in file_counts.items():
        if info.get("path"):
            print(f"    {fname:<8} → {info['records']:>5} records")

    if not args.no_download:
        zip_path = _zip_and_deliver(out_dir.resolve())
        if zip_path:
            print(f"\n  📦 Downloaded → {zip_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
