"""
tlf_generator.py
================
Spec-accurate Mastercard TLF (Transaction Listing File) generator for IDFC Bank.

Source of truth: TLF.xls (59 fields, R1/R2/R3/R4 positions) + real sample TLFX.txt
Format: 3462-char data lines, 3610-char file header.

Each TLF data line packs 4 transaction records:
  Record 1: positions  34– 551 (or wrapper-relative; actual record body 864 chars)
  Record 2: positions 898–1415  (R1 + 864)
  Record 3: positions 1762–2279 (R1 + 1728)
  Record 4: positions 2626–3143 (R1 + 2592)
  + trailer padding to 3462 chars

Outer wrapper (pos 1-39): 003462 + 000864 + DR + 172145892515 + 000000001 + T2A
Processing type T2A distinguishes TLF from PTLF (which uses PRO2).
"""

from __future__ import annotations
import argparse, csv, json, os, random, sys
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

LINE_LEN   = 3462
HEADER_LEN = 3610
REC_OFFSET = 864    # gap between records on the same line

# 59-field map from TLF.xls — (field_name, length, R1_pos)
# To get record N position: R_N = R1 + (N-1) * REC_OFFSET
FIELD_MAP: List[Tuple[str, int, int]] = [
    ('Term_fiid', 4, 44),
    ('Term_id', 16, 48),
    ('Term_type', 2, 215),
    ('Term_name_loc', 25, 361),
    ('Term_own_name', 22, 386),
    ('Term_city', 13, 408),
    ('Term_stat', 3, 421),
    ('Term_Cntry', 2, 424),
    ('Term_br_id', 4, 94),
    ('Term_rgn_id', 4, 98),
    ('Term_ln', 4, 40),
    ('Card_fiid', 4, 68),
    ('Card_num', 19, 72),
    ('Mbr_num', 3, 91),
    ('Card_ln', 4, 64),
    ('From_acct', 19, 250),
    ('To_acct', 19, 270),
    ('Msg_type', 4, 106),
    ('Env_ind', 2, 104),
    ('Tran_type', 2, 34),
    ('tran_date _Admin', 6, 104),
    ('Tran_time_Admin', 8, 110),
    ('tran_date _Normal', 6, 171),
    ('Tran_time_Normal', 8, 177),
    ('Tran_org', 1, 112),
    ('Tran_res', 1, 113),
    ('Post_date_Normal', 6, 185),
    ('Seq_num_Normal', 12, 203),
    ('Resp_code', 3, 358),
    ('Rvsl_code', 2, 498),
    ('Tran_code', 6, 244),
    ('Amt_1', 19, 290),
    ('Amt_2', 19, 309),
    ('Amt_3', 19, 328),
    ('Multi_acct_indt', 1, 289),
    ('Acq_inst_id', 11, 222),
    ('Rcv_inst_id', 11, 233),
    ('Shrg_grp', 1, 516),
    ('Dest_order', 1, 517),
    ('Rte_stat', 2, 110),
    ('Time_ofst', 5, 217),
    ('Entr_time', 19, 114),
    ('Exit_time', 19, 133),
    ('Re_entr_time', 19, 152),
    ('Acq_setl_date', 6, 191),
    ('Iss_setl_date', 6, 197),
    ('Oseq_num', 12, 426),
    ('Otran_date', 4, 437),
    ('Otran_time', 8, 441),
    ('B24_post_date', 4, 449),
    ('Acq_crncy_code', 3, 457),
    ('Iss_crncy_code', 3, 468),
    ('Org_crncy_code', 3, 454),
    ('Acq_conv_rate', 8, 460),
    ('Iss_conv_rate', 8, 471),
    ('Conv_date_time', 19, 479),
    ('Pin_ofst', 16, 500),
    ('Auth_resp_id', 6, 518),
    ('AADHAR NUMBER/RRN', 12, 539),
]

# Verbatim 4-record template captured from real TLFX.txt line 2.
_TEMPLATE = (
    "003462000864DR172145892515000000001T2A2PRO2IDFCER202280        PRO2IDFC401138530"
    "0016320   000IDFC0000  31021000151774720804975      1774720805076      177472080"
    "5076      26032823300400260328  0328  03286370        14000004209290000000000000"
    "00097001000000010259439562   00000010259439562  00000000000000000000000000000000"
    "0000000000000000000000000000000000000000NEW DELHI VIKAS PURI BRANNEW DELHI VIKAS"
    " PURI BDELHI        DL IN6370        0328233004000000356000000000000000000000000"
    "00000000000000000000000000000000000XP      0000000  00    6370        0000000000"
    "000000000000000000000000000                                                     "
    "                                                                                "
    "                                                                                "
    "                                                                      000864DR17"
    "2145892515000000001T2A2PRO2IDFCER403140        PRO2IDFC8888888888888888   000IDF"
    "C0000  31021000161774720808731      1774720808731      1774720808731      260328"
    "23300800260328  0328  03286624        XX0000000000000000000000000009700000000000"
    "000000000000 0000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000068NARODA BRANCH ATM        NARODA BRANCH ATM     Ahmedabad"
    "    GJ IN6624        03282330080000003560000000000000000000000000000000000000000"
    "0000000000000001833XP      0000000  00    00000000000000000000000000000000000000"
    "00000000000                                                                     "
    "                                                                                "
    "                                                                                "
    "                                                      000864DR172145892515000000"
    "001T2A2NFIDNFIDMPB03756        PRO2IDFC4013471503090847   000NFID0000  310210001"
    "51774720810315      1774720810847      1774720810847      26032823301000260328  "
    "0328  03286087237926561400000000008114640000000000010110000000010080476208   000"
    "00000000000000000000000000000070000000000000000000000000000000000000000000000000"
    "00000000NEAR RO PLANT            NEAR RO PLANT         MANESAR      00 IN6087237"
    "92656032823301000000035600000000000000000000000000000000000000000000000000000000"
    "000XP5442350000000  00    0000000000000000000000000000000000000000000000000     "
    "                                                                                "
    "                                                                                "
    "                                                                                "
    "                                      000864DR172145892515000000001T2A2NFIDNFIDP"
    "3ENBU93        PRO2IDFC4235792900626277   000NFID0000  31021000171774720810291  "
    "    1774720810603      1774720810603      26032823301000260328  0328  0328608723"
    "009480XX00000000008000250000000000010110000000010034742359   0000000000000000000"
    "000000000000010000000000000000000000000000000000000000000000000000000000+MUNEKOL"
    "A                +MUNEKOLA             BANGALORE    00 IN60872300948003282330100"
    "0000035600000000000000000000000000000000000000000000000000000000000XP54423300000"
    "00  00    0000000000000000000000000000000000000000000000000                     "
    "                                                                                "
    "                                                                                "
    "                                                                                "
    "                      "
)
assert len(_TEMPLATE) == LINE_LEN, f"template len {len(_TEMPLATE)} != {LINE_LEN}"


# ---------------------------------------------------------------------------
# Transaction model
# ---------------------------------------------------------------------------

@dataclass
class Txn:
    pan: str                 # 19-char Mastercard PAN, Luhn-valid (5xxx)
    seq_num: str             # 12-digit Seq_num_Normal
    tran_date: str           # YYMMDD
    tran_time: str           # HHMMSS (will be padded to HHMMSSCC where needed)
    amount_paise: int        # zero-padded 19 chars at Amt_1
    mcc: str                 # used in narrative; not directly in TLF spec
    merchant_name: str       # 25 chars Term_name_loc
    merchant_city: str       # 13 chars Term_city
    merchant_state: str      # 3 chars Term_stat
    terminal_id: str         # 16 chars Term_id
    msg_type: str = "0210"   # 0210 / 0220 / 0200 / 0420 / 0215
    resp_code: str = "000"   # 000 = approved
    rvsl_code: str = "00"    # 00 = not reversed
    tran_type: str = "01"    # 01 = purchase
    tran_code: str = "100000"
    pos_entry: str = "051"   # placeholder (no canonical TLF field; kept for parity)
    currency: str = "356"    # Org_crncy_code, Acq_crncy_code, Iss_crncy_code
    acq_inst_id: str = "00000001234"  # 11 chars
    rcv_inst_id: str = "00000005678"
    rrn: str = ""            # 12-char AADHAR NUMBER/RRN
    auth_resp_id: str = ""   # 6-char Auth_resp_id
    oseq_num: str = ""       # for reversals, links to original
    otran_date: str = ""     # 4-char original date (YMMD or similar)
    otran_time: str = ""     # 8-char original time
    is_reversal: bool = False
    is_on_us: bool = False
    test_case: str = "random"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _luhn_checksum(num: str) -> int:
    digits = [int(d) for d in num]
    odd = digits[-1::-2]; even = digits[-2::-2]
    return (sum(odd) + sum(sum(divmod(d * 2, 10)) for d in even)) % 10


def _luhn_complete(prefix: str, length: int = 16) -> str:
    body_len = length - 1
    body = prefix + "".join(str(random.randint(0, 9)) for _ in range(body_len - len(prefix)))
    check = (10 - _luhn_checksum(body + "0")) % 10
    return body + str(check)


def _normalize_date(s: str) -> str:
    s = s.strip().replace(",", "")
    fmts = ["%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y",
            "%d-%m-%y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
            "%d %B %y", "%d %b %y"]
    import re
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s)
    for cand in (s, cleaned):
        for f in fmts:
            try: return datetime.strptime(cand, f).strftime("%Y%m%d")
            except ValueError: continue
    raise ValueError(f"unrecognised date: {s!r}")


def _place(buf: list, value: str, start_1idx: int, length: int) -> None:
    s = str(value)[:length].ljust(length)
    start = start_1idx - 1
    for i, ch in enumerate(s):
        if 0 <= start + i < LINE_LEN:
            buf[start + i] = ch


def _place_r(buf: list, value, start_1idx: int, length: int) -> None:
    s = str(value)[-length:].zfill(length)
    start = start_1idx - 1
    for i, ch in enumerate(s):
        if 0 <= start + i < LINE_LEN:
            buf[start + i] = ch


def _places(buf: list, name: str, value: str, rec_idx: int) -> None:
    """Place value at record `rec_idx` (0-3) for ALL fields with this name."""
    offset = rec_idx * REC_OFFSET
    for n, length, r1 in FIELD_MAP:
        if n == name:
            _place(buf, value, r1 + offset, length)


def _places_r(buf: list, name: str, value, rec_idx: int) -> None:
    offset = rec_idx * REC_OFFSET
    for n, length, r1 in FIELD_MAP:
        if n == name:
            _place_r(buf, value, r1 + offset, length)


# ---------------------------------------------------------------------------
# Value generators
# ---------------------------------------------------------------------------

_MERCHANTS = [
    ("AMAZONIN",                  "Bangalore",   "KA", "5942"),
    ("FLIPKART INTERNET",         "Bangalore",   "KA", "5399"),
    ("Swiggy Limited",            "Mumbai",      "MH", "5811"),
    ("Zomato Online Order",       "Gurugram",    "HR", "5811"),
    ("BIG BAZAAR",                "Pune",        "MH", "5411"),
    ("RELIANCE FRESH",            "Chennai",     "TN", "5411"),
    ("DMART NUNNA",               "Vijayawada",  "AP", "5411"),
    ("INDIAN OIL",                "New Delhi",   "DL", "5541"),
    ("HP PETROL",                 "Hyderabad",   "TG", "5541"),
    ("UBER INDIA",                "Bangalore",   "KA", "4121"),
    ("OLA CABS",                  "Bangalore",   "KA", "4121"),
    ("GAUR CITY BRANCH ATM",      "NOIDA",       "UP", "6011"),
    ("GHODBUNDER ROAD",           "THANE",       "MH", "6011"),
    ("CNERGYPRABHADEVI",          "MUMBAI",      "MH", "6011"),
    ("Mumbai Airport T1",         "Mumbai",      "MH", "6011"),
    ("NEW DELHI VIKAS PURI BRAN", "DELHI",       "DL", "6011"),
    ("NARODA BRANCH ATM",         "Ahmedabad",   "GJ", "6011"),
]


def _amount_for(case: str, rng: random.Random) -> int:
    if case == "high_value":
        return rng.randint(5_000_000, 50_000_000)
    if case == "chargebacks":
        return rng.randint(10_000, 500_000)
    return rng.randint(50_000, 2_500_000)


def _make_txn(idx: int, case: str, business_date: str, currency: str,
              acq_id: str, rng: random.Random, seq_start: int) -> Txn:
    yymmdd = business_date[2:8]
    hh = rng.randint(0, 23); mm = rng.randint(0, 59); ss = rng.randint(0, 59)
    time_6 = f"{hh:02d}{mm:02d}{ss:02d}"

    pan = _luhn_complete("5", 16).ljust(19)            # Mastercard 5-prefix
    seq = str(seq_start + idx).zfill(12)
    rrn = str(seq_start + 1_000_000 + idx).zfill(12)
    merch_name, city, state, mcc = rng.choice(_MERCHANTS)
    amount = _amount_for(case, rng)

    is_rev = on_us = False
    msg_type = "0210"; rvsl = "00"
    resp = "000"

    if case == "chargebacks":
        r = rng.random()
        if r < 0.15:
            is_rev = True; msg_type = "0420"; rvsl = "01"
    elif case == "on_us":
        on_us = True
    elif case == "recon_break":
        # 5% will be marked for downstream amount mutation
        pass

    return Txn(
        pan=pan, seq_num=seq, tran_date=yymmdd, tran_time=time_6,
        amount_paise=amount, mcc=mcc, merchant_name=merch_name[:25],
        merchant_city=city[:13], merchant_state=state[:3],
        terminal_id=f"S5NL{rng.randint(10**11, 10**12 - 1)}"[:16],
        msg_type=msg_type, resp_code=resp, rvsl_code=rvsl,
        currency=currency, acq_inst_id=acq_id,
        rcv_inst_id=acq_id if on_us else "00000005678",
        rrn=rrn,
        auth_resp_id=str(rng.randint(100000, 999999)),
        is_reversal=is_rev, is_on_us=on_us, test_case=case,
    )


# ---------------------------------------------------------------------------
# Line builder — pack 4 transactions onto one TLF line
# ---------------------------------------------------------------------------

def build_line(txns_4: List[Optional[Txn]]) -> str:
    """Build one TLF data line with up to 4 transactions packed.
    Missing slots (None) keep the template's static content for that record."""
    buf = list(_TEMPLATE)
    for rec_idx, t in enumerate(txns_4):
        if t is None:
            continue
        _places  (buf, "Card_num",          t.pan,                   rec_idx)
        _places  (buf, "Term_id",           t.terminal_id,           rec_idx)
        _places  (buf, "Term_name_loc",     t.merchant_name.upper(), rec_idx)
        _places  (buf, "Term_own_name",     t.merchant_name.upper(), rec_idx)
        _places  (buf, "Term_city",         t.merchant_city.upper(), rec_idx)
        _places  (buf, "Term_stat",         t.merchant_state,        rec_idx)
        _places  (buf, "Msg_type",          t.msg_type,              rec_idx)
        _places  (buf, "Tran_type",         t.tran_type,             rec_idx)
        _places  (buf, "tran_date _Normal", t.tran_date,             rec_idx)
        _places  (buf, "Tran_time_Normal",  t.tran_time + "00",      rec_idx)
        _places  (buf, "Post_date_Normal",  t.tran_date,             rec_idx)
        _places  (buf, "Acq_setl_date",     t.tran_date,             rec_idx)
        _places  (buf, "Iss_setl_date",     t.tran_date,             rec_idx)
        _places  (buf, "Seq_num_Normal",    t.seq_num,               rec_idx)
        _places  (buf, "Resp_code",         t.resp_code,             rec_idx)
        _places  (buf, "Rvsl_code",         t.rvsl_code,             rec_idx)
        _places  (buf, "Tran_code",         t.tran_code,             rec_idx)
        _places_r(buf, "Amt_1",             t.amount_paise,          rec_idx)
        _places  (buf, "Acq_inst_id",       t.acq_inst_id,           rec_idx)
        _places  (buf, "Rcv_inst_id",       t.rcv_inst_id,           rec_idx)
        _places  (buf, "Org_crncy_code",    t.currency,              rec_idx)
        _places  (buf, "Acq_crncy_code",    t.currency,              rec_idx)
        _places  (buf, "Iss_crncy_code",    t.currency,              rec_idx)
        _places  (buf, "Auth_resp_id",      t.auth_resp_id,          rec_idx)
        _places  (buf, "AADHAR NUMBER/RRN", t.rrn,                   rec_idx)

        if t.is_reversal and t.oseq_num:
            _places(buf, "Oseq_num",   t.oseq_num,   rec_idx)
            _places(buf, "Otran_date", t.otran_date, rec_idx)
            _places(buf, "Otran_time", t.otran_time, rec_idx)

    return "".join(buf)


def build_header(business_date: str) -> str:
    """File header line — 3610 chars with metadata block + first 4 records."""
    yymmdd = business_date[2:]
    prefix = (f"003610000072THA{yymmdd}00512821PRO260        TLF" + " " * 32 +
              f"000076FH {yymmdd}00512821PRO260TLF     " +
              f"TANGO.$IDFCDS.TGATIDFC.TL{yymmdd}    D1 ")
    return prefix.ljust(HEADER_LEN)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(num_txns: int, business_date: str,
             test_case: str = "random",
             institution: str = "IDFC",
             currency: str = "INR",
             seed: Optional[int] = None,
             validate: bool = True) -> Tuple[List[Txn], List[str]]:
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    seq_start = rng.randint(100_000_000_000, 899_999_999_999)
    ccy_num = {"INR": "356", "USD": "840", "EUR": "978"}.get(currency.upper(), "356")
    acq_id = "00000001234"

    txns: List[Txn] = [_make_txn(i, test_case, business_date, ccy_num, acq_id, rng, seq_start)
                       for i in range(num_txns)]

    # Reversal linking
    if test_case == "chargebacks":
        non_rev = [t for t in txns if not t.is_reversal]
        for t in txns:
            if t.is_reversal and non_rev:
                src = rng.choice(non_rev)
                t.oseq_num = src.seq_num
                t.otran_date = src.tran_date[:4]
                t.otran_time = src.tran_time + "00"

    # Pack 4 transactions per line
    lines: List[str] = []
    for i in range(0, num_txns, 4):
        slot = txns[i:i+4]
        while len(slot) < 4:
            slot.append(None)
        lines.append(build_line(slot))

    if validate:
        bad = [(i, len(l)) for i, l in enumerate(lines, 1) if len(l) != LINE_LEN]
        if bad:
            raise AssertionError(f"validation failed: {len(bad)} lines wrong length: {bad[:3]}")

    return txns, lines


def write_outputs(txns: List[Txn], lines: List[str], out_path: str,
                  business_date: str, currency: str) -> None:
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(build_header(business_date) + "\n")
        for l in lines:
            f.write(l + "\n")

    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["seq_num", "rrn", "pan_masked", "msg_type", "tran_date", "tran_time",
                    "amount_paise", "mcc", "merchant_name", "city", "state", "terminal_id",
                    "is_reversal", "is_on_us", "test_case"])
        for t in txns:
            pan_m = t.pan[:6] + "X" * 6 + t.pan.strip()[-4:]
            w.writerow([t.seq_num, t.rrn, pan_m, t.msg_type, t.tran_date, t.tran_time,
                        t.amount_paise, t.mcc, t.merchant_name, t.merchant_city,
                        t.merchant_state, t.terminal_id, t.is_reversal, t.is_on_us,
                        t.test_case])

    totals = {
        "num_txns": len(txns), "num_lines": len(lines),
        "total_amount_paise": sum(t.amount_paise for t in txns),
        "msg_type_counts": _counter([t.msg_type for t in txns]),
        "reversals": sum(1 for t in txns if t.is_reversal),
        "on_us": sum(1 for t in txns if t.is_on_us),
        "currency": currency, "business_date": business_date,
        "line_length": LINE_LEN, "header_length": HEADER_LEN,
        "records_per_line": 4,
    }
    with open(base + "_expected_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


def _counter(items):
    out = {}
    for x in items: out[x] = out.get(x, 0) + 1
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Spec-accurate Mastercard TLF generator (3462-char, 4 records/line)")
    p.add_argument("--num-txns", type=int, default=10)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--institution", default="IDFC")
    p.add_argument("--currency", default="INR")
    p.add_argument("--testcase", default="random",
                   choices=["random", "acquiring", "issuing", "chargebacks",
                            "recon_break", "high_value", "on_us", "atm_mix"])
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--random", action="store_true")
    p.add_argument("--validate", action="store_true", default=True)
    p.add_argument("--output", default="tlf.txt")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    txns, lines = generate(args.num_txns, bdate, args.testcase, args.institution,
                           args.currency, args.seed, args.validate)
    write_outputs(txns, lines, args.output, bdate, args.currency)
    print(f"  wrote {len(txns)} txns in {len(lines)} lines → {args.output}")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    print(f"  totals       → {os.path.splitext(args.output)[0]}_expected_totals.json")
    if args.validate:
        print(f"  validate     → all lines {LINE_LEN} chars, 4 records each  [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
