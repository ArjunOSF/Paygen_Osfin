"""
summary_generator.py
====================
Writes SCENARIO_SUMMARY.md and CONFIG.json into the output folder.
"""

import json
import os
from datetime import datetime
from typing import List, Optional

from data_generator import Transaction
from scenario_engine import FileSet, LayerConfig, layers_description


def write_summary(
    output_dir: str,
    channel: str,
    role: str,
    network: Optional[str],
    layer_cfg: LayerConfig,
    file_set: FileSet,
    transactions: List[Transaction],
    config: dict,
    business_date: str,
    files_written: dict,       # {file_type: (path, record_count)}
) -> None:
    has_network = bool(network and network.upper() not in ("NONE", ""))
    layer_str   = layers_description(layer_cfg, channel, role, has_network)

    total = len(transactions)
    in_switch  = sum(1 for t in transactions if t.in_switch)
    in_cbs     = sum(1 for t in transactions if t.in_cbs)
    in_network = sum(1 for t in transactions if t.in_network)
    in_ej      = sum(1 for t in transactions if t.in_ej)
    ej_ok      = sum(1 for t in transactions if t.in_ej and t.ej_success)

    md_lines = [
        "# Recon Test Scenario Summary",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**Business Date:** {business_date}  ",
        f"**Channel:** {channel.upper()}  ",
        f"**Role:** {role.upper()}  ",
        f"**Network:** {(network or 'N/A').upper()}  ",
        "",
        "## Layer Configuration",
        "",
        f"```",
        layer_str,
        f"```",
        "",
        "## Transaction Counts",
        "",
        f"| Layer | Transactions | Notes |",
        f"|-------|-------------|-------|",
        f"| Total generated | {total} | |",
        f"| Switch (TLF/PTLF) | {in_switch} | {'MISSING' if in_switch == 0 else 'present'} |",
        f"| CBS (ATM_C) | {in_cbs} | {'MISSING' if in_cbs == 0 else 'present'} |",
    ]
    if has_network:
        md_lines.append(f"| Network ({(network or '').upper()}) | {in_network} | {'MISSING' if in_network == 0 else 'present'} |")
    if file_set.generate_ej:
        md_lines.append(f"| EJ | {in_ej} | {ej_ok} success, {in_ej - ej_ok} fail |")

    md_lines += [
        "",
        "## Files Generated",
        "",
        f"| File | Path | Records |",
        f"|------|------|---------|",
    ]
    for file_type, (path, count) in files_written.items():
        rel = os.path.relpath(path, output_dir)
        md_lines.append(f"| {file_type} | `{rel}` | {count} |")

    md_lines += [
        "",
        "## Join Key Reference",
        "",
        "```",
        "PRIMARY KEY (links Switch → CBS → EJ):",
        f"  TLF/PTLF.SequenceNumber  =  ATM_C.SEQ_NO  =  EJ.TRXN_NO",
        f"  Sample: {transactions[0].seq_no if transactions else 'N/A'}",
        "",
        "SECONDARY KEY (links CBS → Network):",
        f"  ATM_C.RR_NO  =  MC_T112.DE37  =  Visa_EPIN.ARN[embedded]  =  RuPay.nRRN",
        f"  Sample: {transactions[0].rrn if transactions else 'N/A'}",
        "",
        "AMOUNT (must match across all files, in paise):",
        f"  Sample: {transactions[0].amount if transactions else 'N/A'} paise",
        "```",
        "",
        "## Recon Expected Outcome",
        "",
    ]

    # Describe the expected recon result
    all_pass = in_switch == total and in_cbs == total and (not has_network or in_network == total)
    if all_pass and (not file_set.generate_ej or ej_ok == in_ej):
        md_lines.append("**RECONCILED** — All layers match. No breaks expected.")
    else:
        md_lines.append("**UNRECONCILED** — Breaks expected on the following layers:")
        if in_switch < total:
            md_lines.append(f"- Switch: {total - in_switch} transactions missing from TLF/PTLF")
        elif any(t.switch_status != "00" for t in transactions if t.in_switch):
            fail_count = sum(1 for t in transactions if t.in_switch and t.switch_status != "00")
            md_lines.append(f"- Switch: {fail_count} transactions with failure status (57)")
        if in_cbs < total:
            md_lines.append(f"- CBS: {total - in_cbs} transactions missing from ATM_C")
        if has_network and in_network < total:
            md_lines.append(f"- Network: {total - in_network} transactions missing from network file")
        if file_set.generate_ej and ej_ok < in_ej:
            md_lines.append(f"- EJ: {in_ej - ej_ok} transactions with TRXN_FINAL_STATUS=Fail")

    md_path = os.path.join(output_dir, "SCENARIO_SUMMARY.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")

    # Also write CONFIG.json snapshot
    cfg_out = {
        "channel": channel.upper(),
        "role": role.upper(),
        "network": (network or "").upper(),
        "business_date": business_date,
        "total_transactions": total,
        "layer_config": {
            "switch":  layer_cfg.switch,
            "cbs":     layer_cfg.cbs,
            "network": layer_cfg.network,
            "ej":      layer_cfg.ej,
        },
        "bank_config": config,
        "generated_at": datetime.now().isoformat(),
    }
    cfg_path = os.path.join(output_dir, "CONFIG.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg_out, f, indent=2)

    print(f"\n  Summary  : {md_path}")
    print(f"  Config   : {cfg_path}")
