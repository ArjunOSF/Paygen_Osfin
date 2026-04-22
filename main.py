"""
main.py
=======
Universal Payment Recon Test File Generator — Interactive CLI.

Usage:
  python main.py                    # interactive question flow
  python main.py --validate         # run with validation checks after generation

Question flow:
  1. Channel   → ATM | POS
  2. Role      → On-Us | Acquiring | Issuing
  3. Network   → MC | Visa | RuPay   (skipped for On-Us)
  4. Scenario  → Exact Match | Custom (per-layer pass/fail/missing)
  5. Volume    → number of transactions
  6. Date      → YYYYMMDD
  7. Config    → defaults | custom JSON path
"""

import argparse
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from typing import Optional

# Add generators/ to path so imports work cleanly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "generators"))

from data_generator import generate_transactions
from scenario_engine import (
    resolve_fileset, apply_layer_config,
    build_exact_match_config, prompt_custom_config,
)
from summary_generator import write_summary

import generators.atm_c_generator   as atm_c_gen
import generators.ej_generator      as ej_gen
import generators.tlf_generator     as tlf_gen
import generators.ptlf_generator    as ptlf_gen
import generators.mc_t112_generator as t112_gen
import generators.mc_t140_generator as t140_gen
import generators.mc_t464_generator as t464_gen
import generators.visa_epin_generator as epin_gen
import generators.rupay_generator   as rupay_gen


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def prompt(question: str, choices: list, default: Optional[str] = None) -> str:
    choices_str = " / ".join(f"[{c}]" for c in choices)
    if default:
        choices_str += f"  (default: {default})"
    while True:
        raw = input(f"\n{question}\n  {choices_str}: ").strip()
        if not raw and default:
            return default
        normalized = raw.upper()
        for c in choices:
            if normalized == c.upper() or normalized == c[0].upper():
                return c
        print(f"  Please enter one of: {', '.join(choices)}")


def prompt_text(question: str, default: str) -> str:
    raw = input(f"\n{question} (default: {default}): ").strip()
    return raw if raw else default


def prompt_int(question: str, default: int, min_val: int = 1) -> int:
    while True:
        raw = input(f"\n{question} (default: {default}): ").strip()
        if not raw:
            return default
        try:
            val = int(raw)
            if val >= min_val:
                return val
        except ValueError:
            pass
        print(f"  Please enter a number >= {min_val}")


def load_config(config_path: Optional[str]) -> dict:
    if config_path:
        with open(config_path) as f:
            return json.load(f)
    default_path = os.path.join(os.path.dirname(__file__), "config", "default_config.json")
    with open(default_path) as f:
        return json.load(f)


def make_output_dir(base: str, business_date: str, channel: str, role: str, network: str) -> str:
    tag = f"{business_date}_{channel.upper()}_{role.upper()}"
    if network:
        tag += f"_{network.upper()}"
    out = os.path.join(base, tag)
    os.makedirs(out, exist_ok=True)
    return out


def zip_output(out_dir: str) -> str:
    folder_name = os.path.basename(out_dir)
    downloads = os.path.expanduser("~/Downloads")
    zip_path = os.path.join(downloads, f"{folder_name}.zip")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in os.listdir(out_dir):
            fpath = os.path.join(out_dir, fname)
            if os.path.isfile(fpath):
                zf.write(fpath, fname)

    # Reveal the zip in Finder (macOS) — no-op on other platforms
    try:
        subprocess.run(["open", "--reveal", zip_path], check=False)
    except FileNotFoundError:
        pass

    return zip_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(validate: bool = False) -> None:
    print("\n" + "=" * 60)
    print("  Universal Payment Recon Test File Generator")
    print("=" * 60)

    # 1. Channel
    channel = prompt(
        "1. Channel?",
        ["ATM", "POS"],
        default="ATM",
    )

    # 2. Role
    role = prompt(
        "2. Role?",
        ["On-Us", "Acquiring", "Issuing"],
        default="On-Us",
    )
    # Normalize
    role_upper = role.upper().replace("-", "").replace(" ", "")
    if role_upper in ("ONUS", "ONUS"):
        role = "ON-US"

    # 3. Network (skipped for On-Us)
    network: Optional[str] = None
    if role.upper() not in ("ON-US", "ONUS"):
        network = prompt(
            "3. Network?",
            ["MC", "Visa", "RuPay"],
            default="MC",
        )

    # 4. Scenario
    scenario_type = prompt(
        "4. Scenario?",
        ["Exact Match", "Custom"],
        default="Exact Match",
    )

    has_network = network is not None

    if scenario_type.upper().startswith("E"):
        layer_cfg = build_exact_match_config()
        print(f"\n  → All layers: PASS (exact match)")
    else:
        layer_cfg = prompt_custom_config(channel, role, has_network)

    # 5. Volume
    count = prompt_int("5. Number of transactions?", default=10, min_val=1)

    # 6. Date
    today = datetime.now().strftime("%Y%m%d")
    date_str = prompt_text("6. Business date (YYYYMMDD)?", default=today)
    try:
        datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        print(f"  Invalid date '{date_str}', using today: {today}")
        date_str = today

    # 7. Config
    config_choice = prompt("7. Config?", ["Defaults", "Custom"], default="Defaults")
    if config_choice.upper().startswith("C"):
        config_path = prompt_text("   Path to custom config JSON?", default="")
        config = load_config(config_path if config_path else None)
    else:
        config = load_config(None)

    # ---------------------------------------------------------------------------
    # Generate transactions (shared data model)
    # ---------------------------------------------------------------------------
    net_str = network or "ONUS"
    print(f"\n  Generating {count} transactions for {channel.upper()} {role.upper()} {net_str}...")

    transactions = generate_transactions(
        count=count,
        network=network or "MC",   # On-Us uses MC format internally (PAN prefix)
        business_date=date_str,
        config=config,
    )

    # Apply layer flags per scenario
    apply_layer_config(transactions, layer_cfg, channel, role)

    # Resolve which files to generate
    file_set = resolve_fileset(channel, role, network)

    # Output directory
    output_base = os.path.join(os.path.dirname(__file__), "output")
    out_dir = make_output_dir(output_base, date_str, channel, role, net_str)

    files_written = {}

    print(f"\n  Output dir: {out_dir}")
    print()

    # ---------------------------------------------------------------------------
    # Run each generator
    # ---------------------------------------------------------------------------

    if file_set.generate_tlf:
        path = os.path.join(out_dir, "tlf.txt")
        n = tlf_gen.generate(transactions, config, path)
        files_written["TLF (Switch ATM)"] = (path, n)
        print(f"  TLF         : {n} records → {os.path.basename(path)}")

    if file_set.generate_ptlf:
        path = os.path.join(out_dir, "ptlf.txt")
        n = ptlf_gen.generate(transactions, config, role=role, output_path=path)
        files_written["PTLF (Switch POS)"] = (path, n)
        print(f"  PTLF        : {n} records → {os.path.basename(path)}")

    if file_set.generate_atm_c:
        path = os.path.join(out_dir, "atm_c.txt")
        n = atm_c_gen.generate(transactions, config, channel=channel, output_path=path)
        files_written["ATM_C (CBS)"] = (path, n)
        print(f"  ATM_C       : {n} records → {os.path.basename(path)}")

    if file_set.generate_ej:
        path = os.path.join(out_dir, "ej.csv")
        n = ej_gen.generate(transactions, config, output_path=path)
        files_written["EJ (Hyosung)"] = (path, n)
        print(f"  EJ          : {n} records → {os.path.basename(path)}")

    if file_set.generate_t112:
        path = os.path.join(out_dir, "t112.txt")
        n = t112_gen.generate(transactions, config, business_date=date_str, channel=channel, output_path=path)
        files_written["MC T112"] = (path, n)
        print(f"  MC T112     : {n} 1240-msgs → {os.path.basename(path)}")

    if file_set.generate_t140:
        path = os.path.join(out_dir, "t140.txt")
        t140_gen.generate(transactions, config, business_date=date_str, output_path=path)
        files_written["MC T140"] = (path, sum(1 for t in transactions if t.in_network))
        print(f"  MC T140     : settlement summary → {os.path.basename(path)}")

    if file_set.generate_t464:
        path = os.path.join(out_dir, "t464.t464")
        n = t464_gen.generate(transactions, config, business_date=date_str, output_path=path)
        files_written["MC T464"] = (path, n)
        print(f"  MC T464     : {n} FREC records → {os.path.basename(path)}")

    if file_set.generate_visa_epin:
        path = os.path.join(out_dir, "visa_epin.txt")
        n = epin_gen.generate(transactions, config, channel=channel, output_path=path)
        files_written["Visa EPIN"] = (path, n)
        print(f"  Visa EPIN   : {n} transactions → {os.path.basename(path)}")

    if file_set.generate_rupay or file_set.generate_rupay_acq:
        cat  = "ACQ" if file_set.generate_rupay_acq else "ISS"
        path = os.path.join(out_dir, f"rupay_{cat.lower()}.xml")
        n = rupay_gen.generate(transactions, config, file_category=cat, output_path=path)
        files_written[f"RuPay XML ({cat})"] = (path, n)
        print(f"  RuPay XML   : {n} transactions → {os.path.basename(path)}")

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    write_summary(
        output_dir=out_dir,
        channel=channel,
        role=role,
        network=network,
        layer_cfg=layer_cfg,
        file_set=file_set,
        transactions=transactions,
        config=config,
        business_date=date_str,
        files_written=files_written,
    )

    # ---------------------------------------------------------------------------
    # Zip for download
    # ---------------------------------------------------------------------------
    zip_path = zip_output(out_dir)
    print(f"  Download : {zip_path}")

    # ---------------------------------------------------------------------------
    # Validation (optional)
    # ---------------------------------------------------------------------------
    if validate:
        print("\n  Running join key validation...")
        _validate(out_dir, file_set, transactions)

    print("\n" + "=" * 60)
    print("  Done!")
    print("=" * 60 + "\n")


def _validate(out_dir: str, file_set, transactions) -> None:
    """Quick spot-check: SEQ_NO consistency between TLF and ATM_C."""
    errors = 0

    # Check TLF SEQ_NO (pos 188–199) matches ATM_C SEQ_NO (pos 45–56)
    tlf_path   = os.path.join(out_dir, "tlf.txt")
    atm_c_path = os.path.join(out_dir, "atm_c.txt")

    if os.path.exists(tlf_path) and os.path.exists(atm_c_path):
        tlf_seqs   = set()
        atm_c_seqs = set()
        with open(tlf_path) as f:
            for line in f:
                if len(line.rstrip("\n")) >= 200:
                    tlf_seqs.add(line[188:200])
        with open(atm_c_path) as f:
            for line in f:
                if len(line.rstrip("\n")) >= 57:
                    atm_c_seqs.add(line[45:57])

        missing = tlf_seqs - atm_c_seqs
        extra   = atm_c_seqs - tlf_seqs
        if missing or extra:
            print(f"  [WARN] TLF↔ATM_C SEQ_NO mismatch: {len(missing)} in TLF only, {len(extra)} in ATM_C only")
            errors += 1
        else:
            print(f"  [OK]   TLF ↔ ATM_C SEQ_NO join: {len(tlf_seqs)} records match")

    # Check PTLF SEQ_NO (pos 282–293) matches ATM_C SEQ_NO (pos 45–56)
    ptlf_path = os.path.join(out_dir, "ptlf.txt")
    if os.path.exists(ptlf_path) and os.path.exists(atm_c_path):
        ptlf_seqs  = set()
        atm_c_seqs = set()
        with open(ptlf_path) as f:
            for line in f:
                if len(line.rstrip("\n")) >= 294:
                    ptlf_seqs.add(line[282:294])
        with open(atm_c_path) as f:
            for line in f:
                if len(line.rstrip("\n")) >= 57:
                    atm_c_seqs.add(line[45:57])

        missing = ptlf_seqs - atm_c_seqs
        if missing:
            print(f"  [WARN] PTLF↔ATM_C SEQ_NO mismatch: {len(missing)} in PTLF not in ATM_C")
            errors += 1
        else:
            print(f"  [OK]   PTLF ↔ ATM_C SEQ_NO join: {len(ptlf_seqs)} records match")

    # Check T112 RRN (DE37) matches ATM_C RR_NO (pos 57–68)
    t112_path = os.path.join(out_dir, "t112.txt")
    if os.path.exists(t112_path) and os.path.exists(atm_c_path):
        t112_rrns  = set()
        atm_c_rrns = set()
        with open(t112_path) as f:
            for line in f:
                for part in line.split("|"):
                    if part.startswith("DE37:"):
                        t112_rrns.add(part[5:].strip())
        with open(atm_c_path) as f:
            for line in f:
                if len(line.rstrip("\n")) >= 69:
                    atm_c_rrns.add(line[57:69])

        missing = t112_rrns - atm_c_rrns
        if missing and t112_rrns:
            print(f"  [WARN] T112.DE37 ↔ ATM_C.RR_NO mismatch: {len(missing)} DE37 values not in ATM_C")
            errors += 1
        elif t112_rrns:
            print(f"  [OK]   T112.DE37 ↔ ATM_C.RR_NO join: {len(t112_rrns)} records match")

    # Record length checks
    for fname, expected_len, label in [
        ("tlf.txt",    574,  "TLF"),
        ("atm_c.txt",  186,  "ATM_C"),
        ("ptlf.txt",   2610, "PTLF"),
    ]:
        path = os.path.join(out_dir, fname)
        if os.path.exists(path):
            bad = []
            with open(path) as f:
                for i, line in enumerate(f):
                    line_stripped = line.rstrip("\n")
                    if len(line_stripped) != expected_len:
                        bad.append((i + 1, len(line_stripped)))
            if bad:
                print(f"  [FAIL] {label}: {len(bad)} records with wrong length (expected {expected_len})")
                for ln, ln_len in bad[:3]:
                    print(f"         Line {ln}: {ln_len} chars")
                errors += 1
            else:
                lines_count = sum(1 for _ in open(path))
                print(f"  [OK]   {label}: all {lines_count} records = {expected_len} chars")

    print(f"\n  Validation: {'PASS' if errors == 0 else f'FAIL ({errors} issues)'}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Universal Payment Recon Test File Generator"
    )
    parser.add_argument("--validate", action="store_true",
                        help="Run join key and record length validation after generation")
    args = parser.parse_args()
    run(validate=args.validate)
