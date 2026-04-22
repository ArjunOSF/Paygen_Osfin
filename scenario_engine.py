"""
scenario_engine.py
==================
Maps (channel, role, network, scenario) answers to:
  - FileSet: which file generators to run
  - Per-transaction layer flags: in_switch, in_cbs, in_network, in_ej, etc.
"""

from dataclasses import dataclass, field
from typing import List, Optional
from data_generator import Transaction


# ---------------------------------------------------------------------------
# FileSet
# ---------------------------------------------------------------------------

@dataclass
class FileSet:
    generate_tlf:        bool = False
    generate_ptlf:       bool = False
    generate_atm_c:      bool = False
    generate_ej:         bool = False
    generate_t112:       bool = False
    generate_t140:       bool = False
    generate_t464:       bool = False   # MC ATM Acquiring only
    generate_visa_epin:  bool = False
    generate_rupay:      bool = False
    generate_rupay_acq:  bool = False   # ACQ vs ISS RuPay have different file categories


# ---------------------------------------------------------------------------
# LayerChoice — what the user picks for each layer in Custom mode
# ---------------------------------------------------------------------------

PASS    = "pass"
FAIL    = "fail"
MISSING = "missing"


@dataclass
class LayerConfig:
    switch:  str = PASS    # pass | fail | missing
    cbs:     str = PASS
    network: str = PASS
    ej:      str = PASS


# ---------------------------------------------------------------------------
# Scenario resolution
# ---------------------------------------------------------------------------

def resolve_fileset(channel: str, role: str, network: Optional[str]) -> FileSet:
    """Determine which files to generate based on channel/role/network."""
    fs = FileSet()
    channel = channel.upper()
    role = role.upper()
    net = (network or "").upper()

    is_atm = channel == "ATM"
    is_pos = channel == "POS"
    is_onus = role == "ON-US" or role == "ONUS"
    is_acq  = role == "ACQUIRING" or role == "ACQ"
    is_iss  = role == "ISSUING" or role == "ISS"

    # Switch file
    if is_atm:
        fs.generate_tlf = True
    if is_pos:
        fs.generate_ptlf = True

    # CBS (ATM_C) always generated
    fs.generate_atm_c = True

    # EJ — ATM only (not issuing — EJ belongs to acquirer's ATM)
    if is_atm and not is_iss:
        fs.generate_ej = True

    # Network files
    if not is_onus and net:
        if net == "MC":
            fs.generate_t112 = True
            fs.generate_t140 = True
            if is_atm and is_acq:
                fs.generate_t464 = True
        elif net == "VISA":
            fs.generate_visa_epin = True
        elif net == "RUPAY":
            if is_acq:
                fs.generate_rupay_acq = True
            else:
                fs.generate_rupay = True

    return fs


def apply_layer_config(
    transactions: List[Transaction],
    layer_cfg: LayerConfig,
    channel: str,
    role: str,
) -> List[Transaction]:
    """
    Set in_switch / in_cbs / in_network / in_ej / ej_success / switch_status
    on each transaction according to the user's layer choices.

    For 'missing': transaction absent from that layer's file.
    For 'fail':    transaction present but with failure indicators.
    For 'pass':    transaction present with success indicators.
    """
    channel = channel.upper()
    role    = role.upper()
    is_iss  = role in ("ISSUING", "ISS")

    for txn in transactions:
        # Switch layer
        if layer_cfg.switch == PASS:
            txn.in_switch = True
            txn.switch_status = "00"
        elif layer_cfg.switch == FAIL:
            txn.in_switch = True
            txn.switch_status = "57"
        else:  # missing
            txn.in_switch = False
            txn.switch_status = "57"

        # CBS layer
        txn.in_cbs = (layer_cfg.cbs != MISSING)

        # Network layer
        txn.in_network = (layer_cfg.network != MISSING)

        # EJ layer — only relevant for ATM non-issuing
        if channel == "ATM" and not is_iss:
            if layer_cfg.ej == PASS:
                txn.in_ej = True
                txn.ej_success = True
            elif layer_cfg.ej == FAIL:
                txn.in_ej = True
                txn.ej_success = False
            else:  # missing
                txn.in_ej = False
                txn.ej_success = False
        else:
            txn.in_ej = False
            txn.ej_success = False

    return transactions


def build_exact_match_config() -> LayerConfig:
    return LayerConfig(switch=PASS, cbs=PASS, network=PASS, ej=PASS)


def prompt_custom_config(channel: str, role: str, has_network: bool) -> LayerConfig:
    """Interactive prompt for custom layer pass/fail/missing."""
    channel = channel.upper()
    role    = role.upper()
    is_iss  = role in ("ISSUING", "ISS")

    def ask(layer_name: str) -> str:
        while True:
            val = input(f"  {layer_name} layer [pass/fail/missing]: ").strip().lower()
            if val in (PASS, FAIL, MISSING):
                return val
            print("  Please enter: pass, fail, or missing")

    print("\nConfigure each layer (pass = success indicators, fail = failure indicators, missing = omit from file):")
    switch_choice = ask("Switch (TLF/PTLF)")
    cbs_choice    = ask("CBS (ATM_C)")
    network_choice = ask("Network (T112/EPIN/XML)") if has_network else PASS
    ej_choice     = ask("EJ") if (channel == "ATM" and not is_iss) else PASS

    return LayerConfig(
        switch=switch_choice,
        cbs=cbs_choice,
        network=network_choice,
        ej=ej_choice,
    )


def layers_description(layer_cfg: LayerConfig, channel: str, role: str, has_network: bool) -> str:
    """Human-readable summary of the layer configuration."""
    channel = channel.upper()
    role    = role.upper()
    is_iss  = role in ("ISSUING", "ISS")

    parts = [
        f"Switch={layer_cfg.switch.upper()}",
        f"CBS={layer_cfg.cbs.upper()}",
    ]
    if has_network:
        parts.append(f"Network={layer_cfg.network.upper()}")
    if channel == "ATM" and not is_iss:
        parts.append(f"EJ={layer_cfg.ej.upper()}")

    return " | ".join(parts)
