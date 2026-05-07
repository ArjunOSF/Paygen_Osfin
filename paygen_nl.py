"""
paygen_nl.py
============
Natural Language input layer for PayGen.

User types a free-form prompt — the system parses it, validates against the
knowledge base, asks one question at a time when required info is missing,
shows a confirmation, and then orchestrates the right generator scripts.

Examples:
  $ python3 paygen_nl.py
  > 5000 Mastercard acquiring transactions, INR, 1 April 2026

  $ python3 paygen_nl.py --prompt "10000 Visa issuing e-commerce, HDFC, 26/03/26"

The parser is pure rule-based (regex + keyword) so no API key needed and
parsing is deterministic. Knowledge base, parser, validator, and orchestrator
are all in this single file.
"""

from __future__ import annotations
import argparse, json, os, re, shlex, subprocess, sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

GEN_DIR = Path(__file__).parent / "generators"

# ---------------------------------------------------------------------------
# KNOWLEDGE BASE — what the system knows
# ---------------------------------------------------------------------------

KB_NETWORKS = {
    # canonical → aliases
    "MC":    ["mastercard", "mc", "master card", "mcard"],
    "VISA":  ["visa"],
    "RUPAY": ["rupay", "ru-pay"],
    "NFS":   ["nfs"],
}

KB_ROLES = {
    "ACQUIRING": ["acquiring", "acquirer", "acquiring side", "acquirer side", "acq"],
    "ISSUING":   ["issuing", "issuer", "issuing side", "issuer side", "iss"],
    "ON_US":     ["on us", "on-us", "onus", "same bank", "intra-bank", "intrabank"],
}

KB_CHANNELS = {
    "POS":      ["pos", "point of sale", "purchase", "purchases", "merchant"],
    "ECOM":     ["e-commerce", "ecommerce", "online", "internet", "ecom", "card not present"],
    "ATM":      ["atm", "cash withdrawal", "cash w", "cash wd"],
}

KB_SCENARIOS = {
    "random":       ["random", "default", "normal"],
    "chargebacks":  ["chargeback", "chargebacks", "dispute", "disputes"],
    "recon_break":  ["recon break", "recon mismatch", "should not match", "do not match", "don't match",
                     "mismatch", "break"],
    "high_value":   ["high value", "high-value", "large amount", "big amount"],
    "on_us":        ["on us", "on-us", "onus"],
    "atm_mix":      ["atm mix", "mixed atm"],
}

# What files each (channel, network) tuple produces
KB_FILE_PLAN = {
    # (channel, network) → list of generators to invoke
    ("POS",  "MC"):    ["tlf",  "cbs_mc", "ep747_or_t140"],   # placeholder for T112/T140 if added later
    ("ECOM", "MC"):    ["tlf",  "cbs_mc"],
    ("ATM",  "MC"):    ["t464", "cbs_mc"],
    ("POS",  "VISA"):  ["ptlf", "epin",   "cbs_visa", "ep747"],
    ("ECOM", "VISA"):  ["ptlf", "epin",   "cbs_visa", "ep747"],
    ("ATM",  "VISA"):  ["epin", "cbs_visa", "fss_gl"],
}

# Forbidden combinations — for guardrails
KB_FORBIDDEN = [
    ("ATM", "T112",  "T112 is for merchant terminal transactions only. For ATM use T464."),
    ("ECOM", "T464", "T464 is for ATM cash transactions only. For e-commerce use T112 (MC) or PTLF (Visa)."),
    ("ON_US", "VISA","On Us transactions don't go through Visa — they settle internally. Did you mean Off Us Visa?"),
    ("ON_US", "MC",  "On Us transactions don't go through Mastercard — they settle internally. Did you mean Off Us MC?"),
]


# ---------------------------------------------------------------------------
# PARSER — free-form prompt → ParsedConfig
# ---------------------------------------------------------------------------

NUMBER_WORDS = {
    "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "thousand": 1000, "million": 1_000_000,
}


@dataclass
class DateBucket:
    date_yyyymmdd: str
    count: int


@dataclass
class ParsedConfig:
    count: Optional[int] = None
    networks: List[str] = field(default_factory=list)   # ["MC"], ["VISA"], or ["MC", "VISA"]
    network_split: Dict[str, int] = field(default_factory=dict)   # {"MC": 4000, "VISA": 6000}
    role: Optional[str] = None
    channel: Optional[str] = None
    date: Optional[str] = None                  # YYYYMMDD
    date_buckets: List[DateBucket] = field(default_factory=list)
    scenarios: List[Tuple[str, int, Optional[str]]] = field(default_factory=list)
    # tuples: (scenario_kind, count, on_date_yyyymmdd or None)
    currency: str = "INR"
    institution: str = ""
    raw_prompt: str = ""
    errors: List[str] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)


def _parse_count(text: str) -> Optional[int]:
    """Find a transaction count — supports digits, 5k, 5K, twenty thousand."""
    # Digit form: 10000, 5,000, 5_000
    m = re.search(r'\b([\d,_]+)\s*(transactions?|txns?|records?)?\b', text, re.IGNORECASE)
    if m:
        s = m.group(1).replace(",", "").replace("_", "")
        if s.isdigit() and int(s) > 0:
            return int(s)
    # 5k / 5K / 10k
    m = re.search(r'\b(\d+(?:\.\d+)?)\s*([kKmM])\b', text)
    if m:
        n = float(m.group(1))
        mult = 1000 if m.group(2).lower() == "k" else 1_000_000
        return int(n * mult)
    # twenty thousand
    for word, val in NUMBER_WORDS.items():
        m = re.search(rf'\b(\w+\s+)?{word}\b', text, re.IGNORECASE)
        if m and val >= 1000:
            preceding = m.group(1) or ""
            preceding = preceding.strip().lower()
            mult_word = NUMBER_WORDS.get(preceding, 1)
            return mult_word * val
    return None


def _parse_currency(text: str) -> str:
    for ccy in ("INR", "USD", "EUR", "GBP", "AED", "SGD"):
        if re.search(rf'\b{ccy}\b', text, re.IGNORECASE):
            return ccy
    if re.search(r'\b(rupees?|rs\.?)\b', text, re.IGNORECASE):
        return "INR"
    if re.search(r'\b(dollars?)\b', text, re.IGNORECASE):
        return "USD"
    return "INR"


def _parse_networks(text: str) -> Tuple[List[str], Dict[str, int]]:
    """Returns (networks_list, split_dict)."""
    found = []
    for canon, aliases in KB_NETWORKS.items():
        for alias in aliases:
            if re.search(rf'\b{re.escape(alias)}\b', text, re.IGNORECASE):
                if canon not in found:
                    found.append(canon)
                break
    # Detect split: "60/40", "70-30", or "6000 Visa and 4000 Mastercard"
    split = {}
    if len(found) >= 2:
        m = re.search(r'(\d{1,3})\s*/\s*(\d{1,3})', text)
        if m:
            split[found[0]] = int(m.group(1))
            split[found[1]] = int(m.group(2))
        # Explicit counts: "6000 Visa", "4000 Mastercard"
        for canon, aliases in KB_NETWORKS.items():
            for alias in aliases:
                m = re.search(rf'(\d+)\s+{re.escape(alias)}', text, re.IGNORECASE)
                if m:
                    split[canon] = int(m.group(1))
                    break
    return found, split


def _parse_role(text: str) -> Optional[str]:
    for canon, aliases in KB_ROLES.items():
        for alias in aliases:
            if re.search(rf'\b{re.escape(alias)}\b', text, re.IGNORECASE):
                return canon
    return None


def _parse_channel(text: str) -> Optional[str]:
    # Order matters — check ECOM before POS (since "ecommerce" contains nothing matching POS but be safe)
    for canon in ("ECOM", "ATM", "POS"):
        for alias in KB_CHANNELS[canon]:
            if re.search(rf'\b{re.escape(alias)}\b', text, re.IGNORECASE):
                return canon
    return None


def _parse_dates(text: str) -> Tuple[Optional[str], List[DateBucket]]:
    """Returns (single_date, list_of_date_buckets) — buckets used for date splits."""
    formats = ["%Y%m%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y",
               "%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
               "%d %B %y", "%d %b %y"]

    def try_parse(s: str) -> Optional[str]:
        s = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s).strip()
        for f in formats:
            try: return datetime.strptime(s, f).strftime("%Y%m%d")
            except ValueError: continue
        return None

    # Collect all date-shaped substrings
    date_re = re.compile(
        r"(\d{8}"
        r"|\d{1,2}[\-\/]\d{1,2}[\-\/]\d{2,4}"
        r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{2,4}"
        r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2}(?:st|nd|rd|th)?\s+\d{2,4})",
        re.IGNORECASE,
    )

    # Date split: "100 on 25 March 2026, 4000 on 28/03/26"
    buckets: List[DateBucket] = []
    bucket_re = re.compile(r"(\d+)\s+on\s+([\w\s\/\-,]+?)(?=,|$|\sand\s)", re.IGNORECASE)
    for m in bucket_re.finditer(text):
        cnt = int(m.group(1))
        date_str = m.group(2).strip().rstrip(",.")
        d = try_parse(date_str)
        if d:
            buckets.append(DateBucket(date_yyyymmdd=d, count=cnt))

    # Single date — first match
    single: Optional[str] = None
    for m in date_re.finditer(text):
        d = try_parse(m.group(0))
        if d:
            single = d
            break
    return single, buckets


def _parse_scenarios(text: str) -> List[Tuple[str, int, Optional[str]]]:
    """Detect scenario mix like '2000 chargebacks on 30 March, 1000 reversals on 2 April'."""
    out: List[Tuple[str, int, Optional[str]]] = []
    formats = ["%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d/%m/%y", "%Y%m%d",
               "%d-%m-%Y", "%d-%m-%y"]
    for kind, aliases in KB_SCENARIOS.items():
        for alias in aliases:
            pattern = rf'(\d+)\s+{re.escape(alias)}(?:\s+on\s+([\w\s\/\-]+?))?(?=,|$|\sand\s|\s+\d|\Z)'
            for m in re.finditer(pattern, text, re.IGNORECASE):
                cnt = int(m.group(1))
                d = None
                if m.group(2):
                    s = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", m.group(2)).strip()
                    for f in formats:
                        try: d = datetime.strptime(s, f).strftime("%Y%m%d"); break
                        except ValueError: continue
                out.append((kind, cnt, d))
    return out


def _parse_institution(text: str) -> str:
    banks = ["HDFC", "ICICI", "Axis", "SBI", "IDFC", "Kotak", "Yes Bank", "PNB",
             "Bank of Baroda", "Canara", "Union Bank", "IndusInd"]
    for b in banks:
        if re.search(rf'\b{re.escape(b)}\b', text, re.IGNORECASE):
            return b
    return ""


def parse(prompt: str) -> ParsedConfig:
    cfg = ParsedConfig(raw_prompt=prompt)
    cfg.count       = _parse_count(prompt)
    cfg.currency    = _parse_currency(prompt)
    cfg.networks, cfg.network_split = _parse_networks(prompt)
    cfg.role        = _parse_role(prompt)
    cfg.channel     = _parse_channel(prompt)
    cfg.date, cfg.date_buckets = _parse_dates(prompt)
    cfg.scenarios   = _parse_scenarios(prompt)
    cfg.institution = _parse_institution(prompt)

    # Defaults per prompt spec
    if cfg.role is None and (cfg.count or cfg.networks):
        cfg.role = "ACQUIRING"
    if cfg.channel is None:
        cfg.channel = "POS"   # default if not specified
    return cfg


# ---------------------------------------------------------------------------
# VALIDATOR / GUARDRAILS
# ---------------------------------------------------------------------------

def validate(cfg: ParsedConfig) -> ParsedConfig:
    # Missing required fields → questions (one at a time)
    if not cfg.count and not cfg.date_buckets:
        cfg.questions.append("How many transactions do you need?")
        return cfg
    if not cfg.networks:
        cfg.questions.append("Which network — Mastercard, Visa, RuPay, or NFS?")
        return cfg
    if len(cfg.networks) >= 2 and not cfg.network_split:
        cfg.questions.append(
            f"You mentioned {' and '.join(cfg.networks)} — how do you want to split? "
            f"For example 50/50, or a specific count like '6000 {cfg.networks[0]} and "
            f"4000 {cfg.networks[1]}'."
        )
        return cfg
    if not cfg.date and not cfg.date_buckets:
        cfg.questions.append("What date should these transactions be on?")
        return cfg

    # Forbidden combinations
    if cfg.role == "ON_US" and cfg.networks and any(n in ("MC", "VISA") for n in cfg.networks):
        for n in cfg.networks:
            if n in ("MC", "VISA"):
                cfg.errors.append(
                    f"On Us transactions don't go through {n} — they settle internally between "
                    f"branches of the same bank. Did you mean Off Us {n}, or On Us with no network?"
                )
        return cfg

    if cfg.channel == "ATM" and cfg.networks == ["MC"]:
        # T464 territory — no problem
        pass

    if cfg.channel == "ECOM" and "MC" in cfg.networks:
        # T112 territory — no T464 confusion needed; this is fine
        pass

    return cfg


# ---------------------------------------------------------------------------
# RESOLVER — ParsedConfig → list of (script, args)
# ---------------------------------------------------------------------------

def _yyyymmdd_to_arg(d: str) -> str:
    return d   # already YYYYMMDD


# Flags each script supports — used to filter args before invoking
_SCRIPT_FLAGS = {
    # ── Visa switch / network ──
    "ptlf_generator_v2.py":    {"--num-txns","--date","--currency","--testcase","--seed","--output","--institution","--validate","--random"},
    "epin_generator.py":       {"--num-txns","--date","--currency","--testcase","--seed","--output","--member-id","--reversal-offset","--validate","--random"},
    "ep747_generator.py":      {"--num-txns","--date","--currency","--testcase","--seed","--output","--member-id","--bin"},
    # ── Mastercard switch / network ──
    "tlf_generator.py":        {"--num-txns","--date","--currency","--testcase","--seed","--output","--institution","--validate","--random"},
    "mc_t112_generator.py":    {"--num-txns","--date","--seed","--output","--testcase","--currency"},
    "mc_t140_generator.py":    {"--num-txns","--date","--seed","--output","--testcase","--currency"},
    "mc_t464_generator.py":    {"--num-txns","--date","--seed","--output","--testcase","--currency"},
    "t464_generator_v2.py":    {"--num-txns","--date","--currency","--testcase","--seed","--output","--reversal-offset","--validate","--random"},
    "mci_ar_generator.py":     {"--num-txns","--date","--seed","--output","--testcase","--currency"},
    "t461_generator.py":       {"--num-txns","--date","--seed","--output","--side","--validate","--random"},
    # ── Shared CBS / GL ──
    "cbs_generator.py":        {"--num-txns","--date","--testcase","--seed","--output","--network","--random"},
    "fss_gl_out_generator.py": {"--num-txns","--date","--testcase","--seed","--output","--network","--random"},
    # ── NFS ──
    "fig_b2c_generator.py":      {"--num-txns","--date","--seed","--output","--validate","--random"},
    "ntsl_generator.py":         {"--date","--seed","--output","--bank-name","--random"},
    "nfs_adjustment_generator.py": {"--num-txns","--date","--seed","--output","--random"},
    "verifireversal_generator.py": {"--num-txns","--date","--seed","--output","--random"},
    # ── RuPay ──
    "rupay_88_generator.py":   {"--num-txns","--date","--seed","--output","--category","--member-inst-cd","--version","--file-seq","--random"},
    "rupay_dsr_generator.py":  {"--date","--seed","--output","--random"},
}


def _filter_args(script: str, args: List[str]) -> List[str]:
    """Drop CLI flags the target script doesn't recognise (each gen has different flags)."""
    allowed = _SCRIPT_FLAGS.get(script, set())
    out: List[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            if a in allowed:
                out.append(a)
                if i + 1 < len(args) and not args[i+1].startswith("--"):
                    out.append(args[i+1])
                    i += 2; continue
            else:
                # Drop this flag and its value if any
                if i + 1 < len(args) and not args[i+1].startswith("--"):
                    i += 2; continue
            i += 1
        else:
            out.append(a); i += 1
    return out


# ─────────────────────────────────────────────────────────────────────────
# ROUTING TABLE — single source of truth for which generators run for each
# (network, channel, role) combination. Matches the documented coverage
# matrix exactly. Use channel=None or role=None for "any" wildcard.
#
# Each entry in the recipe list is (script, extra_args, label, output_ext).
# The orchestrator adds --num-txns, --date, --seed, --currency, --testcase,
# and --output for every generator from a single shared common_args block,
# then layers extra_args on top. _filter_args() drops any flag the target
# script doesn't recognise so per-generator CLI surface stays clean.
# ─────────────────────────────────────────────────────────────────────────

ROUTING_TABLE = {
    # ───── Mastercard ─────
    ("MC", "POS", "ACQUIRING"): [
        ("mc_t112_generator.py",     [],                            "T112 (MC IPM)",          "txt"),
        ("ptlf_generator_v2.py",     [],                            "PTLF (MC POS switch)",   "txt"),
        ("cbs_generator.py",         ["--network", "MC"],           "CBS (MC)",               "txt"),
        ("fss_gl_out_generator.py",  ["--network", "MC"],           "FSS GL OUT (MC)",        "txt"),
        ("mc_t140_generator.py",     [],                            "T140 (MC settlement)",   "txt"),
    ],
    ("MC", "POS", "ISSUING"): [
        ("mc_t112_generator.py",     [],                            "T112 (MC IPM)",          "txt"),
        ("ptlf_generator_v2.py",     [],                            "PTLF (MC POS switch)",   "txt"),
        ("cbs_generator.py",         ["--network", "MC"],           "CBS (MC)",               "txt"),
        ("fss_gl_out_generator.py",  ["--network", "MC"],           "FSS GL OUT (MC)",        "txt"),
        ("mc_t140_generator.py",     [],                            "T140 (MC settlement)",   "txt"),
    ],
    ("MC", "ATM", "ACQUIRING"): [
        ("t464_generator_v2.py",     [],                            "T464 (MC ATM)",          "txt"),
        ("tlf_generator.py",         [],                            "TLF (MC switch)",        "txt"),
        ("cbs_generator.py",         ["--network", "MC"],           "CBS (MC)",               "txt"),
        ("fss_gl_out_generator.py",  ["--network", "MC"],           "FSS GL OUT (MC)",        "txt"),
        ("mci_ar_generator.py",      [],                            "T057 (MCI.AR DCR — ACQ)","txt"),
        ("t461_generator.py",        ["--side", "ACQ"],             "T461 (hourly perf — ACQ)","txt"),
    ],
    ("MC", "ATM", "ISSUING"): [
        ("t464_generator_v2.py",     [],                            "T464 (MC ATM)",          "txt"),
        ("tlf_generator.py",         [],                            "TLF (MC switch)",        "txt"),
        ("cbs_generator.py",         ["--network", "MC"],           "CBS (MC)",               "txt"),
        ("fss_gl_out_generator.py",  ["--network", "MC"],           "FSS GL OUT (MC)",        "txt"),
        ("t461_generator.py",        ["--side", "ISS"],             "T461 (hourly perf — ISS)","txt"),
    ],

    # ───── Visa ─────
    ("VISA", "POS", "ACQUIRING"): [
        ("epin_generator.py",        [],                            "EPIN (Visa BASE II)",    "txt"),
        ("ptlf_generator_v2.py",     [],                            "PTLF (Visa switch)",     "txt"),
        ("cbs_generator.py",         ["--network", "VISA"],         "CBS (Visa)",             "txt"),
        ("fss_gl_out_generator.py",  ["--network", "VISA"],         "FSS GL OUT (Visa)",      "txt"),
        ("ep747_generator.py",       [],                            "EP747 (VSS bundle)",     "txt"),
    ],
    ("VISA", "POS", "ISSUING"): [
        ("epin_generator.py",        [],                            "EPIN (Visa BASE II)",    "txt"),
        ("ptlf_generator_v2.py",     [],                            "PTLF (Visa switch)",     "txt"),
        ("cbs_generator.py",         ["--network", "VISA"],         "CBS (Visa)",             "txt"),
        ("fss_gl_out_generator.py",  ["--network", "VISA"],         "FSS GL OUT (Visa)",      "txt"),
        ("ep747_generator.py",       [],                            "EP747 (VSS bundle)",     "txt"),
    ],
    ("VISA", "ATM", "ACQUIRING"): [
        ("epin_generator.py",        [],                            "EPIN (Visa BASE II)",    "txt"),
        ("tlf_generator.py",         [],                            "TLF (Visa ATM switch)",  "txt"),
        ("cbs_generator.py",         ["--network", "VISA"],         "CBS (Visa)",             "txt"),
        ("fss_gl_out_generator.py",  ["--network", "VISA"],         "FSS GL OUT (Visa)",      "txt"),
        ("ep747_generator.py",       [],                            "EP747 (VSS bundle)",     "txt"),
    ],
    ("VISA", "ATM", "ISSUING"): [
        ("epin_generator.py",        [],                            "EPIN (Visa BASE II)",    "txt"),
        ("tlf_generator.py",         [],                            "TLF (Visa ATM switch)",  "txt"),
        ("cbs_generator.py",         ["--network", "VISA"],         "CBS (Visa)",             "txt"),
        ("fss_gl_out_generator.py",  ["--network", "VISA"],         "FSS GL OUT (Visa)",      "txt"),
        ("ep747_generator.py",       [],                            "EP747 (VSS bundle)",     "txt"),
    ],

    # ───── NFS (channel-flexible: AEPS / Micro-ATM / etc.) ─────
    ("NFS", None, None): [
        ("fig_b2c_generator.py",     [],                            "FIG B2C TRAXN",          "csv"),
        ("cbs_generator.py",         ["--network", "NFS"],          "CBS (NFS)",              "txt"),
        ("ntsl_generator.py",        [],                            "NTSL Daily Settlement",  "xlsx"),
    ],

    # ───── RuPay ─────
    ("RUPAY", "POS", None): [
        ("rupay_88_generator.py",    ["--category", "P"],           "RuPay 88 (XML)",         "xml"),
        ("ptlf_generator_v2.py",     [],                            "PTLF (RuPay POS switch)","txt"),
        ("cbs_generator.py",         ["--network", "RUPAY"],        "CBS (RuPay)",            "txt"),
        ("rupay_dsr_generator.py",   [],                            "RuPay DSR",              "xlsx"),
    ],
    ("RUPAY", "ATM", None): [
        ("rupay_88_generator.py",    ["--category", "A"],           "RuPay 88 (XML)",         "xml"),
        ("tlf_generator.py",         [],                            "TLF (RuPay ATM switch)", "txt"),
        ("cbs_generator.py",         ["--network", "RUPAY"],        "CBS (RuPay)",            "txt"),
        ("rupay_dsr_generator.py",   [],                            "RuPay DSR",              "xlsx"),
    ],
}


def _lookup_recipe(net: str, channel: Optional[str], role: Optional[str]):
    """Find best-matching recipe in routing table. Tries exact → role-wildcard
    → channel-wildcard → both-wildcard. Returns None if no match."""
    for key in [(net, channel, role),
                (net, channel, None),
                (net, None,    None)]:
        if key in ROUTING_TABLE:
            return ROUTING_TABLE[key]
    return None


def _validate_plan(plan, cfg, per_net):
    """Assertion-based sanity checks per spec — fail loudly if routing went wrong."""
    scripts_per_net = {}
    for net in per_net:
        scripts_per_net[net] = [s for s, _, _ in plan]   # plan is global; we don't split per-net here
    all_scripts = [s for s, _, _ in plan]

    role = (cfg.role or "").upper()
    channel = (cfg.channel or "").upper()

    if "MC" in cfg.networks and channel == "ATM" and role == "ACQUIRING":
        assert "mci_ar_generator.py" in all_scripts, "MC ATM acquiring must include T057 (mci_ar)"
        assert "t461_generator.py"   in all_scripts, "MC ATM acquiring must include T461"
        assert "mc_t140_generator.py" not in all_scripts, "MC ATM acquiring must NOT include T140"
    if "MC" in cfg.networks and channel == "ATM" and role == "ISSUING":
        assert "mci_ar_generator.py" not in all_scripts, "MC ATM issuing must NOT include T057"
        assert "t461_generator.py"   in all_scripts, "MC ATM issuing must include T461"
        assert "mc_t140_generator.py" not in all_scripts, "MC ATM issuing must NOT include T140"
    if "MC" in cfg.networks and channel in ("POS", "ECOM"):
        assert "ptlf_generator_v2.py" in all_scripts, "MC POS must include PTLF"
        assert "tlf_generator.py"     not in all_scripts, "MC POS must NOT include TLF"
    if "RUPAY" in cfg.networks and channel == "POS":
        assert "ptlf_generator_v2.py" in all_scripts, "RuPay POS must include PTLF"
    if "RUPAY" in cfg.networks and channel == "ATM":
        assert "tlf_generator.py"     in all_scripts, "RuPay ATM must include TLF"
    if "VISA" in cfg.networks and channel == "ATM":
        assert "tlf_generator.py"        in all_scripts, "Visa ATM must include TLF"
        assert "ptlf_generator_v2.py"    not in all_scripts, "Visa ATM must NOT include PTLF"
    if "VISA" in cfg.networks and channel in ("POS", "ECOM"):
        assert "ptlf_generator_v2.py"    in all_scripts, "Visa POS must include PTLF"
        assert "tlf_generator.py"        not in all_scripts, "Visa POS must NOT include TLF"
    if "MC" in cfg.networks and channel in ("POS", "ECOM"):
        assert "mc_t112_generator.py"    in all_scripts, "MC POS must include T112"
        assert "t464_generator_v2.py"    not in all_scripts, "MC POS must NOT include T464"
    if "MC" in cfg.networks and channel == "ATM":
        assert "t464_generator_v2.py"    in all_scripts, "MC ATM must include T464"
        assert "mc_t112_generator.py"    not in all_scripts, "MC ATM must NOT include T112"
    if "MC" in cfg.networks or "VISA" in cfg.networks:
        assert "fss_gl_out_generator.py" in all_scripts, "MC/Visa routes must include FSS GL OUT"
        assert "cbs_generator.py"        in all_scripts, "All routes must include CBS"
    if "NFS" in cfg.networks:
        assert "fig_b2c_generator.py"    in all_scripts, "NFS must include FIG B2C"
        assert "mc_t112_generator.py"    not in all_scripts, "NFS must NOT include T112"
        assert "epin_generator.py"       not in all_scripts, "NFS must NOT include EPIN"
    if "RUPAY" in cfg.networks:
        assert "rupay_88_generator.py"   in all_scripts, "RuPay must include RuPay 88 XML"
        assert "fig_b2c_generator.py"    not in all_scripts, "RuPay must NOT include FIG B2C"


def resolve_files(cfg: ParsedConfig) -> List[Tuple[str, List[str], str]]:
    """Resolve per-prompt config to the exact list of (script, args, label) to run.

    Routing is driven by ROUTING_TABLE (above) keyed on (network, channel, role).
    Same --seed is passed to every generator in the batch so RRNs and join keys
    correlate across all output files. Scenario flags (reversal/chargeback/
    merchandise_credit/on_us/recon_break) layer onto the base routing by setting
    the --testcase flag — generators handle each case internally.
    """
    plan: List[Tuple[str, List[str], str]] = []

    date = cfg.date or (cfg.date_buckets[0].date_yyyymmdd if cfg.date_buckets else
                        datetime.now().strftime("%Y%m%d"))

    # ── Per-network counts ──────────────────────────────────────────────
    if cfg.network_split:
        per_net = dict(cfg.network_split)
    elif len(cfg.networks) == 1:
        per_net = {cfg.networks[0]: cfg.count or sum(b.count for b in cfg.date_buckets) or 1000}
    else:
        total = cfg.count or 1000
        share = total // max(1, len(cfg.networks))
        per_net = {n: share for n in cfg.networks}

    # ── SEED rule (non-negotiable per spec) ─────────────────────────────
    # Single seed for the entire batch so all files correlate via RRN.
    # If user supplies one in the future via cfg.seed we'd take that; for now
    # we use a deterministic 42 so reproducible across runs.
    seed = "42"

    # ── Scenario detection ──────────────────────────────────────────────
    scenario_kinds = {s[0].lower() for s in cfg.scenarios} if cfg.scenarios else set()
    has_reversal           = bool(scenario_kinds & {"reversal", "reversals"})
    has_chargeback         = bool(scenario_kinds & {"chargeback", "chargebacks"})
    has_dispute            = bool(scenario_kinds & {"dispute", "disputes"}) or has_chargeback
    has_late_reversal      = bool(scenario_kinds & {"late_reversal", "late reversal"})
    has_merchandise_credit = bool(scenario_kinds & {"merchandise_credit", "merchandise credit"})
    has_on_us              = "on_us" in scenario_kinds or (cfg.role or "").upper() == "ON_US"
    has_recon_break        = "recon_break" in scenario_kinds
    has_high_value         = "high_value" in scenario_kinds

    # Pick the testcase flag value based on scenario (priority order matters).
    # Generators that don't recognise the value get "random" via _filter_args
    # / their own argparse choices.
    if   has_chargeback:         testcase = "chargebacks"
    elif has_reversal:           testcase = "chargebacks"   # existing reversal path lives under chargebacks testcase
    elif has_merchandise_credit: testcase = "chargebacks"   # MC pairs added by epin under chargebacks path
    elif has_on_us:              testcase = "on_us"
    elif has_recon_break:        testcase = "recon_break"
    elif has_high_value:         testcase = "high_value"
    elif (cfg.channel or "").upper() == "ATM": testcase = "atm_mix"
    else:                        testcase = "random"

    role    = (cfg.role or "ACQUIRING").upper()
    channel = (cfg.channel or "POS").upper()
    if channel == "ECOM": channel_lookup = "POS"
    else:                 channel_lookup = channel

    def _add(script, args, label):
        plan.append((script, _filter_args(script, args), label))

    # ── Build base file list per network from routing table ─────────────
    for net, count in per_net.items():
        if count <= 0: continue
        recipe = _lookup_recipe(net, channel_lookup, role)
        if recipe is None:
            raise ValueError(f"No routing for ({net}, {channel}, {role}). "
                             f"Add an entry to ROUTING_TABLE.")
        common_args = ["--num-txns", str(count), "--date", date,
                       "--currency", cfg.currency, "--testcase", testcase,
                       "--seed", seed]
        for script, extra, label, ext in recipe:
            stem = script.replace("_generator", "").replace("_v2", "").replace(".py", "")
            out_path = f"out_{date}/{stem}_{net.lower()}.{ext}"
            args = list(common_args) + list(extra) + ["--output", out_path]
            _add(script, args, label)

    # ── NFS conditional adds: dispute → Adjustment, late reversal → VeriFireversal ──
    if "NFS" in cfg.networks:
        nfs_count = per_net.get("NFS", cfg.count or 1000)
        nfs_common = ["--num-txns", str(nfs_count), "--date", date, "--seed", seed]
        if has_dispute:
            _add("nfs_adjustment_generator.py",
                 nfs_common + ["--output", f"out_{date}/nfs_adjustment.xlsx"],
                 "NFS Adjustment Report (dispute)")
        if has_late_reversal or has_reversal:
            _add("verifireversal_generator.py",
                 nfs_common + ["--output", f"out_{date}/nfs_verifireversal.xlsx"],
                 "NFS VeriFireversal (late reversal)")

    # ── Validate the resolved plan against spec assertions ──────────────
    _validate_plan(plan, cfg, per_net)

    return plan


# ---------------------------------------------------------------------------
# CONFIRMATION + ORCHESTRATION
# ---------------------------------------------------------------------------

def format_summary(cfg: ParsedConfig, plan: List[Tuple[str, List[str], str]]) -> str:
    files = " + ".join(label for _, _, label in plan) if plan else "(none)"
    bits = [
        f"{cfg.count or 'N/A'} txns",
        " + ".join(cfg.networks) or "?",
        cfg.role or "ACQUIRING",
        cfg.channel or "POS",
        cfg.currency,
        cfg.date or "DATE_MISSING",
    ]
    if cfg.network_split:
        bits.append("split " + " / ".join(f"{n}:{c}" for n, c in cfg.network_split.items()))
    if cfg.scenarios:
        bits.append("scenario mix " + ", ".join(f"{c} {k}" for k, c, _ in cfg.scenarios))
    if cfg.institution:
        bits.append(cfg.institution)
    line1 = " | ".join(bits)
    return f"Understood → {files}\n             {line1}"


def _zip_and_deliver(out_dir: Path, label: Optional[str] = None) -> Optional[Path]:
    """Zip the generated output dir and drop it in ~/Downloads/. Reveal in Finder on macOS."""
    import zipfile, platform
    if not out_dir.exists() or not any(out_dir.iterdir()):
        return None
    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = label or out_dir.name
    zip_path = downloads / f"{base}_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in out_dir.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(out_dir.parent))
    # Reveal in Finder on macOS
    if platform.system() == "Darwin":
        try:
            subprocess.run(["open", "--reveal", str(zip_path)], check=False, timeout=5)
        except Exception:
            pass
    return zip_path


def _build_zip_label(cfg) -> str:
    """e.g. paygen_MC_ACQ_ATM_1000_20260507"""
    parts = ["paygen"]
    if cfg.networks:
        parts.append("-".join(cfg.networks))
    if cfg.role:
        parts.append({"ACQUIRING": "ACQ", "ISSUING": "ISS", "ON_US": "ONUS"}.get(cfg.role, cfg.role))
    if cfg.channel:
        parts.append(cfg.channel)
    if cfg.count:
        parts.append(f"{cfg.count}txn")
    if cfg.date:
        parts.append(cfg.date)
    return "_".join(parts)


def run_plan(plan: List[Tuple[str, List[str], str]], dry_run: bool = False,
             auto_download: bool = True, zip_label: Optional[str] = None) -> int:
    if not plan:
        print("  Nothing to generate.")
        return 0
    print()
    rc = 0
    out_dirs: set = set()
    for script, args, label in plan:
        path = GEN_DIR / script
        if not path.exists():
            print(f"  [skip] {label} — generator not found: {path.name}")
            continue
        cmd = [sys.executable, str(path)] + args
        # Ensure output dir exists + track it for zipping
        for i, a in enumerate(args):
            if a == "--output" and i + 1 < len(args):
                p = Path(args[i + 1])
                p.parent.mkdir(parents=True, exist_ok=True)
                out_dirs.add(p.parent.resolve())
        print(f"  [run]  {label}")
        print(f"         $ {' '.join(shlex.quote(c) for c in cmd)}")
        if dry_run:
            continue
        # Run from project root so legacy generators (mc_t112, mc_t140, mc_t464,
        # mci_ar) can `from data_generator import Transaction`. Standalone gens
        # are unaffected.
        env = os.environ.copy()
        env["PYTHONPATH"] = str(GEN_DIR.parent) + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(cmd, capture_output=True, text=True,
                                cwd=str(GEN_DIR.parent), env=env)
        if result.returncode != 0:
            rc = 1
            print(f"         FAILED:\n{result.stderr}")
        else:
            for ln in result.stdout.strip().splitlines():
                print(f"         {ln}")

    # Auto-download — zip output dirs and drop in ~/Downloads/
    if not dry_run and auto_download and rc == 0:
        for od in sorted(out_dirs):
            zip_path = _zip_and_deliver(od, zip_label)
            if zip_path:
                print(f"\n  📦 Downloaded → {zip_path}")
    return rc


# ---------------------------------------------------------------------------
# MAIN — interactive REPL or one-shot
# ---------------------------------------------------------------------------

EXAMPLES = [
    "5000 Mastercard acquiring transactions, INR, 1 April 2026",
    "10000 Visa issuing e-commerce, HDFC Bank, 26/03/26",
    "2000 ATM transactions with reversals on the next day",
    "10000 acquiring transactions, Visa and Mastercard 60/40, INR, 26 March 2026",
]


def interactive(initial_prompt: str = "", dry_run: bool = False,
                auto_download: bool = True) -> int:
    print("PayGen NL — describe what files you need.")
    print("Type 'quit' to exit; 'examples' to see prompt samples.\n")

    prompt = initial_prompt
    cfg: Optional[ParsedConfig] = None

    while True:
        if not prompt:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print(); return 0
            if not prompt: continue
            if prompt.lower() in ("quit", "exit"): return 0
            if prompt.lower() == "examples":
                print("\nExample prompts:")
                for e in EXAMPLES: print(f"  • {e}")
                print()
                continue

        # If we have prior context and this looks like a follow-up (just a number, date, etc.),
        # append it to the prior prompt
        if cfg and cfg.questions and not re.search(r"transactions?|txns?", prompt, re.IGNORECASE):
            prompt = cfg.raw_prompt + ", " + prompt

        cfg = validate(parse(prompt))

        if cfg.errors:
            print("\nPushback:")
            for e in cfg.errors:
                print(f"  ⚠ {e}")
            cfg = None; prompt = ""; continue

        if cfg.questions:
            # Ask only the FIRST question (one at a time per spec)
            print(f"\n  {cfg.questions[0]}")
            prompt = ""
            continue

        # Resolved — show confirmation
        plan = resolve_files(cfg)
        print()
        print(format_summary(cfg, plan))
        try:
            ans = input("\nProceed? [Y/n/edit]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(); return 0
        if ans in ("", "y", "yes"):
            return run_plan(plan, dry_run=dry_run, auto_download=auto_download,
                             zip_label=_build_zip_label(cfg))
        elif ans in ("edit", "e"):
            cfg = None; prompt = ""; continue
        else:
            print("  cancelled.")
            return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="PayGen Natural Language input layer")
    p.add_argument("--prompt", default="", help="one-shot prompt (skip REPL)")
    p.add_argument("--dry-run", action="store_true",
                   help="show resolved plan but don't run generators")
    p.add_argument("--json", action="store_true",
                   help="emit parsed config as JSON and exit")
    p.add_argument("--no-download", action="store_true",
                   help="don't auto-zip and copy to ~/Downloads/ (default: do)")
    args = p.parse_args(argv)

    if args.json:
        if not args.prompt:
            print("--json requires --prompt", file=sys.stderr); return 2
        cfg = validate(parse(args.prompt))
        print(json.dumps({
            "count": cfg.count,
            "networks": cfg.networks,
            "network_split": cfg.network_split,
            "role": cfg.role,
            "channel": cfg.channel,
            "date": cfg.date,
            "date_buckets": [(b.date_yyyymmdd, b.count) for b in cfg.date_buckets],
            "scenarios": cfg.scenarios,
            "currency": cfg.currency,
            "institution": cfg.institution,
            "errors": cfg.errors,
            "questions": cfg.questions,
        }, indent=2))
        return 0

    return interactive(args.prompt, dry_run=args.dry_run,
                       auto_download=not args.no_download)


if __name__ == "__main__":
    sys.exit(main())
