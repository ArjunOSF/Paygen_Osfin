"""
main.py
=======
Universal Payment Recon Test File Generator — Interactive CLI.

Usage:
  python main.py                    # interactive question flow
  python main.py --validate         # run with validation checks after generation

Modes:
  Single scenario  — one channel/role/network, one set of files
  Full day batch   — multiple networks, merged TLF + ATM_C, separate network files
"""

import argparse
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from typing import List, Optional

# Add generators/ to path so imports work cleanly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "generators"))

from data_generator import generate_transactions, Transaction
from scenario_engine import (
    resolve_fileset, apply_layer_config,
    build_exact_match_config, prompt_custom_config,
    FileSet,
)
from summary_generator import write_summary

import generators.atm_c_generator    as atm_c_gen
import generators.ej_generator       as ej_gen
import generators.tlf_generator      as tlf_gen
import generators.ptlf_generator     as ptlf_gen
import generators.mc_t112_generator  as t112_gen
import generators.mc_t140_generator  as t140_gen
import generators.mc_t464_generator  as t464_gen
import generators.visa_epin_generator as epin_gen
import generators.rupay_generator    as rupay_gen
import generators.rupay_863_generator as rupay863_gen
import generators.nfs_generator      as nfs_gen
import generators.fss_ej_generator   as fss_ej_gen
import generators.mci_ar_generator   as mci_ar_gen


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


def prompt_multiselect(question: str, choices: list) -> list:
    """Comma-separated multi-select. Returns list of chosen items."""
    choices_str = ", ".join(choices)
    while True:
        raw = input(f"\n{question}\n  Options: {choices_str}\n  Enter choices (comma-separated): ").strip()
        selected = [s.strip().upper() for s in raw.split(",") if s.strip()]
        valid = []
        for sel in selected:
            for c in choices:
                if sel == c.upper() or sel == c[0].upper():
                    if c not in valid:
                        valid.append(c)
                    break
        if valid:
            return valid
        print(f"  Please enter at least one of: {choices_str}")


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


def make_output_dir(base: str, tag: str) -> str:
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

    try:
        subprocess.run(["open", "--reveal", zip_path], check=False)
    except FileNotFoundError:
        pass

    return zip_path


# ---------------------------------------------------------------------------
# Generator runner — shared by both single and batch modes
# ---------------------------------------------------------------------------

def run_generators(
    transactions: List[Transaction],
    file_set: FileSet,
    config: dict,
    out_dir: str,
    date_str: str,
    channel: str,
    role: str,
    network: Optional[str],
    pos_type: str = "PHYSICAL",
    atm_vendor: str = "HYOSUNG",
) -> dict:
    """Run all generators for a given fileset. Returns files_written dict."""
    files_written = {}

    if file_set.generate_tlf:
        path = os.path.join(out_dir, "tlf.txt")
        n = tlf_gen.generate(transactions, config, path)
        files_written["TLF (Switch ATM)"] = (path, n)
        print(f"  TLF         : {n} records → {os.path.basename(path)}")

    if file_set.generate_ptlf:
        path = os.path.join(out_dir, "ptlf.txt")
        n = ptlf_gen.generate(transactions, config, role=role, output_path=path, pos_type=pos_type)
        files_written["PTLF (Switch POS)"] = (path, n)
        print(f"  PTLF        : {n} records → {os.path.basename(path)}")

    if file_set.generate_atm_c:
        path = os.path.join(out_dir, "atm_c.txt")
        n = atm_c_gen.generate(transactions, config, channel=channel, output_path=path)
        files_written["ATM_C (CBS)"] = (path, n)
        print(f"  ATM_C       : {n} records → {os.path.basename(path)}")

    if file_set.generate_ej:
        if atm_vendor.upper() == "FSS":
            path = os.path.join(out_dir, "fss_ej.txt")
            n = fss_ej_gen.generate(transactions, config, output_path=path)
            files_written["EJ (FSS Cards)"] = (path, n)
            print(f"  FSS EJ      : {n} records → {os.path.basename(path)}")
        else:
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

    if file_set.generate_nfs_iss:
        path = os.path.join(out_dir, "nfs_iss.txt")
        n = nfs_gen.generate(transactions, config, file_category="ISS", output_path=path)
        files_written["NFS ISS"] = (path, n)
        print(f"  NFS ISS     : {n} records → {os.path.basename(path)}")

    if file_set.generate_nfs_acq:
        path = os.path.join(out_dir, "nfs_acq.txt")
        n = nfs_gen.generate(transactions, config, file_category="ACQ", output_path=path)
        files_written["NFS ACQ"] = (path, n)
        print(f"  NFS ACQ     : {n} records → {os.path.basename(path)}")

    if file_set.generate_rupay or file_set.generate_rupay_acq:
        cat = "ACQ" if file_set.generate_rupay_acq else "ISS"
        fcat = "A" if channel.upper() == "ATM" else "P"
        path = os.path.join(out_dir, f"rupay_863_{cat.lower()}.dat")
        n = rupay863_gen.generate(transactions, config, output_path=path, file_category=fcat)
        files_written[f"RuPay 863 ({cat})"] = (path, n)
        print(f"  RuPay 863   : {n} records → {os.path.basename(path)}")

    # MCI.AR daily control report — always generated for MC/NFS scenarios
    has_mc_or_nfs = any(t.network.upper() in ("MC", "NFS") for t in transactions)
    if has_mc_or_nfs:
        path = os.path.join(out_dir, "mci_ar.txt")
        mci_ar_gen.generate(transactions, config, business_date=date_str, output_path=path)
        files_written["MCI.AR"] = (path, 1)
        print(f"  MCI.AR      : daily control report → {os.path.basename(path)}")

    return files_written


# ---------------------------------------------------------------------------
# Single scenario mode
# ---------------------------------------------------------------------------

def run_single(validate: bool = False) -> None:
    # 1. Channel
    channel = prompt("1. Channel?", ["ATM", "POS"], default="ATM")

    # 1b. Sub-type based on channel
    pos_type   = "PHYSICAL"
    atm_vendor = "HYOSUNG"
    if channel.upper() == "POS":
        pos_type_choice = prompt(
            "1b. POS type?",
            ["Physical POS", "E-commerce"],
            default="Physical POS",
        )
        pos_type = "ECOM" if pos_type_choice.upper().startswith("E") else "PHYSICAL"
    elif channel.upper() == "ATM":
        vendor_choice = prompt(
            "1b. ATM Vendor?",
            ["Hyosung", "FSS"],
            default="Hyosung",
        )
        atm_vendor = "FSS" if vendor_choice.upper().startswith("F") else "HYOSUNG"

    # 2. Role
    role = prompt("2. Role?", ["On-Us", "Acquiring", "Issuing"], default="On-Us")
    role_upper = role.upper().replace("-", "").replace(" ", "")
    if role_upper == "ONUS":
        role = "ON-US"

    # 3. Network (skipped for On-Us)
    network: Optional[str] = None
    if role.upper() not in ("ON-US", "ONUS"):
        network = prompt("3. Network?", ["MC", "Visa", "RuPay", "NFS"], default="MC")

    # 4. Scenario
    scenario_type = prompt("4. Scenario?", ["Exact Match", "Custom"], default="Exact Match")
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

    net_str = network or "ONUS"
    print(f"\n  Generating {count} transactions for {channel.upper()} {role.upper()} {net_str}...")

    transactions = generate_transactions(
        count=count,
        network=network or "MC",
        business_date=date_str,
        config=config,
    )
    apply_layer_config(transactions, layer_cfg, channel, role)
    file_set = resolve_fileset(channel, role, network)

    output_base = os.path.join(os.path.dirname(__file__), "output")
    tag = f"{date_str}_{channel.upper()}_{role.upper()}_{net_str}"
    if channel.upper() == "POS" and pos_type == "ECOM":
        tag += "_ECOM"
    out_dir = make_output_dir(output_base, tag)

    print(f"\n  Output dir: {out_dir}\n")

    files_written = run_generators(
        transactions, file_set, config, out_dir,
        date_str, channel, role, network, pos_type, atm_vendor,
    )

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

    zip_path = zip_output(out_dir)
    print(f"  Download : {zip_path}")

    if validate:
        print("\n  Running join key validation...")
        _validate(out_dir, file_set, transactions)


# ---------------------------------------------------------------------------
# Full day batch mode
# ---------------------------------------------------------------------------

def run_batch(validate: bool = False) -> None:
    print("\n  [BATCH MODE] Generates per-network files + one merged TLF + one merged ATM_C")

    # Channel
    channel = prompt("1. Channel?", ["ATM", "POS"], default="ATM")

    pos_type   = "PHYSICAL"
    atm_vendor = "HYOSUNG"
    if channel.upper() == "POS":
        pos_type_choice = prompt(
            "1b. POS type?",
            ["Physical POS", "E-commerce"],
            default="Physical POS",
        )
        pos_type = "ECOM" if pos_type_choice.upper().startswith("E") else "PHYSICAL"
    elif channel.upper() == "ATM":
        vendor_choice = prompt(
            "1b. ATM Vendor?",
            ["Hyosung", "FSS"],
            default="Hyosung",
        )
        atm_vendor = "FSS" if vendor_choice.upper().startswith("F") else "HYOSUNG"

    # Role (batch is always Acquiring — issuing is per-network)
    role = prompt("2. Role?", ["Acquiring", "Issuing"], default="Acquiring")
    role_upper = role.upper().replace("-", "").replace(" ", "")

    # Multi-select networks
    networks = prompt_multiselect(
        "3. Networks to include?",
        ["MC", "Visa", "RuPay", "NFS"],
    )
    print(f"  → Selected: {', '.join(networks)}")

    # Per-network volume + scenario
    network_configs = {}
    for net in networks:
        print(f"\n  --- {net} ---")
        count = prompt_int(f"  Volume for {net}?", default=10, min_val=1)
        sc = prompt(f"  Scenario for {net}?", ["Exact Match", "Custom"], default="Exact Match")
        has_network = True
        if sc.upper().startswith("E"):
            layer_cfg = build_exact_match_config()
        else:
            layer_cfg = prompt_custom_config(channel, role, has_network)
        network_configs[net] = {"count": count, "layer_cfg": layer_cfg}

    # Date + Config
    today = datetime.now().strftime("%Y%m%d")
    date_str = prompt_text("4. Business date (YYYYMMDD)?", default=today)
    try:
        datetime.strptime(date_str, "%Y%m%d")
    except ValueError:
        date_str = today

    config_choice = prompt("5. Config?", ["Defaults", "Custom"], default="Defaults")
    if config_choice.upper().startswith("C"):
        config_path = prompt_text("   Path to custom config JSON?", default="")
        config = load_config(config_path if config_path else None)
    else:
        config = load_config(None)

    # Output dir
    output_base = os.path.join(os.path.dirname(__file__), "output")
    tag = f"{date_str}_{channel.upper()}_{role.upper()}_BATCH_{'_'.join(networks)}"
    out_dir = make_output_dir(output_base, tag)
    print(f"\n  Output dir: {out_dir}\n")

    all_transactions: List[Transaction] = []
    all_files_written = {}

    # Generate per-network files
    for net_idx, net in enumerate(networks):
        cfg = network_configs[net]
        count = cfg["count"]
        layer_cfg = cfg["layer_cfg"]

        print(f"  [{net}] Generating {count} transactions...")
        txns = generate_transactions(
            count=count,
            network=net,
            business_date=date_str,
            config=config,
            seed=int(datetime.now().timestamp()) + net_idx * 100_000,
        )
        apply_layer_config(txns, layer_cfg, channel, role)

        # Network-only fileset (no switch/CBS — those get merged)
        file_set = resolve_fileset(channel, role, net)
        file_set.generate_tlf   = False   # merged separately
        file_set.generate_ptlf  = False
        file_set.generate_atm_c = False
        file_set.generate_ej    = False

        net_files = run_generators(
            txns, file_set, config, out_dir,
            date_str, channel, role, net, pos_type,
        )
        all_files_written.update(net_files)
        all_transactions.extend(txns)

    # Merged TLF (all transactions → one file)
    print(f"\n  [MERGE] Writing merged switch + CBS files...")
    if channel.upper() == "ATM":
        path = os.path.join(out_dir, "tlf_merged.txt")
        n = tlf_gen.generate(all_transactions, config, path)
        all_files_written["TLF Merged"] = (path, n)
        print(f"  TLF merged  : {n} records → {os.path.basename(path)}")
    else:
        path = os.path.join(out_dir, "ptlf_merged.txt")
        n = ptlf_gen.generate(all_transactions, config, role=role, output_path=path, pos_type=pos_type)
        all_files_written["PTLF Merged"] = (path, n)
        print(f"  PTLF merged : {n} records → {os.path.basename(path)}")

    # Merged ATM_C
    path = os.path.join(out_dir, "atm_c_merged.txt")
    n = atm_c_gen.generate(all_transactions, config, channel=channel, output_path=path)
    all_files_written["ATM_C Merged"] = (path, n)
    print(f"  ATM_C merged: {n} records → {os.path.basename(path)}")

    # Merged EJ (ATM non-issuing only)
    if channel.upper() == "ATM" and role.upper() not in ("ISSUING", "ISS"):
        if atm_vendor.upper() == "FSS":
            path = os.path.join(out_dir, "fss_ej_merged.txt")
            n = fss_ej_gen.generate(all_transactions, config, output_path=path)
            all_files_written["FSS EJ Merged"] = (path, n)
            print(f"  FSS EJ merged: {n} records → {os.path.basename(path)}")
        else:
            path = os.path.join(out_dir, "ej_merged.csv")
            n = ej_gen.generate(all_transactions, config, output_path=path)
            all_files_written["EJ Merged"] = (path, n)
            print(f"  EJ merged   : {n} records → {os.path.basename(path)}")

    zip_path = zip_output(out_dir)
    print(f"\n  Download : {zip_path}")

    if validate:
        # Validate merged files
        from scenario_engine import FileSet as FS
        merged_fs = FS(
            generate_tlf=(channel.upper() == "ATM"),
            generate_ptlf=(channel.upper() == "POS"),
            generate_atm_c=True,
        )
        # Remap paths for validate to pick up merged files
        _validate_merged(out_dir, channel, all_transactions)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(validate: bool = False) -> None:
    print("\n" + "=" * 60)
    print("  Universal Payment Recon Test File Generator")
    print("=" * 60)

    mode = prompt(
        "Mode?",
        ["Single scenario", "Full day batch"],
        default="Single scenario",
    )

    if mode.upper().startswith("F"):
        run_batch(validate=validate)
    else:
        run_single(validate=validate)

    print("\n" + "=" * 60)
    print("  Done!")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate(out_dir: str, file_set, transactions) -> None:
    errors = 0

    tlf_path   = os.path.join(out_dir, "tlf.txt")
    atm_c_path = os.path.join(out_dir, "atm_c.txt")

    if os.path.exists(tlf_path) and os.path.exists(atm_c_path):
        tlf_seqs, atm_c_seqs = set(), set()
        with open(tlf_path) as f:
            for line in f:
                if len(line.rstrip("\n")) >= 200:
                    tlf_seqs.add(line[188:200])
        with open(atm_c_path) as f:
            for line in f:
                if len(line.rstrip("\n")) >= 57:
                    atm_c_seqs.add(line[45:57])
        missing = tlf_seqs - atm_c_seqs
        if missing or tlf_seqs - atm_c_seqs:
            print(f"  [WARN] TLF↔ATM_C SEQ_NO mismatch: {len(missing)} in TLF only")
            errors += 1
        else:
            print(f"  [OK]   TLF ↔ ATM_C SEQ_NO join: {len(tlf_seqs)} records match")

    ptlf_path = os.path.join(out_dir, "ptlf.txt")
    if os.path.exists(ptlf_path) and os.path.exists(atm_c_path):
        ptlf_seqs, atm_c_seqs = set(), set()
        with open(ptlf_path) as f:
            for line in f:
                stripped = line.rstrip("\n")
                # Skip the 2052-char file header; only 1904-char data records carry SEQ_NO
                if len(stripped) == 1904:
                    ptlf_seqs.add(stripped[204:216])
        with open(atm_c_path) as f:
            for line in f:
                if len(line.rstrip("\n")) >= 57:
                    atm_c_seqs.add(line[45:57])
        missing = ptlf_seqs - atm_c_seqs
        if missing:
            print(f"  [WARN] PTLF↔ATM_C SEQ_NO mismatch: {len(missing)} in PTLF only")
            errors += 1
        else:
            print(f"  [OK]   PTLF ↔ ATM_C SEQ_NO join: {len(ptlf_seqs)} records match")

    t112_path = os.path.join(out_dir, "t112.txt")
    if os.path.exists(t112_path) and os.path.exists(atm_c_path):
        t112_rrns, atm_c_rrns = set(), set()
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
            print(f"  [WARN] T112.DE37 ↔ ATM_C.RR_NO mismatch: {len(missing)} DE37 not in ATM_C")
            errors += 1
        elif t112_rrns:
            print(f"  [OK]   T112.DE37 ↔ ATM_C.RR_NO join: {len(t112_rrns)} records match")

    # NFS join check — STAN (pos 49, len 12) → ATM_C.SEQ_NO (pos 45, len 12)
    for nfs_fname in ("nfs_iss.txt", "nfs_acq.txt"):
        nfs_path = os.path.join(out_dir, nfs_fname)
        if os.path.exists(nfs_path) and os.path.exists(atm_c_path):
            nfs_stans, atm_c_seqs = set(), set()
            with open(nfs_path) as f:
                for line in f:
                    l = line.rstrip("\n")
                    if len(l) >= 61:
                        nfs_stans.add(l[49:61])
            with open(atm_c_path) as f:
                for line in f:
                    l = line.rstrip("\n")
                    if len(l) >= 57:
                        atm_c_seqs.add(l[45:57])
            missing = nfs_stans - atm_c_seqs
            label = nfs_fname.upper().replace(".TXT", "")
            if missing:
                print(f"  [WARN] {label}.STAN ↔ ATM_C.SEQ_NO mismatch: {len(missing)} not matched")
                errors += 1
            else:
                print(f"  [OK]   {label}.STAN ↔ ATM_C.SEQ_NO join: {len(nfs_stans)} records match")

    # (fname, expected_len, label, skip_line_len_or_None)
    for fname, expected_len, label, skip_len in [
        ("tlf.txt",    574,  "TLF",     None),
        ("atm_c.txt",  186,  "ATM_C",   None),
        ("ptlf.txt",   1904, "PTLF",    2052),   # skip 2052-char file header
        ("nfs_iss.txt", 407, "NFS ISS", None),
        ("nfs_acq.txt", 274, "NFS ACQ", None),
    ]:
        path = os.path.join(out_dir, fname)
        if os.path.exists(path):
            bad, good = [], 0
            with open(path) as f:
                for i, line in enumerate(f):
                    l = line.rstrip("\n")
                    if skip_len is not None and len(l) == skip_len:
                        continue
                    if len(l) != expected_len:
                        bad.append((i + 1, len(l)))
                    else:
                        good += 1
            if bad:
                print(f"  [FAIL] {label}: {len(bad)} records with wrong length (expected {expected_len})")
                for ln, ln_len in bad[:3]:
                    print(f"         Line {ln}: {ln_len} chars")
                errors += 1
            else:
                print(f"  [OK]   {label}: all {good} records = {expected_len} chars")

    print(f"\n  Validation: {'PASS' if errors == 0 else f'FAIL ({errors} issues)'}")


def _validate_merged(out_dir: str, channel: str, transactions) -> None:
    errors = 0
    switch_file = "tlf_merged.txt" if channel.upper() == "ATM" else "ptlf_merged.txt"
    atm_c_path  = os.path.join(out_dir, "atm_c_merged.txt")
    sw_path     = os.path.join(out_dir, switch_file)

    if os.path.exists(sw_path) and os.path.exists(atm_c_path):
        sw_seqs, atm_c_seqs = set(), set()
        pos_sw = 188 if channel.upper() == "ATM" else 282
        with open(sw_path) as f:
            for line in f:
                l = line.rstrip("\n")
                if len(l) > pos_sw + 12:
                    sw_seqs.add(l[pos_sw:pos_sw + 12])
        with open(atm_c_path) as f:
            for line in f:
                l = line.rstrip("\n")
                if len(l) >= 57:
                    atm_c_seqs.add(l[45:57])
        missing = sw_seqs - atm_c_seqs
        label = "TLF" if channel.upper() == "ATM" else "PTLF"
        if missing:
            print(f"  [WARN] Merged {label}↔ATM_C SEQ_NO mismatch: {len(missing)} unmatched")
            errors += 1
        else:
            print(f"  [OK]   Merged {label} ↔ ATM_C SEQ_NO join: {len(sw_seqs)} records match")

    print(f"\n  Batch validation: {'PASS' if errors == 0 else f'FAIL ({errors} issues)'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Universal Payment Recon Test File Generator")
    parser.add_argument("--validate", action="store_true",
                        help="Run join key and record length validation after generation")
    args = parser.parse_args()
    run(validate=args.validate)
