"""
t461_generator.py
=================
Mastercard T461 — Daily Processor Performance Report (machine-readable H/D/T).

Source: real T461 sample (header H202603021748321, ~21 D-records, T trailer).
Companion to T057 / mci_ar (the human-readable report covering the same data).

File structure:
  H{YYYYMMDD}{HHMMSS}{seq}                                    e.g. H202603021748321
  D{HHMM}{section_code}{FD/MD}{4 numeric fields}{9-padding}    e.g. D0030322FD000000010000000000000010000000000000010000000999999999999
  T{count_5}{control_total}                                    e.g. T00021000002420490721706

Where:
  HHMM         = time bucket (e.g. 0030 = 00:30, 0038 = 00:38, 0088 = 00:88 — these
                  are not strictly hours/minutes; they are processor-specific tick
                  identifiers in the real file)
  section_code = 3-digit category (322 = financial txn count, 402 = response time,
                  262 = inquiry count, 782 = stand-in, etc.)
  FD/MD        = card type:
                  FD = (legacy / failed disposition)
                  MD = Mastercard Debit
  4 fields     = 14-char zero-padded numeric metrics; trailing 9999999999999999
                  marks unused / null filler

Each H/D/T file represents one acquirer or issuer side of one calendar day.
The control_total in T = sum of (some fields across all D records) — exact rule
is processor-specific; we use a reproducible hash of D content for our generator
so a recon harness can validate.
"""

from __future__ import annotations
import argparse, csv, hashlib, json, os, random, re, sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

REC_LEN = 68    # observed line width — D records are 68 chars including newline

# Section codes seen in real file (mapped to meaning where known)
SECTIONS = {
    "262": "inquiry_count",
    "322": "financial_count",
    "402": "response_time_ms",
    "782": "standin_count",
    "842": "failed_count",
    "962": "availability_pct",
}


@dataclass
class T461Record:
    hhmm: str          # 4-char time bucket
    section: str       # 3-char section code
    card_type: str     # FD or MD
    fields: List[int]  # 4 numeric metrics


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


def _format_d_record(r: T461Record) -> str:
    """D + HHMM + section + card_type + 4×14-char zero-padded + 16 nines (filler)."""
    fields_str = "".join(str(f).zfill(14) for f in r.fields)
    # Pad / truncate to fixed width with trailing 9s
    body = f"D{r.hhmm}{r.section}{r.card_type}{fields_str}"
    return body + "9" * max(0, 68 - len(body))


def _format_h_record(business_date: str, file_seq: int = 1) -> str:
    """H + YYYYMMDD + HHMMSS + sequence (1 char)."""
    now = datetime.now()
    return f"H{business_date}{now.strftime('%H%M%S')}{file_seq}"


def _format_t_record(records: List[T461Record]) -> str:
    """T + 5-digit count + 18-digit control total.
    Control total is a deterministic checksum of all D-record numeric fields."""
    total = 0
    for r in records:
        total += sum(r.fields)
    # Fold to 18 digits
    ctrl = str(total).zfill(18)[-18:]
    return f"T{len(records):05d}{ctrl}"


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _make_records(num_txns: int, rng: random.Random,
                  acq_or_iss: str = "ACQ") -> List[T461Record]:
    """Generate ~21 D-records spanning hourly buckets and key sections."""
    records: List[T461Record] = []

    # Per-hour activity profile (tx counts ~peaks midday)
    hour_activity = [max(1, int(num_txns * w / 100)) for w in
                     [1, 1, 1, 1, 2, 3, 5, 7, 8, 9, 9, 8,
                      8, 7, 6, 5, 4, 4, 3, 3, 2, 2, 1, 1]]

    # Time buckets aren't strict HH:MM — real file uses 0030, 0038, 0088 etc.
    # We synthesize plausible buckets matching the observed pattern.
    buckets = ["0030", "0038", "0088", "0128", "0328", "0368", "0369",
               "0448", "0488", "0508", "0518", "0528", "0608", "0643",
               "0648", "0688", "0728", "0848", "0908", "0967", "0968"]

    for i, b in enumerate(buckets[:21]):
        hour_idx = min(23, int(b[:2]))
        n = hour_activity[hour_idx] + rng.randint(0, max(1, hour_activity[hour_idx]))
        avg_amount = rng.randint(50, 2500) * 100   # paise

        if i == 0:
            # First record — section 322 FD = file metadata header-ish
            records.append(T461Record(b, "322", "FD",
                [1, 0, 0, 1]))
        elif b in ("0368", "0488", "0728", "0967"):
            # Section 262 = inquiry count
            records.append(T461Record(b, "262", "FD",
                [n, 0, 0, n]))
        elif b in ("0488", "0508", "0518", "0528"):
            # Various sub-sections
            records.append(T461Record(b, str(rng.choice([322, 402, 782])), "MD",
                [n, n * avg_amount, 0, n]))
        else:
            # Standard MD record — financial transactions
            sec = "322"
            records.append(T461Record(b, sec, "MD",
                [n, n * avg_amount, n * avg_amount, 0]))

    return records


def generate(num_txns: int, business_date: str,
             seed: Optional[int] = None,
             acq_or_iss: str = "ACQ",
             validate: bool = True) -> Tuple[List[T461Record], List[str]]:
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    records = _make_records(num_txns, rng, acq_or_iss)

    out: List[str] = [_format_h_record(business_date)]
    out.extend(_format_d_record(r) for r in records)
    out.append(_format_t_record(records))

    if validate:
        # Verify D records are uniform width, T count matches, control total parses
        d_lines = [l for l in out if l.startswith("D")]
        widths = {len(l) for l in d_lines}
        assert len(widths) == 1, f"D records vary in width: {widths}"
        trailer = out[-1]
        assert trailer.startswith("T"), "trailer must start with T"
        cnt = int(trailer[1:6])
        assert cnt == len(d_lines), f"trailer count {cnt} != actual D count {len(d_lines)}"

    return records, out


def write_outputs(records: List[T461Record], lines: List[str],
                  out_path: str, business_date: str) -> None:
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        for l in lines:
            f.write(l + "\n")

    # Master table — flat view of records for downstream verification
    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["hhmm", "section", "section_label", "card_type",
                    "field_1", "field_2", "field_3", "field_4"])
        for r in records:
            w.writerow([r.hhmm, r.section, SECTIONS.get(r.section, "unknown"),
                        r.card_type, *r.fields])

    totals = {
        "business_date": business_date,
        "num_d_records": len(records),
        "control_total_field_sum": sum(sum(r.fields) for r in records),
        "card_type_counts": {
            "FD": sum(1 for r in records if r.card_type == "FD"),
            "MD": sum(1 for r in records if r.card_type == "MD"),
        },
        "section_counts": _counter([r.section for r in records]),
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
    p = argparse.ArgumentParser(description="Mastercard T461 Daily Processor Performance Report (H/D/T machine-readable)")
    p.add_argument("--num-txns", type=int, default=1000)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--side", choices=["ACQ", "ISS"], default="ACQ",
                   help="Acquiring or Issuing side report")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--random", action="store_true")
    p.add_argument("--validate", action="store_true", default=True)
    p.add_argument("--output", default="t461.txt")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    records, lines = generate(args.num_txns, bdate, args.seed,
                              args.side, args.validate)
    write_outputs(records, lines, args.output, bdate)
    print(f"  wrote {len(lines)} lines (1 H + {len(records)} D + 1 T) → {args.output}")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    print(f"  totals       → {os.path.splitext(args.output)[0]}_expected_totals.json")
    if args.validate:
        print(f"  validate     → H/D/T structure intact, control total computed [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
