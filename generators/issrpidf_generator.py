"""
issrpidf_generator.py
=====================
NFS Issuer Interchange (NPCI) raw file — fixed-width 407 chars per line.

Built from real sample (Downloads/out_20260401 3/  ISSRPIDF.txt, 19133 records).

Field positions (0-indexed, real-file confirmed):
  [0:4]     IDF0           institution
  [4:8]     subtype + ' '  '0402 '=MCC 6011, '0501 '=MCC 6012, '0400 '=ICCW
                           (also seen: 0401, 0403, 0500, 0502, 0503, 0700, 0800)
  [9:21]    NFS_RRN        12 digits — joins CBS.RRN
  [73:77]   MCC            6011 / 6012 / 6013
  [77:83]   txn_date       YYMMDD (e.g. 260329 = 29-Mar-2026)
  [83:87]   ATM_prefix     S5NM / S5NL / 6NSA etc.
  [87:99]   terminal_id    12 chars
  [108:130] location       (city/branch label)
  [130:142] city           12 chars
  [351:355] 'IDFB'         ICCW marker (only for MCC 6013)
  [365:377] UPI_RRN        12 digits — joins UPI_SWITCH.field[1] (ICCW only)

Total length: 407 chars per record (single-line, NOT multi-line).
"""

from __future__ import annotations
import argparse, csv, json, os, random, sys
from datetime import datetime
from typing import List, Optional

# Real-file template lines — both 407 chars exactly. Patched per record.
NON_ICCW_TEMPLATE = "IDF0402  608723010285284013470502069810    5422862803260102852603282300006011260329S5NM002256623  02256623PERAMBUR CASHPOINT     CHENNAI      TNIN622018     ATM00000010083523655                                         356000000000060000000000000000000000000000000000356000000000000000000000000000000000000000000000   000000000000000000000000000000000000000000000000000000000000000000000000100000000000000000"
ICCW_TEMPLATE     = "IDF0400  10048248003500        10155606099 4800352803260224032603282301256013260329S5NL021053621  21053621MIG BHEL               RAMACHANDRAPUTSIN622018     ATM                                                          356000000000150000000000000150000000000000000000356000000000150000000000000000000000000000000000   000000000000000000000000000000    IDFB0040101   907611549841000000000000100000000000000000"
assert len(NON_ICCW_TEMPLATE) == 407, f"non-ICCW template = {len(NON_ICCW_TEMPLATE)}"
assert len(ICCW_TEMPLATE)     == 407, f"ICCW template = {len(ICCW_TEMPLATE)}"

# Sub-type codes seen in real file (excludes ICCW which uses 0400 + IDFB marker)
NON_ICCW_SUBTYPES = ["0402", "0401", "0403", "0501", "0502", "0503", "0500", "0700", "0800"]
SUBTYPE_FOR_MCC = {"6011": ["0402", "0401", "0403"], "6012": ["0501", "0502", "0503"]}
ATM_PREFIXES = ["S5NM", "S5NL", "S5BW", "6NSA", "EN  ", "PMT2"]


def _normalize_date(s: str) -> str:
    fmts = ["%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"]
    for f in fmts:
        try: return datetime.strptime(s.strip(), f).strftime("%Y%m%d")
        except ValueError: continue
    raise ValueError(f"unrecognised date: {s!r}")


def _yymmdd(yyyymmdd: str) -> str:
    return yyyymmdd[2:]


def _patch(template: str, *patches) -> str:
    """patches: tuples of (pos_start, value). Each value overwrites bytes at that position.
    Final length must remain 407."""
    s = list(template)
    for start, value in patches:
        for i, ch in enumerate(value):
            s[start + i] = ch
    out = "".join(s)
    if len(out) != 407:
        raise ValueError(f"patched record length {len(out)} != 407")
    return out


def build_record(nfs_rrn: str, mcc: str, txn_date_yymmdd: str, atm_prefix: str,
                 upi_rrn: str = "") -> str:
    if mcc == "6013":
        # ICCW path — use ICCW template, patch NFS_RRN, MCC, ATM, UPI_RRN
        return _patch(
            ICCW_TEMPLATE,
            (9,  nfs_rrn.zfill(12)[:12]),
            (73, mcc),
            (77, txn_date_yymmdd),
            (83, atm_prefix.ljust(4)[:4]),
            (365, upi_rrn.zfill(12)[:12]),
        )
    else:
        subtype = SUBTYPE_FOR_MCC.get(mcc, ["0402"])[0]
        return _patch(
            NON_ICCW_TEMPLATE,
            (4,  subtype),
            (9,  nfs_rrn.zfill(12)[:12]),
            (73, mcc),
            (77, txn_date_yymmdd),
            (83, atm_prefix.ljust(4)[:4]),
        )


def generate(num_txns: int, business_date: str, mcc: str = "6011",
             seed: Optional[int] = None):
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    # First randint must match CBS so CBS.RRN == ISSRPIDF.NFS_RRN
    nfs_rrn_start = rng.randint(600_000_000_000, 699_999_999_999)
    upi_rrn_start = rng.randint(900_000_000_000, 999_999_999_999)
    txn_date = _yymmdd(business_date)

    rows = []
    out_lines = []
    for i in range(num_txns):
        nfs_rrn = str(nfs_rrn_start + i).zfill(12)
        upi_rrn = str(upi_rrn_start + i).zfill(12) if mcc == "6013" else ""
        atm_prefix = rng.choice(ATM_PREFIXES)
        line = build_record(nfs_rrn, mcc, txn_date, atm_prefix, upi_rrn)
        out_lines.append(line)
        rows.append({"nfs_rrn": nfs_rrn, "upi_rrn": upi_rrn, "mcc": mcc,
                     "atm_prefix": atm_prefix})
    return rows, out_lines


def write_outputs(rows, lines, out_path):
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")

    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nfs_rrn", "upi_rrn", "mcc", "atm_prefix"])
        for r in rows:
            w.writerow([r["nfs_rrn"], r["upi_rrn"], r["mcc"], r["atm_prefix"]])

    totals = {
        "num_records": len(rows),
        "mcc_breakdown": {},
        "format": "fixed-width 407 chars/line, single-line records",
        "iccw_count": sum(1 for r in rows if r["mcc"] == "6013"),
    }
    for r in rows:
        totals["mcc_breakdown"][r["mcc"]] = totals["mcc_breakdown"].get(r["mcc"], 0) + 1
    with open(base + "_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="NFS ISSRPIDF (NPCI raw) — 407-char fixed-width")
    p.add_argument("--num-txns", type=int, default=100)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--mcc", choices=["6011", "6012", "6013"], default="6011")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--output", default="issrpidf.txt")
    args = p.parse_args(argv)

    bdate = _normalize_date(args.date)
    rows, lines = generate(args.num_txns, bdate, args.mcc, args.seed)
    write_outputs(rows, lines, args.output)

    print(f"  wrote {len(lines)} ISSRPIDF records (407 chars each, MCC {args.mcc}) → {args.output}")
    if args.mcc == "6013":
        print(f"  ICCW: NFS_RRN at [9:21], IDFB marker at [351:355], UPI_RRN at [365:377]")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
