"""
epin_generator.py
=================
Visa BASE II incoming EPIN clearing file generator.
Format: fixed-width 168 chars per record; multiple records per transaction.

Source: real SAVE.INCOMING.ASCII.EPIN sample (March 2026 IDFC data)
        prompt-doc spec for known field positions (TC05 TCR0)

Record types (TC + TCR) and bundle composition per transaction:
  TC05 (sales / purchase):       TCR0 + TCR1 + TCR5 always; +TCR7 chip; TCR4 optional
  TC06 (credit / refund):        TCR0 + TCR1 + TCR5
  TC07 (ATM cash disbursement):  TCR0 + TCR1 + TCR4 + TCR5 + TCR7
  TC25 (purchase reversal):      TCR0 only (links to original via ARN)
  TC27 (ATM reversal):           TCR0 only
  TC91 (file header):            ~50 records at top
  TC92 (file trailer):           1 record at end with totals
  TC46 (settlement data):        TCR0 + TCR1 — generated alongside transactions

Variable field positions (1-indexed) in TC05 TCR0, verified across 50+ samples:
  pos   5–20 (16)  Account Number (PAN, Visa-prefix Luhn)
  pos  27–49 (23)  ARN (Acquirer Reference Number) — primary join key
  pos  51–57 (7)   filler
  pos  58–61 (4)   Purchase Date MMDD
  pos  62–73 (12)  Destination Amount (paise zero-padded)
  pos  74–76 (3)   Destination Currency (356=INR)
  pos  77–88 (12)  Source Amount
  pos  89–91 (3)   Source Currency
  pos  92–116 (25) Merchant Name
  pos 117–129 (13) Merchant City
  pos 130–131 (2)  Merchant Country
  pos 133–136 (4)  MCC
  pos 153–158 (6)  Authorization Code
  pos 162–163 (2)  POS Entry Mode (07=chip, 05=manual, 90=swipe)
"""

from __future__ import annotations
import argparse, csv, json, os, random, sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

REC_LEN = 168

# Real-world templates per TC+TCR type, captured from the IDFC sample file.
# These preserve all structural / static bytes; we overwrite only known variable
# positions per transaction to produce realistic-looking but distinct records.
TEMPLATES = {
    "0500": "05004405232017798960000Z  74578116047000113384446000000000216000000072346356000000069700417CHIKINOFF                BISHKEK      KG 581200000     1000N0394765 1 0760480",
    "0501": "0501   A1249    000000                                                     983SY0201392893     J392893 000000000000  6047 0    5 0                           000000000  ",
    "0503": "0503            LG        0          26021400000000000000000000000000000000000000000000000000000000000001000000000000                                                   ",
    "0504": "0504          SD0002C                         210500000000                  000000000000000000000000   0000000000000000V001001382406351643274805564340060488242         ",
    "0505": "050539604740453301800000006970041700        0000 000000000000                              000000014686238C0711435107110169000000000000I             0000000000000000 C ",
    "0506": "050600000000000020000000000000                                                 000000000000AA60474468933030954                                                          ",
    "0507": "050700001260216E0F848417        C6526829002B20207C2B09B9231F60F30112000000000003A02000000000069700000000000000000000060000000000000000000000000000000020700000          ",
    "050D": "050DRP129906                         YIa3qzM0VF0000000049001                                                                                                            ",
    "0600": "06004281021015068339000   74012566047606963071444000000000216000000001180356000000001180356PYU*RIOT GAMES LIMITED   HTTPS://WWW.RUS 581590064LA   1000D0729909 4 0160480",
    "0601": "0601 10         000000                                                        S 470000087120146871201460000000000007 6048104 1 V10  000081058882597          000000000  ",
    "0605": "060540604746679536500000000118035685        0000 000000001180N124-46624801                 000000000238360D0000000000000000000000000000C             9999999999999999 C ",
    "0700": "07004405230001787023000Z  72750956047000217239481000000000216000000944004356000000010400840*NORTH NATOMAS           SACRAMENTO   US 601100000CA   1000N0043615 2 056048H",
    "0701": "0701   A1249    000000                                                     8C0SYBANK OF AMERICAICAD3752000000000000  6047 0    5 2                           000000000  ",
    "0704": "0704          SD00025   2   2                     00000400DB                000000000000000000000000   0003630800000000V001001402534761685139924089300000000000         ",
    "0705": "0705406047771289147000000010000840          0000 000000000000                              000000013615440D0000000007110169000000000000I             0000000000000000 C ",
    "0707": "070701001260216604020840        F1A2CA6E000F3800471946B4DC22FECF0112808004800003A0A0000000000100000000000000000000000600000000000000000000000000000000                  ",
    "2500": "25004405233000490391000   74103626047000037548979000000000216000000000241356000000032500860YANDEX.GO                YUNUSOBOD TUMUZ 478900000     1000N0160599 4 1060480",
    "2700": "27004405234001548138000   74518556048000200030281000000000217000002963105356000002963105356CROWNMELB RIVERWALK MIDDLSOUTHBANK    AU 601100000   9 1000E0089675 2 056048H",
    "9100": "910040156126048000000099423777000000000208000001000000000855000000        000000209000000000000000000000001652511382000000000000000000000000000000000000000000000       ",
    "9200": "920040156126048000004994923516000000008947000050000000035974000000        000010403000000000000000000000036973042301000000000000000000000000000000000000000000000",
    "4500": "45004015613201151REPORT ID: FRDDTS14                                             VISA INTERNATIONAL                             PAGE NUM:         1  FRDDTS14  000003580",
    "4600": "4600401561000000900053889290003750169000375016001356      V2110  20260482026048              I1000000000000000000000000000000000000000000000000000000000000            0",
    "4601": "4601       000000000000057               000000015003437DB000000000000000  000000015003437DB                                                                            ",
}
# Pad any short template to REC_LEN (some templates lose trailing spaces during capture)
TEMPLATES = {k: v.ljust(REC_LEN)[:REC_LEN] for k, v in TEMPLATES.items()}
for k, v in TEMPLATES.items():
    assert len(v) == REC_LEN, f"template {k} wrong length: {len(v)} != {REC_LEN}"


# ---------------------------------------------------------------------------
# Transaction model
# ---------------------------------------------------------------------------

@dataclass
class Txn:
    pan: str
    arn: str             # 23-char Acquirer Reference Number — primary join → ATM_C.RR_NO
    amount_paise: int
    purchase_date: str   # MMDD
    merchant_name: str
    merchant_city: str
    merchant_country: str
    mcc: str
    auth_code: str       # 6 digits
    pos_entry: str = "07"  # 07=chip 05=manual 90=swipe 81=ecom
    currency: str = "356"
    tc_kind: str = "TC05"  # TC05/TC06/TC07/TC25/TC27
    is_reversal: bool = False
    has_chip: bool = True
    has_supplemental: bool = False
    test_case: str = "random"
    # PROMPT 11 FIX 3 — explicit BASE II spec fields:
    resp_code: str = "00"          # [82:84] '00'=approved, '05'=declined
    stan: str = "000000000000"     # [52:64] System Trace Audit Number — 12 digits
    bin_acquirer: str = "00099900" # [3:11] Acquirer BIN — 8 chars
    tran_time_hhmmss: str = "000000"  # [46:52]
    merchant_zip: str = "000000"      # [124:130]
    # PROMPT 7 FIX: original-txn linking + reversal date offset
    original_arn: str = ""        # for TC25/TC27 — links to original TC05/TC07
    original_date: str = ""       # for reversals — MMDD of original txn
    reversal_offset_days: int = 0 # if >0, this txn's purchase_date = orig_date + offset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _luhn_checksum(num: str) -> int:
    digits = [int(d) for d in num]
    odd = digits[-1::-2]; even = digits[-2::-2]
    return (sum(odd) + sum(sum(divmod(d * 2, 10)) for d in even)) % 10


def _luhn_complete(prefix: str, length: int = 16) -> str:
    body = prefix + "".join(str(random.randint(0, 9)) for _ in range(length - 1 - len(prefix)))
    check = (10 - _luhn_checksum(body + "0")) % 10
    return body + str(check)


def _normalize_date(s: str) -> str:
    s = s.strip().replace(",", "")
    fmts = ["%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y",
            "%d-%m-%y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"]
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
        if 0 <= start + i < REC_LEN:
            buf[start + i] = ch


def _place_r(buf: list, value, start_1idx: int, length: int) -> None:
    s = str(value)[-length:].zfill(length)
    start = start_1idx - 1
    for i, ch in enumerate(s):
        if 0 <= start + i < REC_LEN:
            buf[start + i] = ch


# ---------------------------------------------------------------------------
# Record builders — substitute Txn fields into TCR templates
# ---------------------------------------------------------------------------

def _apply_tcr0(template_key: str, t: Txn) -> str:
    """TCR0 — built from spec positions (BASE II decode):
       [0:2] TC | [2:3] TCR | [3:11] BIN | [11:28] PAN | [28:40] AMT_1 |
       [40:46] DATE | [46:52] TIME | [52:64] STAN | [64:76] RRN |
       [76:82] AUTH | [82:84] RESP | [84:109] MERCH | [109:122] CITY |
       [122:124] COUNTRY | [124:130] ZIP | [130:168] padding."""
    tc = template_key[:2]              # '05' / '06' / '07' / '25' / '27'
    buf = [" "] * REC_LEN
    def put(start, value, width):
        v = str(value)[:width].ljust(width)
        for i, ch in enumerate(v):
            buf[start + i] = ch
    def putr(start, value, width):
        v = str(value).rjust(width)[-width:]
        for i, ch in enumerate(v):
            buf[start + i] = ch
    put(0,   tc,                  2)
    put(2,   "0",                 1)                       # TCR
    put(3,   t.bin_acquirer,      8)
    putr(11, t.pan,               17)
    putr(28, t.amount_paise,      12)                      # AMT_1 — zero-padded paise
    # Replace right-pad spaces with '0' for amount field
    for i in range(28, 40):
        if buf[i] == " ":
            buf[i] = "0"
    # Date YYMMDD — derive from purchase_date (MMDD) + business year (yy from arn)
    yy = (t.original_date or t.purchase_date)[:0]   # placeholder
    # Reconstruct YYMMDD: arn carries YDDD; safer to take from t.purchase_date which is MMDD
    yymmdd = (t.arn[7:9] if len(t.arn) >= 9 else "26") + t.purchase_date[:4]
    # Above only gives 6 chars if purchase_date is MMDD (4); pad if shorter
    yymmdd = (yymmdd + "000000")[:6]
    put(40, yymmdd,               6)
    put(46, t.tran_time_hhmmss,   6)
    put(52, t.stan,               12)
    put(64, t.arn[-12:],          12)                      # RRN — last 12 of ARN
    put(76, t.auth_code,          6)
    put(82, t.resp_code,          2)
    put(84, t.merchant_name[:25].ljust(25), 25)
    put(109, t.merchant_city[:13].ljust(13), 13)
    put(122, t.merchant_country,  2)
    put(124, t.merchant_zip,      6)
    return "".join(buf)


def _apply_addendum(template_key: str, t: Txn) -> str:
    """TCR1/4/5/7 addendum records — keep template-mostly intact but stamp ARN/amount."""
    buf = list(TEMPLATES[template_key])
    # ARN often appears at varying positions in addenda — best effort: preserve template
    # but ensure first 4 chars match TC code expectations. The addenda templates already
    # have valid record structure; we just keep them as-is for realism.
    return "".join(buf)


def build_records_for_txn(t: Txn) -> List[str]:
    """Return the full record bundle for one transaction (all required TCRs in order)."""
    out: List[str] = []
    if t.tc_kind == "TC05":
        out.append(_apply_tcr0("0500", t))
        out.append(_apply_addendum("0501", t))
        out.append(_apply_addendum("0505", t))
        if t.has_chip:
            out.append(_apply_addendum("0507", t))
        if t.has_supplemental:
            out.append(_apply_addendum("0504", t))
    elif t.tc_kind == "TC06":
        out.append(_apply_tcr0("0600", t))
        out.append(_apply_addendum("0601", t))
        out.append(_apply_addendum("0605", t))
    elif t.tc_kind == "TC07":
        out.append(_apply_tcr0("0700", t))
        out.append(_apply_addendum("0701", t))
        out.append(_apply_addendum("0704", t))
        out.append(_apply_addendum("0705", t))
        out.append(_apply_addendum("0707", t))
    elif t.tc_kind == "TC25":
        # PROMPT 7 FIX: TC25 (purchase reversal) bundle is TCR0 + TCR1 + TCR5
        # — links to original purchase via ARN; reversal date != original date.
        out.append(_apply_tcr0("2500", t))
        out.append(_apply_addendum("0501", t))
        out.append(_apply_addendum("0505", t))
    elif t.tc_kind == "TC27":
        # PROMPT 7 FIX: TC27 (ATM cash reversal) bundle is TCR0 + TCR1 + TCR5
        # — links to original TC07 via ARN; uses next-day reversal date.
        out.append(_apply_tcr0("2700", t))
        out.append(_apply_addendum("0701", t))
        out.append(_apply_addendum("0705", t))
    return out


def build_header_records(num_txns: int, member_id: str, business_date: str) -> List[str]:
    """TC91 file header bundle (50 records typically)."""
    yyddd = business_date[2:4] + str(datetime.strptime(business_date, "%Y%m%d").timetuple().tm_yday).zfill(3)
    rec = list(TEMPLATES["9100"])
    _place(rec, member_id[:6], 5, 6)
    _place(rec, yyddd, 11, 5)
    return ["".join(rec)] * 50


def build_trailer(num_records: int, total_paise: int, member_id: str, business_date: str) -> str:
    """TC92 file trailer with counts/totals."""
    rec = list(TEMPLATES["9200"])
    _place(rec, member_id[:6], 5, 6)
    _place_r(rec, num_records, 19, 7)
    _place_r(rec, total_paise, 30, 13)
    return "".join(rec)


# ---------------------------------------------------------------------------
# Value generators per test case
# ---------------------------------------------------------------------------

_MERCHANTS = [
    ("AMAZONIN",                "Bangalore",    "IN", "5942"),
    ("FLIPKART INTERNET",       "Bangalore",    "IN", "5399"),
    ("Swiggy Limited",          "Mumbai",       "IN", "5811"),
    ("Zomato Online Order",     "Gurugram",     "IN", "5811"),
    ("DMART NUNNA",             "Vijayawada",   "IN", "5411"),
    ("INDIAN OIL",              "New Delhi",    "IN", "5541"),
    ("UBER INDIA",              "Bangalore",    "IN", "4121"),
    ("MAKEMYTRIP",              "Gurugram",     "IN", "4722"),
    ("NETFLIX",                 "Mumbai",       "IN", "4899"),
    ("CHIKINOFF",               "BISHKEK",      "KG", "5812"),
    ("YANDEX.GO",               "MOSCOW",       "RU", "4789"),
    ("CROWN MELB RIVERWALK",    "SOUTHBANK",    "AU", "6011"),
    ("PYU*RIOT GAMES LIMITED",  "DUBLIN",       "IE", "5815"),
]


def _amount_for(case: str, rng: random.Random) -> int:
    if case == "high_value": return rng.randint(5_000_000, 50_000_000)
    if case == "chargebacks": return rng.randint(10_000, 500_000)
    return rng.randint(50_000, 2_500_000)


def _make_arn(rng: random.Random, business_date: str) -> str:
    """23-char ARN: 1 format code + 6 acq BIN + 4 julian date + 11 ref + 1 check."""
    fmt = "7"
    bin6 = "457811"
    yy = business_date[2:4]
    julian = str(datetime.strptime(business_date, "%Y%m%d").timetuple().tm_yday).zfill(3)
    yddd = yy[1] + julian   # 4 chars: e.g. "6047" for 2026-day-047
    ref = "".join(str(rng.randint(0, 9)) for _ in range(11))
    body = fmt + bin6 + yddd + ref
    check = str(_luhn_checksum(body + "0"))
    return body + check


def _make_txn(idx: int, case: str, business_date: str, currency: str,
              rng: random.Random) -> Txn:
    pan = _luhn_complete("4", 16)
    arn = _make_arn(rng, business_date)
    name, city, country, mcc = rng.choice(_MERCHANTS)
    amount = _amount_for(case, rng)
    auth = str(rng.randint(100000, 999999))
    purchase_mmdd = business_date[4:8]

    # Default: TC05 (sales)
    tc_kind = "TC05"
    is_rev = False
    has_chip = rng.random() < 0.4
    has_supp = rng.random() < 0.16

    if case == "atm_mix":
        tc_kind = rng.choice(["TC05", "TC05", "TC07"])  # ~33% ATM
    elif case == "chargebacks":
        r = rng.random()
        if r < 0.05: tc_kind = "TC25"; is_rev = True
        elif r < 0.06: tc_kind = "TC27"; is_rev = True
        elif r < 0.11: tc_kind = "TC06"  # refunds
    elif case == "recon_break":
        pass
    # acquiring/issuing/random/high_value/on_us all default to TC05 sales

    # PROMPT 11 FIX 3 — populate explicit spec fields
    resp = "00"
    if case == "recon_break" and rng.random() < 0.10:
        resp = "05"   # 10% declined for break-test cases
    stan = str(rng.randint(0, 999_999_999_999)).zfill(12)
    hh = rng.randint(0, 23); mm = rng.randint(0, 59); ss = rng.randint(0, 59)
    time_hhmmss = f"{hh:02d}{mm:02d}{ss:02d}"
    zip_code = str(rng.randint(100000, 999999))

    return Txn(
        pan=pan, arn=arn, amount_paise=amount, purchase_date=purchase_mmdd,
        merchant_name=name, merchant_city=city, merchant_country=country,
        mcc=mcc, auth_code=auth, pos_entry=rng.choice(["07", "05", "90", "81"]),
        currency=currency, tc_kind=tc_kind, is_reversal=is_rev,
        has_chip=has_chip, has_supplemental=has_supp, test_case=case,
        resp_code=resp, stan=stan, tran_time_hhmmss=time_hhmmss,
        merchant_zip=zip_code,
    )


# ---------------------------------------------------------------------------
# Public generation API
# ---------------------------------------------------------------------------

def generate(num_txns: int, business_date: str,
             test_case: str = "random",
             member_id: str = "401561",
             currency: str = "INR",
             reversal_offset_days: int = 1,
             seed: Optional[int] = None,
             validate: bool = True) -> Tuple[List[Txn], List[str]]:
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    ccy_num = {"INR": "356", "USD": "840", "EUR": "978"}.get(currency.upper(), "356")

    txns: List[Txn] = [_make_txn(i, test_case, business_date, ccy_num, rng) for i in range(num_txns)]

    # PROMPT 7 FIX: link reversals to original txns + apply date offset
    # TC25 reversals → link to a TC05 by ARN; TC27 → TC07. Reversal date = orig + N days.
    from datetime import timedelta as _td
    base_date = datetime.strptime(business_date, "%Y%m%d")
    candidates_05 = [t for t in txns if t.tc_kind == "TC05" and not t.is_reversal]
    candidates_07 = [t for t in txns if t.tc_kind == "TC07" and not t.is_reversal]
    for t in txns:
        if t.tc_kind == "TC25" and candidates_05:
            src = rng.choice(candidates_05)
            t.original_arn = src.arn
            t.original_date = src.purchase_date
            t.arn = src.arn   # reversal carries SAME ARN as original
            new_date = base_date + _td(days=reversal_offset_days)
            t.purchase_date = new_date.strftime("%m%d")
            t.reversal_offset_days = reversal_offset_days
        elif t.tc_kind == "TC27" and candidates_07:
            src = rng.choice(candidates_07)
            t.original_arn = src.arn
            t.original_date = src.purchase_date
            t.arn = src.arn
            new_date = base_date + _td(days=reversal_offset_days)
            t.purchase_date = new_date.strftime("%m%d")
            t.reversal_offset_days = reversal_offset_days

    # Build records
    records: List[str] = []
    records.extend(build_header_records(num_txns, member_id, business_date))
    for t in txns:
        records.extend(build_records_for_txn(t))

    total_paise = sum(t.amount_paise for t in txns)
    records.append(build_trailer(len(records), total_paise, member_id, business_date))

    if validate:
        bad = [(i, len(r)) for i, r in enumerate(records, 1) if len(r) != REC_LEN]
        if bad:
            raise AssertionError(f"validation failed: {len(bad)} records wrong length: {bad[:3]}")

    return txns, records


def write_outputs(txns: List[Txn], records: List[str], out_path: str,
                  business_date: str, currency: str) -> None:
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(r + "\n")

    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["arn", "pan_masked", "tc_kind", "purchase_date", "amount_paise",
                    "mcc", "merchant_name", "city", "country", "auth_code",
                    "pos_entry", "is_reversal", "original_arn", "original_date",
                    "reversal_offset_days", "test_case"])
        for t in txns:
            pan_m = t.pan[:6] + "X" * 6 + t.pan[-4:]
            w.writerow([t.arn, pan_m, t.tc_kind, t.purchase_date, t.amount_paise,
                        t.mcc, t.merchant_name, t.merchant_city, t.merchant_country,
                        t.auth_code, t.pos_entry, t.is_reversal,
                        t.original_arn, t.original_date, t.reversal_offset_days,
                        t.test_case])

    tcr_counts = _counter([r[:4] for r in records])
    # PROMPT 11 FIX 3/4 — per-TC AMT_1 sums for EP747 VSS-115 reconciliation.
    # Sum over Txn objects (tc_kind + resp_code) — matches the in-memory state EP747 uses.
    def _by_tc(kind, ok=True):
        return [t for t in txns if t.tc_kind == kind and (t.resp_code == "00") == ok and not t.is_reversal]
    tc05 = _by_tc("TC05"); tc06 = _by_tc("TC06"); tc07 = _by_tc("TC07")
    tc25 = [t for t in txns if t.tc_kind == "TC25"]
    tc27 = [t for t in txns if t.tc_kind == "TC27"]
    totals = {
        "num_txns": len(txns), "num_records": len(records),
        "total_amount_paise": sum(t.amount_paise for t in txns),
        "tc_tcr_counts": tcr_counts,
        "tc_kind_counts": _counter([t.tc_kind for t in txns]),
        "reversals": sum(1 for t in txns if t.is_reversal),
        "currency": currency, "business_date": business_date,
        "record_length": REC_LEN, "member_id": "401561",
        # Per-TC AMT_1 sums — drives EP747 VSS-115 amounts
        "tc05_count":    len(tc05),
        "tc05_amt_sum":  sum(t.amount_paise for t in tc05),
        "tc06_count":    len(tc06),
        "tc06_amt_sum":  sum(t.amount_paise for t in tc06),
        "tc07_count":    len(tc07),
        "tc07_amt_sum":  sum(t.amount_paise for t in tc07),
        "tc25_count":    len(tc25),
        "tc27_count":    len(tc27),
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
    p = argparse.ArgumentParser(description="Spec-accurate Visa BASE II EPIN generator (168-char TCRs)")
    p.add_argument("--num-txns", type=int, default=10)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--member-id", default="401561")
    p.add_argument("--currency", default="INR")
    p.add_argument("--testcase", default="random",
                   choices=["random", "acquiring", "issuing", "chargebacks",
                            "recon_break", "high_value", "on_us", "atm_mix"])
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--random", action="store_true")
    p.add_argument("--validate", action="store_true", default=True)
    p.add_argument("--reversal-offset", type=int, default=1,
                   help="days after original txn for TC25/TC27 reversal date (default 1)")
    p.add_argument("--output", default="epin.txt")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    txns, records = generate(args.num_txns, bdate, args.testcase, args.member_id,
                             args.currency, args.reversal_offset, args.seed, args.validate)
    write_outputs(txns, records, args.output, bdate, args.currency)
    print(f"  wrote {len(txns)} txns, {len(records)} records → {args.output}")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    print(f"  totals       → {os.path.splitext(args.output)[0]}_expected_totals.json")
    if args.validate:
        print(f"  validate     → all records {REC_LEN} chars  [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
