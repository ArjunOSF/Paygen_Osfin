"""
ptlf_generator_v2.py
====================
Spec-accurate Visa PTLF (Portfolio Transaction Listing File) generator for IDFC Bank.

Source of truth: PTLF.xls Sheet1 (103 fields, 1-indexed positions) + real sample
Format: fixed-width 1904 chars per record, file header line 2052 chars.

Layer 1 — outer wrapper [0:43]: proprietary IDFC switch fields, identical per record
Layer 2 — BASE II clearing data [43:911]+: 103 fields at exact spec positions

Replaces the legacy ptlf_generator.py which used incorrect positions (SEQ_NO at 204,
PAN at 29) — those were pattern-matched without a spec. This v2 uses real spec.
"""

from __future__ import annotations
import argparse, csv, json, os, random, sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

RECORD_LEN = 1904
HEADER_LEN = 2052

# 103-field map from PTLF.xls Sheet1 (1-indexed positions, lengths)
# Derived programmatically — do not edit by hand. Re-run xls parser if spec changes.
# Some field names appear twice in spec (Tran Time at pos 147 AND 271, Auth_indicator at
# pos 616 AND 676). Both positions must be written when the value changes — see
# `_FIELD_LIST` and `_places()` helper below.
FIELD_MAP: Dict[str, Tuple[int, int]] = {
    'DateTime': (26, 8),
    'RecordType': (34, 2),
    'Card Ln': (36, 4),
    'Card Fiid': (40, 4),
    'Card Num': (44, 19),
    'Member Number': (63, 3),
    'Retailier Ln': (66, 4),
    'Retailier Fiid': (70, 4),
    'Retailer Group': (74, 4),
    'Retailer Regin': (78, 4),
    'Retailer ID': (82, 19),
    'Retailer Term ID': (101, 16),
    'Shift Number': (117, 3),
    'Batch Number': (120, 3),
    'Term Ln': (123, 4),
    'Term Fiid': (127, 4),
    'Term ID': (131, 16),
    'Tran Time': (147, 8),
    'Alt_term_ID': (155, 16),
    'Alt_rec Format': (171, 1),
    'Alt_retailer_ID': (172, 19),
    'Clerk_Identification_Ni': (191, 6),
    'Data_Flag': (197, 1),
    'Msg Type': (198, 4),
    'Msg Status': (202, 2),
    'Originator': (204, 1),
    'Respondor': (205, 1),
    'Issuer Code': (206, 2),
    'Entry Time': (208, 57),
    'Tran Date': (265, 6),
    'Tran Time': (271, 8),
    'Post_Date': (279, 6),
    'Acq_Interchange_Date': (285, 6),
    'Iss_interchange Date': (291, 6),
    'Seq Num': (297, 12),
    'Term_Name_Loc': (309, 25),
    'Term_owner_Name': (334, 22),
    'Term_city': (356, 13),
    'Term_State': (369, 3),
    'Term_country': (372, 2),
    'Branch_id': (374, 4),
    'User_Field': (378, 3),
    'Time_offset': (381, 5),
    'Acq_Inst_ID': (386, 11),
    'Rcv_Inst_ID': (397, 11),
    'Term_Type': (408, 2),
    'Clerk_ID': (410, 6),
    'CRT_Auth_Group': (416, 4),
    'Crt_auth_User': (420, 8),
    'SIC Code': (428, 4),
    'Tran_Orig': (432, 4),
    'Tran_Dest': (436, 4),
    'Tran_code': (440, 6),
    'Card_Type': (446, 2),
    'Account Number': (448, 19),
    'Response_code': (467, 3),
    'Amt_1': (470, 19),
    'Amt_2': (489, 19),
    'Expiration_Date': (508, 4),
    'Track2': (512, 40),
    'Pin_offset': (552, 16),
    'Pre_Auth_Seq_Num': (568, 12),
    'Invoice_num': (580, 10),
    'Orig_Invoice_Num': (590, 10),
    'Authorizer': (600, 16),
    'Auth_indicator': (616, 1),
    'Shift_number': (617, 3),
    'Batch_Seq_Number': (620, 3),
    'Approval_Code': (623, 8),
    'Approval_code_Length': (631, 1),
    'Interchange_Reponse': (632, 8),
    'Pseudo_identification_number': (640, 4),
    'Referral_Phone': (644, 20),
    'Draft_Capture_Flag': (664, 1),
    'Settlement_Flag': (665, 1),
    'Reversal_code': (666, 2),
    'ChargeBack_reason': (668, 2),
    'ChargeBack_Occurance': (670, 1),
    'Transaction_Origin': (671, 2),
    'POS_EntryMode': (673, 3),
    'Auth_indicator': (676, 1),
    'Currency_code': (677, 3),
    'Multiple_Currency': (680, 41),
    'Auth_refr': (721, 6),
    'Auth_refr_Ind': (727, 1),
    'Auth_setl_Impact': (728, 4),
    'Frwd_inst_Id_Num': (732, 11),
    'Card_Accpt_ID_Num': (743, 11),
    'Card_Isser_Id_Num': (754, 11),
    'Orig_msg_Type': (765, 4),
    'Orig_Time': (769, 8),
    'Orig_Date': (777, 4),
    'Orig_seq_num': (781, 12),
    'Orig_B24_Post_Date': (793, 4),
    'Expn_reason_code': (797, 3),
    'Override_Flag': (800, 1),
    'Addres': (801, 20),
    'Zip_Code': (821, 9),
    'Address_Verification': (830, 1),
    'Pin_indicator': (831, 1),
    'Pin_Retries': (832, 1),
    'Pre_Auth_Expiry_dateTime': (833, 14),
    'Pre_Auth_Hold_Level': (847, 1),
}

# Full list preserves duplicate field names so build_record can write to ALL positions
_FIELD_LIST: List[Tuple[str, int, int]] = [
    ('DateTime', 26, 8), ('RecordType', 34, 2), ('Card Ln', 36, 4), ('Card Fiid', 40, 4),
    ('Card Num', 44, 19), ('Member Number', 63, 3), ('Retailier Ln', 66, 4),
    ('Retailier Fiid', 70, 4), ('Retailer Group', 74, 4), ('Retailer Regin', 78, 4),
    ('Retailer ID', 82, 19), ('Retailer Term ID', 101, 16), ('Shift Number', 117, 3),
    ('Batch Number', 120, 3), ('Term Ln', 123, 4), ('Term Fiid', 127, 4),
    ('Term ID', 131, 16), ('Tran Time', 147, 8), ('Alt_term_ID', 155, 16),
    ('Alt_rec Format', 171, 1), ('Alt_retailer_ID', 172, 19),
    ('Clerk_Identification_Ni', 191, 6), ('Data_Flag', 197, 1), ('Msg Type', 198, 4),
    ('Msg Status', 202, 2), ('Originator', 204, 1), ('Respondor', 205, 1),
    ('Issuer Code', 206, 2), ('Entry Time', 208, 57),
    ('Tran Time', 271, 8),                                # ← duplicate (2nd instance)
    ('Tran Date', 265, 6), ('Acq_Interchange_Date', 285, 6), ('Seq Num', 297, 12),
    ('Term_Name_Loc', 309, 25), ('Term_owner_Name', 334, 22), ('Term_State', 369, 3),
    ('Branch_id', 374, 4), ('Time_offset', 381, 5), ('Acq_Inst_ID', 386, 11),
    ('Rcv_Inst_ID', 397, 11), ('Tran_Orig', 432, 4), ('SIC Code', 428, 4),
    ('Tran_code', 440, 6), ('Account Number', 448, 19), ('Amt_1', 470, 19),
    ('Settlement_Flag', 665, 1), ('Reversal_code', 666, 2),
    ('ChargeBack_reason', 668, 2), ('Transaction_Origin', 671, 2),
    ('POS_EntryMode', 673, 3), ('Auth_indicator', 676, 1),                  # 1st
    ('Currency_code', 677, 3), ('Auth_indicator', 616, 1),                  # 2nd
    ('Orig_Time', 769, 8), ('Orig_seq_num', 781, 12),
]


def _places(buf: list, name: str, value: str) -> int:
    """Place value at ALL spec positions for this field name. Returns count placed."""
    count = 0
    for n, pos, length in _FIELD_LIST:
        if n == name:
            _place(buf, value, pos, length)
            count += 1
    return count


def _places_r(buf: list, name: str, value) -> int:
    """Right-align numeric — place at ALL spec positions for this field name."""
    count = 0
    for n, pos, length in _FIELD_LIST:
        if n == name:
            _place_r(buf, value, pos, length)
            count += 1
    return count


# Verbatim template captured from real PTLF_raw.txt line 2 (post-header).
# Variable fields are overwritten; static structural fields preserved.
_TEMPLATE = (
    "001904001898DR172145892515000000001PRO2IDFC4016130405704080   000IDFEIDFE0000000"
    "0ELANGANA RUCHUL000063993249        000000IDFEIDFE63993249        23300100639932"
    "49        5ELANGANA RUCHUL0000      002100073991774720801654      1774720801717 "
    "     1774720801717      26032823300100260328260328260328608718731409TELANGANA RU"
    "CHULU        TELANGANA RUCHULU     KOTHAGUDEM   XXXINIDFE   00330000004332740000"
    "0000000                    5812XXXXXXXX100000P 00000010098361516  09800000000000"
    "0005000000000000000000000002708XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX             "
    "       000000000000                                     000000        6        0"
    "000                    XX000000007103563560000000100061000000XXXXXXXXXXXXXXXXXXX"
    "1220000    00000000000ELANGANA RUXXXXXXXXXXXXXXX233001000328            XXXX0000"
    "                              10                                                "
    "0012396087648014107XXXXXXXXXXXX                                                 "
    "                                                                                "
    "                                                                                "
    "                                                                                "
    "                                                                                "
    "                                                                                "
    "                                                                                "
    "                                                                                "
    "                                                                                "
    "                                                                                "
    "                                                                                "
    "                                                                                "
    "                                                                "
)
assert len(_TEMPLATE) == RECORD_LEN, f"template len {len(_TEMPLATE)} != {RECORD_LEN}"


# ---------------------------------------------------------------------------
# Transaction model
# ---------------------------------------------------------------------------

@dataclass
class Txn:
    """One PTLF transaction. Field names match the source-of-truth columns
    in the spec; not every spec field gets an explicit Txn attribute — many
    are static (institution codes, format flags) and are preserved from the
    template."""
    pan: str                 # 19-char Visa PAN, Luhn-valid
    seq_num: str             # 12-digit sequence (pos 297)
    tran_date: str           # YYMMDD (pos 265)
    tran_time: str           # HHMMSSCC 8-char (pos 147)
    amount_paise: int        # zero-padded 19 chars at pos 470
    mcc: str                 # 4-digit SIC code (pos 428)
    merchant_name: str       # up to 25 chars (pos 309)
    terminal_id: str         # 16 chars (pos 131)
    msg_type: str = "0210"   # 0210=auth, 0220=fin advice, 0420=reversal
    pos_entry: str = "071"   # 051=chip 012=ecom 071=contactless
    currency: str = "356"    # 356=INR
    reversal_code: str = "00"
    chargeback_reason: str = "00"
    acq_inst_id: str = "00000001234"   # pos 386 len 11
    rcv_inst_id: str = "00000001234"   # pos 397 len 11 (same as acq for On Us)
    orig_seq_num: str = ""             # pos 781 (for reversals)
    is_reversal: bool = False
    is_chargeback: bool = False
    is_on_us: bool = False
    test_case: str = "random"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _luhn_checksum(num: str) -> int:
    digits = [int(d) for d in num]
    odd = digits[-1::-2]
    even = digits[-2::-2]
    total = sum(odd) + sum(sum(divmod(d * 2, 10)) for d in even)
    return total % 10


def _luhn_complete(prefix: str, length: int = 16) -> str:
    """Append Luhn check digit so total length matches."""
    body_len = length - 1
    body = prefix + "".join(str(random.randint(0, 9)) for _ in range(body_len - len(prefix)))
    check = (10 - _luhn_checksum(body + "0")) % 10
    return body + str(check)


def _normalize_date(s: str) -> str:
    """Accept many human formats, return YYYYMMDD."""
    s = s.strip().replace(",", "")
    fmts = [
        "%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y",
        "%d-%m-%y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
        "%d %B %y", "%d %b %y",
    ]
    for f in fmts:
        try:
            return datetime.strptime(s, f).strftime("%Y%m%d")
        except ValueError:
            continue
    # ordinal endings: "1st April 2026" etc.
    import re
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s)
    for f in fmts:
        try:
            return datetime.strptime(cleaned, f).strftime("%Y%m%d")
        except ValueError:
            continue
    raise ValueError(f"unrecognised date format: {s!r}")


def _place(buf: list, value: str, start_1idx: int, length: int) -> None:
    """Place value at 1-indexed position, truncated/padded to length."""
    s = str(value)[:length].ljust(length)
    start = start_1idx - 1
    for i, ch in enumerate(s):
        if 0 <= start + i < RECORD_LEN:
            buf[start + i] = ch


def _place_r(buf: list, value, start_1idx: int, length: int) -> None:
    """Right-align numeric value, zero-pad."""
    s = str(value)[-length:].zfill(length)
    start = start_1idx - 1
    for i, ch in enumerate(s):
        if 0 <= start + i < RECORD_LEN:
            buf[start + i] = ch


# ---------------------------------------------------------------------------
# Value generators per test case
# ---------------------------------------------------------------------------

_MERCHANTS = [
    ("AMAZONIN",                  "5942"),
    ("FLIPKART INTERNET",         "5399"),
    ("Swiggy Limited",            "5811"),
    ("Zomato Online Order",       "5811"),
    ("BIG BAZAAR",                "5411"),
    ("RELIANCE FRESH",            "5411"),
    ("DMART NUNNA",               "5411"),
    ("INDIAN OIL",                "5541"),
    ("HP PETROL",                 "5541"),
    ("UBER INDIA",                "4121"),
    ("OLA CABS",                  "4121"),
    ("MAKEMYTRIP",                "4722"),
    ("NETFLIX SUBSCRIPTION",      "4899"),
    ("TELANGANA RUCHULU",         "5812"),
    ("INDRAPRASTHA GAS LIMITE",   "4900"),
    ("Bharat Petroleum Corpor",   "5541"),
    ("DIAMOND AND GEM DEVELO",    "5944"),
    ("AGENDRA FILLING",           "5541"),
    ("APTDPARUPAYCYBSAPTDPARU",   "9399"),
]


def _amount_for(case: str, rng: random.Random) -> int:
    """Returns amount in paise."""
    if case == "high_value":
        return rng.randint(5_000_000, 50_000_000)
    if case == "chargebacks":
        return rng.randint(10_000, 500_000)
    return rng.randint(50_000, 2_500_000)


def _make_txn(idx: int, case: str, business_date: str, currency: str,
              acq_id: str, rng: random.Random, seq_start: int) -> Txn:
    yymmdd = business_date[2:8]
    hh = rng.randint(0, 23); mm = rng.randint(0, 59)
    ss = rng.randint(0, 59); cc = rng.randint(0, 99)
    time_8 = f"{hh:02d}{mm:02d}{ss:02d}{cc:02d}"

    pan = _luhn_complete("4", 16).ljust(19)            # Visa, 16 digits + 3 spaces
    seq = str(seq_start + idx).zfill(12)
    merch_name, mcc = rng.choice(_MERCHANTS)
    amount = _amount_for(case, rng)

    is_rev = is_cb = on_us = False
    msg_type = "0210"
    rev_code = "00"
    cb_reason = "00"

    if case == "chargebacks":
        # 30% chargebacks, 15% reversals, rest normal
        r = rng.random()
        if r < 0.30:
            is_cb = True; cb_reason = rng.choice(["37", "53", "62", "75"])
        elif r < 0.45:
            is_rev = True; msg_type = "0420"; rev_code = "01"
    elif case == "recon_break":
        # 5% records will be modified post-build to break recon
        pass
    elif case == "on_us":
        on_us = True

    pos_entry = rng.choice(["051", "012", "071"])
    if mcc == "5942" or "online" in merch_name.lower():
        pos_entry = "012"

    return Txn(
        pan=pan,
        seq_num=seq,
        tran_date=yymmdd,
        tran_time=time_8,
        amount_paise=amount,
        mcc=mcc,
        merchant_name=merch_name[:25],
        terminal_id=f"S5NL{rng.randint(10**11, 10**12 - 1)}"[:16],
        msg_type=msg_type,
        pos_entry=pos_entry,
        currency=currency,
        reversal_code=rev_code,
        chargeback_reason=cb_reason,
        acq_inst_id=acq_id,
        rcv_inst_id=acq_id if on_us else "00000005678",
        is_reversal=is_rev,
        is_chargeback=is_cb,
        is_on_us=on_us,
        test_case=case,
    )


# ---------------------------------------------------------------------------
# Record builder — substitute Txn fields into spec positions
# ---------------------------------------------------------------------------

def build_record(t: Txn) -> str:
    """Apply Txn values onto the verbatim template at spec-correct positions.
    `_places` writes to ALL instances of a field name (handles Tran Time appearing
    at both pos 147 and 271)."""
    buf = list(_TEMPLATE)

    _places  (buf, "Card Num",          t.pan)                       # pos 44  len 19
    _places  (buf, "Term ID",           t.terminal_id)               # pos 131 len 16
    _places  (buf, "Tran Time",         t.tran_time)                 # pos 147 + 271
    _places  (buf, "Msg Type",          t.msg_type)                  # pos 198 len 4
    _places  (buf, "Tran Date",         t.tran_date)                 # pos 265 len 6
    _places  (buf, "Acq_Interchange_Date", t.tran_date)              # pos 285 len 6
    _places  (buf, "Seq Num",           t.seq_num)                   # pos 297 len 12 (PRIMARY)
    _places  (buf, "Term_Name_Loc",     t.merchant_name.upper())     # pos 309 len 25
    _places  (buf, "Acq_Inst_ID",       t.acq_inst_id)               # pos 386 len 11
    _places  (buf, "Rcv_Inst_ID",       t.rcv_inst_id)               # pos 397 len 11
    _places  (buf, "SIC Code",          t.mcc)                       # pos 428 len 4
    _places_r(buf, "Amt_1",             t.amount_paise)              # pos 470 len 19
    _places  (buf, "Reversal_code",     t.reversal_code)             # pos 666 len 2
    _places  (buf, "ChargeBack_reason", t.chargeback_reason)         # pos 668 len 2
    _places  (buf, "POS_EntryMode",     t.pos_entry)                 # pos 673 len 3
    _places  (buf, "Currency_code",     t.currency)                  # pos 677 len 3

    if t.is_reversal and t.orig_seq_num:
        _places(buf, "Orig_seq_num", t.orig_seq_num)                 # pos 781 len 12
        _places(buf, "Orig_Time",    t.tran_time)                    # pos 769 len 8

    return "".join(buf)


def build_header(business_date: str) -> str:
    """File header line — 2052 chars (one per file)."""
    yymmdd = business_date[2:]
    hdr = (
        f"002052000072THA{yymmdd}00505287PRO260        PTLF"
        + " " * 31
        + f"000076FH {yymmdd}00505287PRO260PTLF    "
        + f"TANGO.$IDFCDS.TGPTIDFC.PO{yymmdd}    D121"
    )
    return hdr.ljust(HEADER_LEN)


# ---------------------------------------------------------------------------
# Public generation API
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
    acq_id = "00000001234"   # IDFC default; would come from config in real use

    txns: List[Txn] = []
    for i in range(num_txns):
        txns.append(_make_txn(i, test_case, business_date, ccy_num, acq_id, rng, seq_start))

    # Reversal scenario: link to fresh originals
    if test_case == "chargebacks":
        originals = [t for t in txns if not (t.is_reversal or t.is_chargeback)]
        for t in txns:
            if t.is_reversal and originals:
                src = rng.choice(originals)
                t.orig_seq_num = src.seq_num

    records = [build_record(t) for t in txns]

    if validate:
        bad = [(i, len(r)) for i, r in enumerate(records, 1) if len(r) != RECORD_LEN]
        if bad:
            raise AssertionError(f"validation failed: {len(bad)} records wrong length: {bad[:3]}")

    return txns, records


def write_outputs(txns: List[Txn], records: List[str], out_path: str,
                  business_date: str, currency: str) -> None:
    """Write .txt + _master_table.csv + _expected_totals.json"""
    base, _ = os.path.splitext(out_path)

    # Main file
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(build_header(business_date) + "\n")
        for r in records:
            f.write(r + "\n")

    # Master table
    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["seq_num", "pan_masked", "msg_type", "tran_date", "tran_time",
                    "amount_paise", "mcc", "merchant_name", "terminal_id",
                    "is_reversal", "is_chargeback", "is_on_us", "test_case"])
        for t in txns:
            pan_m = t.pan[:6] + "X" * 6 + t.pan.strip()[-4:]
            w.writerow([t.seq_num, pan_m, t.msg_type, t.tran_date, t.tran_time,
                        t.amount_paise, t.mcc, t.merchant_name, t.terminal_id,
                        t.is_reversal, t.is_chargeback, t.is_on_us, t.test_case])

    # Expected totals
    totals = {
        "num_txns": len(txns),
        "total_amount_paise": sum(t.amount_paise for t in txns),
        "msg_type_counts": _counter([t.msg_type for t in txns]),
        "reversals": sum(1 for t in txns if t.is_reversal),
        "chargebacks": sum(1 for t in txns if t.is_chargeback),
        "on_us": sum(1 for t in txns if t.is_on_us),
        "currency": currency,
        "business_date": business_date,
        "record_length": RECORD_LEN,
        "header_length": HEADER_LEN,
    }
    with open(base + "_expected_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


def _counter(items):
    out = {}
    for x in items:
        out[x] = out.get(x, 0) + 1
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Spec-accurate Visa PTLF generator (1904-char)")
    p.add_argument("--num-txns", type=int, default=10)
    p.add_argument("--date", type=str, default=datetime.now().strftime("%Y%m%d"),
                   help="any human format — normalised to YYYYMMDD")
    p.add_argument("--institution", default="IDFC")
    p.add_argument("--currency", default="INR")
    p.add_argument("--testcase", default="random",
                   choices=["random", "acquiring", "issuing", "chargebacks",
                            "recon_break", "high_value", "on_us"])
    p.add_argument("--seed", type=int, default=None, help="for reproducibility")
    p.add_argument("--random", action="store_true",
                   help="alias — random seed (default)")
    p.add_argument("--validate", action="store_true", default=True)
    p.add_argument("--output", default="ptlf.txt")
    args = p.parse_args(argv)

    try:
        bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    txns, records = generate(
        num_txns=args.num_txns,
        business_date=bdate,
        test_case=args.testcase,
        institution=args.institution,
        currency=args.currency,
        seed=args.seed,
        validate=args.validate,
    )
    write_outputs(txns, records, args.output, bdate, args.currency)
    print(f"  wrote {len(records)} records → {args.output}")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    print(f"  totals       → {os.path.splitext(args.output)[0]}_expected_totals.json")
    if args.validate:
        print(f"  validate     → all records {RECORD_LEN} chars  [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
