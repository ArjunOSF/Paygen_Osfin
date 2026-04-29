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
    "ptlf_generator_v2.py":    {"--num-txns","--date","--currency","--testcase","--seed","--output","--institution","--validate","--random"},
    "tlf_generator.py":        {"--num-txns","--date","--currency","--testcase","--seed","--output","--institution","--validate","--random"},
    "epin_generator.py":       {"--num-txns","--date","--currency","--testcase","--seed","--output","--member-id","--reversal-offset","--validate","--random"},
    "cbs_generator.py":        {"--num-txns","--date","--testcase","--seed","--output","--network","--random"},
    "fss_gl_out_generator.py": {"--num-txns","--date","--testcase","--seed","--output","--network","--random"},
    "ep747_generator.py":      {"--num-txns","--date","--currency","--testcase","--seed","--output","--member-id","--bin"},
    "t464_generator_v2.py":    {"--num-txns","--date","--currency","--testcase","--seed","--output","--reversal-offset","--validate","--random"},
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


def resolve_files(cfg: ParsedConfig) -> List[Tuple[str, List[str], str]]:
    """Returns list of (script_name, cli_args, label) ready to invoke.
    Only generates files for what's in scope from the current generator suite."""
    plan: List[Tuple[str, List[str], str]] = []
    def _add(script: str, args: List[str], label: str) -> None:
        plan.append((script, _filter_args(script, args), label))
    date = cfg.date or (cfg.date_buckets[0].date_yyyymmdd if cfg.date_buckets else
                        datetime.now().strftime("%Y%m%d"))

    # Determine per-network counts
    if cfg.network_split:
        per_net = dict(cfg.network_split)
    elif len(cfg.networks) == 1:
        per_net = {cfg.networks[0]: cfg.count or sum(b.count for b in cfg.date_buckets)}
    else:
        # Default 50/50 if not specified
        total = cfg.count or 0
        share = total // len(cfg.networks)
        per_net = {n: share for n in cfg.networks}

    # Determine testcase
    testcase = "random"
    if cfg.scenarios:
        kinds = [s[0] for s in cfg.scenarios]
        if "chargebacks" in kinds: testcase = "chargebacks"
        elif "recon_break" in kinds: testcase = "recon_break"
        elif "high_value" in kinds: testcase = "high_value"
    if cfg.channel == "ATM":
        testcase = "atm_mix"

    common_args = ["--date", date, "--currency", cfg.currency, "--testcase", testcase]
    if cfg.role:
        # we don't have a --role flag in v1 — encoded via testcase choice
        pass

    for net, count in per_net.items():
        if count <= 0: continue
        net_args = common_args + ["--num-txns", str(count), "--seed", "42"]

        if net == "VISA":
            if cfg.channel in ("POS", "ECOM"):
                _add("ptlf_generator_v2.py", net_args + ["--output", f"out_{date}/ptlf_visa.txt"], "PTLF (Visa switch)")
            _add("epin_generator.py", net_args + ["--output", f"out_{date}/epin.txt"], "EPIN (Visa BASE II)")
            _add("cbs_generator.py", net_args + ["--network", "VISA", "--output", f"out_{date}/cbs_visa.txt"], "CBS (Visa)")
            _add("ep747_generator.py", net_args + ["--output", f"out_{date}/ep747.txt"], "EP747 (Visa VSS bundle)")

        elif net == "MC":
            if cfg.channel == "ATM":
                _add("t464_generator_v2.py", net_args + ["--output", f"out_{date}/t464.txt"], "T464 (MC ATM acquiring)")
            else:
                _add("tlf_generator.py", net_args + ["--output", f"out_{date}/tlf_mc.txt"], "TLF (MC switch)")
            _add("cbs_generator.py", net_args + ["--network", "MC", "--output", f"out_{date}/cbs_mc.txt"], "CBS (Mastercard)")

    # FSS GL OUT for ATM scenarios
    if cfg.channel == "ATM":
        total = sum(per_net.values())
        _add("fss_gl_out_generator.py",
             common_args + ["--num-txns", str(total), "--seed", "42",
                            "--output", f"out_{date}/fss_gl_out.txt"], "FSS GL OUT")

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


def run_plan(plan: List[Tuple[str, List[str], str]], dry_run: bool = False) -> int:
    if not plan:
        print("  Nothing to generate.")
        return 0
    print()
    rc = 0
    for script, args, label in plan:
        path = GEN_DIR / script
        if not path.exists():
            print(f"  [skip] {label} — generator not found: {path.name}")
            continue
        cmd = [sys.executable, str(path)] + args
        # Ensure output dir exists
        for i, a in enumerate(args):
            if a == "--output" and i + 1 < len(args):
                Path(args[i + 1]).parent.mkdir(parents=True, exist_ok=True)
        print(f"  [run]  {label}")
        print(f"         $ {' '.join(shlex.quote(c) for c in cmd)}")
        if dry_run:
            continue
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            rc = 1
            print(f"         FAILED:\n{result.stderr}")
        else:
            for ln in result.stdout.strip().splitlines():
                print(f"         {ln}")
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


def interactive(initial_prompt: str = "", dry_run: bool = False) -> int:
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
            return run_plan(plan, dry_run=dry_run)
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

    return interactive(args.prompt, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
