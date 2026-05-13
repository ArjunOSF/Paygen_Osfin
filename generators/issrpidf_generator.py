"""
issrpidf_generator.py
=====================
NFS ISSRPIDF (Issuer / Acquirer raw data file) — built from the official NPCI
file-format spec (250Issuer Rawdata File Format.pdf / 250Acquirer Rawdata File
Format.pdf) rather than from a template patch.

Issuer file:    407 chars/record (.mIDF when IDFC is receiver)
Acquirer file:  274 chars/record

Fields are emitted by 1-indexed start-position per the PDF. Common header
[1:157] is shared; pos 158+ differs between roles.

File naming (NFS-OSG Table 6):
  Zip:      NFSRawdataIDF{DDMMYY}.zip
  Issuer:   250ISSuerIDF{DDMMYY}.mIDF
  Acquirer: 250ACQuirerIDF{DDMMYY}.mIDF

Real-file distributions (from sample of 19,133 records, used as defaults):
  Txn Type:  04=91%, 05=8%, 07=0.7%, 08=0.25%
  From Acct: 02=79%, 01=10%, 00=9%, 03=<1%
  MCC:       6011=95%, 6013=4%, 6012=1%
  To Acct:   always blank (single-leg withdrawal)
  Member:    always blank (issuer side)
  Network:   ATM
  Currency:  356 (INR)
"""

from __future__ import annotations
import argparse, csv, json, os, random, sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Spec — common header [1:157] same in Issuer + Acquirer
# Tuples: (name, start_1idx, length, kind) — kind: 'char' (left, space-pad) /
#         'num' (right, zero-pad) / 'pan' (left, space-pad)
# ---------------------------------------------------------------------------

COMMON_FIELDS = [
    ("participant_id",   1,   3, "char"),
    ("txn_type",         4,   2, "char"),
    ("from_acct",        6,   2, "char"),
    ("to_acct",          8,   2, "char"),
    ("rrn",             10,  12, "num"),
    ("resp_code",       22,   2, "char"),
    ("pan",             24,  19, "pan"),
    ("member",          43,   1, "char"),
    ("approval",        44,   6, "char"),
    ("stan",            50,  12, "num"),
    ("txn_date",        62,   6, "num"),
    ("txn_time",        68,   6, "num"),
    ("mcc",             74,   4, "num"),
    ("acpt_settle_dt",  78,   6, "num"),
    ("card_acpt_id",    84,  15, "char"),
    ("atm_id",          99,   8, "char"),
    ("term_location",  107,  40, "char"),
    ("acquirer_id",    147,  11, "char"),
]

# Issuer-only [158+]
ISSUER_FIELDS = [
    ("network_id",     158,   3, "char"),
    ("acct1_no",       161,  19, "char"),
    ("acct1_branch",   180,  10, "char"),
    ("acct2_no",       190,  19, "char"),
    ("acct2_branch",   209,  10, "char"),
    ("txn_curr",       219,   3, "num"),
    ("txn_amt",        222,  15, "num"),
    ("actual_amt",     237,  15, "num"),
    ("txn_activity_fee", 252, 15, "num"),
    ("iss_curr",       267,   3, "num"),
    ("iss_settle_amt", 270,  15, "num"),
    ("iss_settle_fee", 285,  15, "num"),
    ("iss_settle_proc_fee", 300, 15, "num"),
    ("ch_billing_curr", 315,  3, "char"),
    ("ch_billing_amt", 318,  15, "num"),
    ("ch_billing_activity_fee", 333, 15, "num"),
    ("ch_billing_proc_fee",     348, 15, "num"),
    ("ch_billing_svc_fee",      363, 15, "num"),
    ("conv_rate_issuer",        378, 15, "num"),
    ("conv_rate_cardholder",    393, 15, "num"),
]
ISSUER_LEN = 407

# Acquirer-only [158+]
ACQUIRER_FIELDS = [
    ("acq_settle_dt",  158,   6, "num"),
    ("txn_curr",       164,   3, "char"),
    ("txn_amt",        167,  15, "num"),
    ("actual_amt",     182,  15, "num"),
    ("txn_activity_fee", 197, 15, "num"),
    ("acq_settle_curr", 212,   3, "char"),
    ("acq_settle_amt", 215,  15, "num"),
    ("acq_settle_fee", 230,  15, "num"),
    ("acq_settle_proc_fee", 245, 15, "num"),
    ("conv_rate_acquirer",  260, 15, "num"),
]
ACQUIRER_LEN = 274


# ---------------------------------------------------------------------------
# Field placement
# ---------------------------------------------------------------------------

def _place(buf: List[str], value, start_1idx: int, length: int, kind: str):
    s = start_1idx - 1
    if kind == "num":
        v = str(int(value or 0)).zfill(length)[-length:]   # right-justified, zero-pad
    elif kind == "pan":
        v = str(value).ljust(length)[:length]              # left-justified, space-pad (19 chars)
    else:   # char
        v = str(value).ljust(length)[:length]
    for i, ch in enumerate(v):
        buf[s + i] = ch


def build_record(values: Dict, fields: List[Tuple], total_len: int) -> str:
    buf = [" "] * total_len
    for name, pos, ln, kind in fields:
        v = values.get(name, "")
        if kind == "num" and v == "":
            v = 0
        _place(buf, v, pos, ln, kind)
    return "".join(buf)


# ---------------------------------------------------------------------------
# Case matrix — 18 cases (CBS in {1, NULL} × Switch in {1, 0, NULL} × NPCI ditto)
# ---------------------------------------------------------------------------

CASES = [
    # case, cbs, switch, npci, action
    (1,  1,    1,    1,    "CLOSED — all matched"),
    (2,  1,    1,    0,    "Refund to customer — GL to CASA"),
    (3,  1,    1,    None, "Refund to customer — GL to CASA"),
    (4,  1,    0,    1,    "Force match — CLOSED"),
    (5,  1,    0,    0,    "Refund to customer — GL to CASA"),
    (6,  1,    0,    None, "Refund to customer — GL to CASA"),
    (7,  1,    None, 1,    "Force match — CLOSED"),
    (8,  1,    None, 0,    "Refund to customer — GL to CASA"),
    (9,  1,    None, None, "Refund to customer — GL to CASA"),
    (10, None, 1,    1,    "Recovery from customer — CASA to GL"),
    (11, None, 1,    0,    "No action — CLOSED"),
    (12, None, 1,    None, "No action — CLOSED"),
    (13, None, 0,    1,    "Recovery from customer — CASA to GL"),
    (14, None, 0,    0,    "No action — CLOSED"),
    (15, None, 0,    None, "No action — CLOSED"),
    (16, None, None, 1,    "Recovery from customer — CASA to GL"),
    (17, None, None, 0,    "No action — CLOSED"),
    (18, None, None, None, "No action — CLOSED"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _luhn_pan(prefix: str, length: int, rng: random.Random) -> str:
    body = prefix + "".join(str(rng.randint(0,9)) for _ in range(length-1-len(prefix)))
    digits = [int(d) for d in body+"0"]
    odd = digits[-1::-2]; even = digits[-2::-2]
    chk = (sum(odd) + sum(sum(divmod(d*2,10)) for d in even)) % 10
    return body + str((10-chk)%10)


def _yymmdd(yyyymmdd: str) -> str:
    return yyyymmdd[2:]


def _normalize_date(s: str) -> str:
    s = s.strip()
    # 6-digit → DDMMYY (NFS convention); 8-digit → YYYYMMDD; else try all
    if len(s) == 6 and s.isdigit():
        fmts = ["%d%m%y"]
    elif len(s) == 8 and s.isdigit():
        fmts = ["%Y%m%d", "%d%m%Y"]
    else:
        fmts = ["%Y%m%d","%d%m%y","%d-%m-%Y","%d/%m/%Y","%Y-%m-%d","%d%m%Y"]
    for f in fmts:
        try: return datetime.strptime(s, f).strftime("%Y%m%d")
        except ValueError: continue
    raise ValueError(f"unrecognised date: {s!r}")


def _ddmmyy(yyyymmdd: str) -> str:
    return yyyymmdd[6:8] + yyyymmdd[4:6] + yyyymmdd[2:4]


# Real-file distributions
TXN_TYPES = ["04"]*91 + ["05"]*8 + ["07"] + ["08"]   # 100 weighted
FROM_ACCTS = ["02"]*79 + ["01"]*10 + ["00"]*9 + ["03"]*2
MCCS = ["6011"]*95 + ["6013"]*4 + ["6012"]
ATM_PREFIXES = ["S5NM", "S5NL", "S5BW", "6NSA", "EN  ", "PMT2"]
CITIES = [("MUMBAI","MH"),("CHENNAI","TN"),("DELHI","DL"),("BANGALORE","KA"),
          ("KOLKATA","WB"),("PUNE","MH"),("HYDERABAD","TG"),("GURUGRAM","HR")]
BRANCHES = ["MIG BHEL","PERAMBUR CASHPOINT","BANJARA HILLS","SECTOR 17",
            "MG ROAD","BANDRA WEST","SALT LAKE SEC 3","KORAMANGALA",
            "ANNA NAGAR","JAYANAGAR"]


def _build_values(rrn: str, pan: str, amount_paise: int, business_date: str,
                  rng: random.Random, npci: Optional[int], role: str,
                  participant_id: str = "IDF") -> Dict:
    """Compose field-value dict for one transaction record."""
    yymmdd = _yymmdd(business_date)
    next_day = datetime.strptime(business_date, "%Y%m%d")
    branch, state = rng.choice(CITIES)
    location = f"{rng.choice(BRANCHES):<22}{branch:<13}{state}IN"[:40]
    term_pfx = rng.choice(ATM_PREFIXES).strip() or "S5"
    term_id8 = str(rng.randint(10_000_000, 99_999_999))
    card_acpt_id = f"{term_pfx}{term_id8[:11]:<11}"[:15]
    hh = rng.randint(0,23); mm = rng.randint(0,59); ss = rng.randint(0,59)

    # Response Code by NPCI flag
    if npci == 1:
        resp = "00"
    elif npci == 0:
        resp = rng.choice(["04", "51", "55", "91", "FR"])
    else:
        resp = "  "

    # If NPCI=0 (failure) → amount fields zero
    amt = amount_paise if npci != 0 else 0

    return {
        "participant_id":  participant_id,
        "txn_type":        rng.choice(TXN_TYPES),
        "from_acct":       rng.choice(FROM_ACCTS),
        "to_acct":         "  ",
        "rrn":             rrn,
        "resp_code":       resp,
        "pan":             pan,
        "member":          " ",
        "approval":        str(rng.randint(100000, 999999)),
        "stan":            str(rng.randint(10**11, 10**12 - 1)),
        "txn_date":        yymmdd,
        "txn_time":        f"{hh:02d}{mm:02d}{ss:02d}",
        "mcc":             rng.choice(MCCS),
        "acpt_settle_dt":  yymmdd,
        "card_acpt_id":    card_acpt_id,
        "atm_id":          term_id8,
        "term_location":   location,
        "acquirer_id":     f"{rng.randint(600000,699999):>6}     ",
        # issuer fields
        "network_id":      "ATM",
        "acct1_no":        str(rng.randint(10**14, 10**15-1)),
        "acct1_branch":    "0000000001",
        "acct2_no":        "",
        "acct2_branch":    "",
        "txn_curr":        "356",
        "txn_amt":         amt,
        "actual_amt":      amt,
        "txn_activity_fee": 0,
        "iss_curr":        "356",
        "iss_settle_amt":  amt,
        "iss_settle_fee":  0,
        "iss_settle_proc_fee": 0,
        "ch_billing_curr": "   ",
        "ch_billing_amt":  amt,
        "ch_billing_activity_fee": 0,
        "ch_billing_proc_fee": 0,
        "ch_billing_svc_fee": 0,
        "conv_rate_issuer": 0,
        "conv_rate_cardholder": 0,
        # acquirer fields
        "acq_settle_dt":   yymmdd,
        "acq_settle_curr": "356",
        "acq_settle_amt":  amt,
        "acq_settle_fee":  0,
        "acq_settle_proc_fee": 0,
        "conv_rate_acquirer":  0,
    }


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_day(business_date: str, txns_per_case: int, role: str,
                 seed: Optional[int] = None,
                 participant_id: str = "IDF",
                 cases: List = None) -> Tuple[List[Dict], List[str], List[str]]:
    """role in {'issuer','acquirer','both'}. Returns (master_rows, issuer_lines, acquirer_lines)."""
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    cases = cases or CASES
    rrn_start = rng.randint(600_000_000_000, 699_999_999_999)

    master = []
    iss_lines = []
    acq_lines = []
    rrn_idx = 0
    for case_num, cbs, sw, npci, action in cases:
        for _ in range(txns_per_case):
            rrn = str(rrn_start + rrn_idx).zfill(12)
            rrn_idx += 1
            pan = _luhn_pan("4", 16, rng)
            amount = rng.randint(50, 5000) * 10000   # paise, ATM multiples of ₹100

            vals = _build_values(rrn, pan, amount, business_date, rng, npci, role,
                                  participant_id)
            # Add case context
            master.append({
                "case_num": case_num, "rrn": rrn, "pan": pan,
                "amount_paise": amount, "date": business_date,
                "mcc": vals["mcc"], "atm_id": vals["atm_id"],
                "cbs_status": "" if cbs is None else cbs,
                "switch_status": "" if sw is None else sw,
                "npci_status": "" if npci is None else npci,
                "action": action,
            })
            # Emit ISSRPIDF only when NPCI is not NULL (NULL = transaction missing)
            if npci is not None:
                if role in ("issuer","both"):
                    iss_lines.append(build_record(vals, COMMON_FIELDS + ISSUER_FIELDS, ISSUER_LEN))
                if role in ("acquirer","both"):
                    acq_lines.append(build_record(vals, COMMON_FIELDS + ACQUIRER_FIELDS, ACQUIRER_LEN))
    return master, iss_lines, acq_lines


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_files(master, iss_lines, acq_lines, business_date: str,
                participant_id: str, out_dir: str, role: str) -> Dict:
    os.makedirs(out_dir, exist_ok=True)
    ddmmyy = _ddmmyy(business_date)
    pid = participant_id
    paths = {}
    if iss_lines:
        p = os.path.join(out_dir, f"250ISSuer{pid}{ddmmyy}.m{pid}")
        with open(p, "w", encoding="utf-8", newline="") as f:
            for l in iss_lines:
                assert len(l) == ISSUER_LEN, f"issuer record len {len(l)} != {ISSUER_LEN}"
                f.write(l + "\n")
        paths["issuer"] = p
    if acq_lines:
        p = os.path.join(out_dir, f"250ACQuirer{pid}{ddmmyy}.m{pid}")
        with open(p, "w", encoding="utf-8", newline="") as f:
            for l in acq_lines:
                assert len(l) == ACQUIRER_LEN, f"acquirer record len {len(l)} != {ACQUIRER_LEN}"
                f.write(l + "\n")
        paths["acquirer"] = p
    # master_table
    m = os.path.join(out_dir, f"master_table_{pid}{ddmmyy}.csv")
    with open(m, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="|")
        w.writerow(["case_num","rrn","pan","amount_paise","date","mcc",
                    "atm_id","cbs_status","switch_status","npci_status","action"])
        for r in master:
            w.writerow([r["case_num"], r["rrn"], r["pan"], r["amount_paise"],
                        r["date"], r["mcc"], r["atm_id"], r["cbs_status"],
                        r["switch_status"], r["npci_status"], r["action"]])
    paths["master_table"] = m
    return paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="NFS ISSRPIDF — Issuer/Acquirer raw data (NPCI spec)")
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"),
                   help="business date — accepts YYYYMMDD, DDMMYY, etc.")
    p.add_argument("--txns-per-case", type=int, default=10,
                   help="transactions per case (×18 cases = total)")
    p.add_argument("--role", choices=["issuer","acquirer","both"], default="both")
    p.add_argument("--participant-id", default="IDF", help="3-char member code")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--output-dir", default="out_nfs")
    p.add_argument("--days", type=int, default=1,
                   help="number of consecutive days to generate (one zip per day)")
    p.add_argument("--zip", action="store_true",
                   help="package each day into NFSRawdataIDF{DDMMYY}.zip")
    # legacy compat — accept --num-txns and split across 18 cases
    p.add_argument("--num-txns", type=int, default=None,
                   help="legacy alias — divides into txns_per_case across 18 cases")
    p.add_argument("--mcc", default=None,
                   help="legacy alias — single-MCC mode (no case matrix)")
    p.add_argument("--output", default=None,
                   help="legacy single-file output path; uses role=issuer if set")
    args = p.parse_args(argv)

    bdate = _normalize_date(args.date)
    pid = args.participant_id

    # Legacy single-file mode (back-compat for paygen_nl router)
    if args.output:
        rng = random.Random(args.seed)
        rrn_start = rng.randint(600_000_000_000, 699_999_999_999)
        N = args.num_txns or (args.txns_per_case * 18)
        lines = []
        master = []
        for i in range(N):
            rrn = str(rrn_start + i).zfill(12)
            pan = _luhn_pan("4", 16, rng)
            amount = rng.randint(50, 5000) * 10000
            vals = _build_values(rrn, pan, amount, bdate, rng, 1, "issuer", pid)
            if args.mcc:
                vals["mcc"] = args.mcc
            lines.append(build_record(vals, COMMON_FIELDS + ISSUER_FIELDS, ISSUER_LEN))
            master.append({"case_num":1,"rrn":rrn,"pan":pan,"amount_paise":amount,
                           "date":bdate,"mcc":vals["mcc"],"atm_id":vals["atm_id"],
                           "cbs_status":1,"switch_status":1,"npci_status":1,
                           "action":"matched"})
        with open(args.output, "w", encoding="utf-8") as f:
            for l in lines: f.write(l + "\n")
        base, _ = os.path.splitext(args.output)
        with open(base + "_master_table.csv","w",newline="",encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["rrn","pan","amount_paise","mcc","atm_id"])
            for r in master:
                w.writerow([r["rrn"], r["pan"], r["amount_paise"], r["mcc"], r["atm_id"]])
        with open(base + "_totals.json","w",encoding="utf-8") as f:
            json.dump({"num_records":len(lines),"record_length":ISSUER_LEN,
                       "spec":"NPCI Issuer 407-char"}, f, indent=2)
        print(f"  wrote {len(lines)} ISSRPIDF records ({ISSUER_LEN} chars each) → {args.output}")
        print(f"  master table → {base}_master_table.csv")
        return 0

    # 18-case mode — multi-day loop
    from datetime import timedelta
    import zipfile
    base_dt = datetime.strptime(bdate, "%Y%m%d")
    for d in range(args.days):
        day_dt = base_dt + timedelta(days=d)
        day_bdate = day_dt.strftime("%Y%m%d")
        day_seed = (args.seed + d) if args.seed is not None else None
        master, iss, acq = generate_day(day_bdate, args.txns_per_case, args.role,
                                         seed=day_seed, participant_id=pid)
        paths = write_files(master, iss, acq, day_bdate, pid, args.output_dir, args.role)
        print(f"  day {d+1}/{args.days}  {day_bdate} ({_ddmmyy(day_bdate)}) — {len(master)} txns, {sum(1 for r in master if r['npci_status'] != '')} emitted")
        for k, v in paths.items():
            print(f"    {k:>12}: {v}")
        if args.zip:
            ddmmyy = _ddmmyy(day_bdate)
            zp = os.path.join(args.output_dir, f"NFSRawdata{pid}{ddmmyy}.zip")
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for k, v in paths.items():
                    if k in ("issuer", "acquirer"):
                        zf.write(v, arcname=os.path.basename(v))
            print(f"    {'zip':>12}: {zp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
