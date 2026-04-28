"""
ep747_generator.py
==================
Visa EP747 VSS report bundle generator.

EP747 is Visa's equivalent of Mastercard T140 — a single text file containing
multiple VisaNet Settlement Service (VSS) reports packed together.

Source: real EP747 (2).txt sample (4205 lines, 157 reports across 7 types)

Each report has:
  Header:  "REPORT ID:  VSS-XXX  ...  PROC DATE: DDMMMYY  REPORT DATE: DDMMMYY"
  Trailer: "*** END OF VSS-XXX REPORT ***"

Reports in the bundle (real-file counts):
  VSS-110  Daily Settlement Summary                   17 instances
  VSS-120  Settlement Detail by Business Mode         16
  VSS-130  Settlement Detail by Currency              28
  VSS-140  Settlement Detail                          18
  VSS-210  Funds Transfer Summary                     16
  VSS-900  Full Daily Settlement Position              1 (master total)
  VSS-900-S Settlement Detail per Transaction Type    61 (most detailed)

Equivalence to T140:
  VSS-110   = T140 clearing day total section
  VSS-900-S = T140 FIRST PRES. notification section with txn-type breakdown
  VSS-900   = T140 cycle summary

Approach: real report templates are captured verbatim from the sample file
and embedded as-is. The generator substitutes the date fields (PROC DATE,
REPORT DATE) and member IDs to match user inputs. For total amounts, the
templates retain their original values from the source file — when run
alongside the EPIN generator with the same --seed, totals will not match
exactly (this is a known limitation flagged for a future enhancement).
"""

from __future__ import annotations
import argparse, json, os, re, sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Embedded report templates — captured verbatim from real EP747 file.
# Loaded lazily from the sidecar JSON file (kept separate to avoid 50KB+
# string literals in this module).
# ---------------------------------------------------------------------------

_TEMPLATES_PATH = Path(__file__).parent / "ep747_templates.json"

_INSTANCE_COUNTS = {
    "VSS-110":   17,
    "VSS-120":   16,
    "VSS-130":   28,
    "VSS-140":   18,
    "VSS-210":   16,
    "VSS-900":    1,
    "VSS-900-S": 61,
}

# Order in which reports appear in the bundle (matches real file)
_REPORT_ORDER = ["VSS-110", "VSS-120", "VSS-130", "VSS-140", "VSS-210",
                 "VSS-900-S", "VSS-900"]


def _load_templates() -> Dict[str, str]:
    if _TEMPLATES_PATH.exists():
        return json.loads(_TEMPLATES_PATH.read_text())
    raise FileNotFoundError(
        f"Templates file missing: {_TEMPLATES_PATH}\n"
        "Run scripts/extract_ep747_templates.py to regenerate from a sample file."
    )


def _normalize_date(s: str) -> str:
    s = s.strip().replace(",", "")
    fmts = ["%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y",
            "%d-%m-%y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"]
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s)
    for cand in (s, cleaned):
        for f in fmts:
            try: return datetime.strptime(cand, f).strftime("%Y%m%d")
            except ValueError: continue
    raise ValueError(f"unrecognised date: {s!r}")


def _ddmmm_yy(yyyymmdd: str) -> str:
    """Format YYYYMMDD as DDMMMYY (e.g. 20260224 → 24FEB26)."""
    d = datetime.strptime(yyyymmdd, "%Y%m%d")
    return d.strftime("%d%b%y").upper()


# ---------------------------------------------------------------------------
# Template substitution
# ---------------------------------------------------------------------------

def _substitute(template: str, date_ddmmmyy: str, member_id: str, bin_id: str) -> str:
    """Replace the date, member ID, and BIN in a report template."""
    out = template
    # Replace any DDMMMYY date pattern with the user's date.
    # Real templates have dates like '24FEB26', '23FEB26', '22FEB26'.
    out = re.sub(r'\b\d{2}[A-Z]{3}\d{2}\b', date_ddmmmyy, out)
    # Replace member IDs (10-digit) — keep generic 9000xxxxxx pattern.
    if member_id:
        out = re.sub(r'\b9000\d{6}\b', member_id, out)
    # Replace BIN
    if bin_id:
        out = re.sub(r'\bBIN\s+\d+\b', f'BIN {bin_id}', out)
    return out


def _build_report_instance(template: str, instance_idx: int,
                           date_ddmmmyy: str, member_id: str, bin_id: str) -> str:
    """Substitute and adjust page number per instance."""
    rep = _substitute(template, date_ddmmmyy, member_id, bin_id)
    # Adjust the page number — real bundle has sequential page numbers
    # but each report restarts at PAGE 1. Keep page 1 — simpler & matches reality.
    return rep


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(business_date: str, member_id: str = "9000375016",
             bin_id: str = "401561",
             num_txns: Optional[int] = None,
             currency: str = "INR",
             test_case: str = "random",
             seed: Optional[int] = None) -> str:
    """Generate a complete EP747 VSS report bundle as a single string."""
    templates = _load_templates()
    date_dd = _ddmmm_yy(business_date)

    # Optionally scale instance counts by num_txns (rough)
    counts = dict(_INSTANCE_COUNTS)
    if num_txns is not None and num_txns > 0:
        # Scale 110/120/130/140/210/900-S proportionally; keep 900 = 1
        scale = max(1, num_txns / 10000)   # baseline 10k txns ≈ real-file counts
        for k in counts:
            if k != "VSS-900":
                counts[k] = max(1, round(counts[k] * scale))

    output_parts: List[str] = []
    for vt in _REPORT_ORDER:
        if vt not in templates:
            continue
        for i in range(counts[vt]):
            instance = _build_report_instance(templates[vt], i, date_dd, member_id, bin_id)
            output_parts.append(instance)

    return "\n".join(output_parts) + "\n"


def write_outputs(content: str, out_path: str, business_date: str,
                  member_id: str, bin_id: str, currency: str) -> None:
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    # Totals JSON — counts of each report type in the output
    counts: Dict[str, int] = {}
    for vt in _REPORT_ORDER:
        counts[vt] = content.count(f"REPORT ID:  {vt}")
    summary = {
        "business_date": business_date,
        "member_id": member_id,
        "bin": bin_id,
        "currency": currency,
        "total_reports": sum(counts.values()),
        "reports_by_type": counts,
        "file_size_bytes": len(content),
        "line_count": content.count("\n"),
    }
    with open(base + "_totals.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Visa EP747 VSS report bundle generator")
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--member-id", default="9000375016",
                   help="Visa member ID (10 digits, typically 9000xxxxxx)")
    p.add_argument("--bin", default="401561", help="Visa BIN")
    p.add_argument("--num-txns", type=int, default=None,
                   help="if set, scales instance counts proportional to baseline 10k")
    p.add_argument("--currency", default="INR")
    p.add_argument("--testcase", default="random",
                   choices=["random", "acquiring", "issuing", "chargebacks",
                            "recon_break", "high_value", "on_us"])
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--output", default="ep747.txt")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    content = generate(bdate, args.member_id, args.bin, args.num_txns,
                       args.currency, args.testcase, args.seed)
    write_outputs(content, args.output, bdate, args.member_id, args.bin, args.currency)

    n_reports = sum(content.count(f"REPORT ID:  {vt}") for vt in _REPORT_ORDER)
    print(f"  wrote {n_reports} reports, {content.count(chr(10))} lines → {args.output}")
    print(f"  totals → {os.path.splitext(args.output)[0]}_totals.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
