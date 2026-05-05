"""
recon_scenario_generator.py
============================
Generate a multi-file dataset for RECON TESTING with explicit per-file states.

Each transaction is tagged with a scenario row defining what record (or no
record) appears in each of the three files (CBS / FSS GL / Network).
The shared RRN is the join key — recon software should match records
across files using RRN, then assert the match status against the scenario.

States per file:
  Successful          — record present, normal debit/credit
  Declined            — record present, but response code = declined
  Successful&Reversed — original record + matching reversal record
  Declined&Reversed   — declined record + reversal record
  Transaction Missing — no record at all in this file
  Not In Interchange  — (GL only) GL has no entry; txn never reached interchange
  Failed              — (GL only) GL posting present with failure/error code

Default scenarios (50) cover the cross-product of Network × CBS × GL states.

Usage:
  python3 recon_scenario_generator.py --num-txns 1000 --network VISA --date 20260401
  python3 recon_scenario_generator.py --num-txns 1000 --scenarios 1,2,3,49,50
"""

from __future__ import annotations
import argparse, csv, json, os, random, re, subprocess, sys, zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 50-row scenario matrix — (scenario_id, cbs_state, gl_state, network_state)
# ---------------------------------------------------------------------------

SCENARIOS: List[Tuple[int, str, str, str]] = [
    # (scenario_id, cbs_state, gl_state, network_state)
    # Network = Successful (cases 1–12)
    ( 1, "Successful",            "Successful",            "Successful"),
    ( 2, "Successful",            "Declined",              "Successful"),
    ( 3, "Successful",            "Not In Interchange",    "Successful"),
    ( 4, "Declined",              "Successful",            "Successful"),
    ( 5, "Declined",              "Declined",              "Successful"),
    ( 6, "Declined",              "Not In Interchange",    "Successful"),
    ( 7, "Declined & Reversed",   "Successful",            "Successful"),
    ( 8, "Declined & Reversed",   "Declined",              "Successful"),
    ( 9, "Declined & Reversed",   "Not In Interchange",    "Successful"),
    (10, "Transaction Missing",   "Successful",            "Successful"),
    (11, "Transaction Missing",   "Declined",              "Successful"),
    (12, "Transaction Missing",   "Not In Interchange",    "Successful"),
    # Network = Successful & Reversed (cases 13–24)
    (13, "Successful",            "Successful",            "Successful & Reversed"),
    (14, "Successful",            "Declined",              "Successful & Reversed"),
    (15, "Successful",            "Not In Interchange",    "Successful & Reversed"),
    (16, "Declined",              "Successful",            "Successful & Reversed"),
    (17, "Declined",              "Declined",              "Successful & Reversed"),
    (18, "Declined",              "Not In Interchange",    "Successful & Reversed"),
    (19, "Successful & Reversed", "Successful",            "Successful & Reversed"),
    (20, "Successful & Reversed", "Declined",              "Successful & Reversed"),
    (21, "Successful & Reversed", "Not In Interchange",    "Successful & Reversed"),
    (22, "Transaction Missing",   "Successful",            "Successful & Reversed"),
    (23, "Transaction Missing",   "Declined",              "Successful & Reversed"),
    (24, "Transaction Missing",   "Not In Interchange",    "Successful & Reversed"),
    # Network = Transaction Missing (cases 25–36)
    (25, "Successful",            "Successful",            "Transaction Missing"),
    (26, "Successful",            "Declined",              "Transaction Missing"),
    (27, "Successful",            "Not In Interchange",    "Transaction Missing"),
    (28, "Declined",              "Successful",            "Transaction Missing"),
    (29, "Declined",              "Declined",              "Transaction Missing"),
    (30, "Declined",              "Not In Interchange",    "Transaction Missing"),
    (31, "Successful & Reversed", "Successful",            "Transaction Missing"),
    (32, "Successful & Reversed", "Declined",              "Transaction Missing"),
    (33, "Successful & Reversed", "Not In Interchange",    "Transaction Missing"),
    (34, "Transaction Missing",   "Successful",            "Transaction Missing"),
    (35, "Transaction Missing",   "Declined",              "Transaction Missing"),
    (36, "Transaction Missing",   "Not In Interchange",    "Transaction Missing"),
    # GL = Failed (cases 37–48)
    (37, "Successful",            "Failed",                "Successful"),
    (38, "Declined",              "Failed",                "Successful"),
    (39, "Successful & Reversed", "Failed",                "Successful"),
    (40, "Transaction Missing",   "Failed",                "Successful"),
    (41, "Successful",            "Failed",                "Successful & Reversed"),
    (42, "Declined",              "Failed",                "Successful & Reversed"),
    (43, "Successful & Reversed", "Failed",                "Successful & Reversed"),
    (44, "Transaction Missing",   "Failed",                "Successful & Reversed"),
    (45, "Successful",            "Failed",                "Transaction Missing"),
    (46, "Declined",              "Failed",                "Transaction Missing"),
    (47, "Successful & Reversed", "Failed",                "Transaction Missing"),
    (48, "Transaction Missing",   "Failed",                "Transaction Missing"),
    # Special cases (49–50)
    (49, "Declined & Reversed",   "Declined & Reversed",   "Successful & Reversed"),
    (50, "Declined & Reversed",   "Declined & Reversed",   "Successful"),
]


# ---------------------------------------------------------------------------
# Transaction model
# ---------------------------------------------------------------------------

@dataclass
class ReconTxn:
    txn_id: int
    scenario_id: int
    cbs_state: str
    gl_state: str
    network_state: str
    pan: str
    rrn: str
    arn: str
    amount_paise: int
    date_yyyymmdd: str
    time_hhmmss: str
    terminal_id: str
    auth_code: str
    mcc: str
    merchant_name: str
    merchant_city: str
    merchant_country: str
    network: str = "VISA"


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
    fmts = ["%Y%m%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y",
            "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"]
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s)
    for cand in (s, cleaned):
        for f in fmts:
            try: return datetime.strptime(cand, f).strftime("%Y%m%d")
            except ValueError: continue
    raise ValueError(f"unrecognised date: {s!r}")


_MERCHANTS = [
    ("AMAZONIN",                "Bangalore",    "IN", "5942"),
    ("FLIPKART INTERNET",       "Bangalore",    "IN", "5399"),
    ("SWIGGY LIMITED",          "Mumbai",       "IN", "5811"),
    ("DMART NUNNA",             "Vijayawada",   "IN", "5411"),
    ("INDIAN OIL",              "New Delhi",    "IN", "5541"),
    ("ZOMATO ONLINE ORDER",     "Gurugram",     "IN", "5811"),
    ("MAKEMYTRIP",              "Gurugram",     "IN", "4722"),
    ("UBER INDIA",              "Bangalore",    "IN", "4121"),
    ("NETFLIX",                 "Mumbai",       "IN", "4899"),
    ("BIG BAZAAR",              "Pune",         "IN", "5411"),
]


def _make_arn(rng: random.Random, business_date: str) -> str:
    fmt = "7"; bin6 = "457811"
    yy = business_date[2:4]
    julian = str(datetime.strptime(business_date, "%Y%m%d").timetuple().tm_yday).zfill(3)
    yddd = yy[1] + julian
    ref = "".join(str(rng.randint(0, 9)) for _ in range(11))
    body = fmt + bin6 + yddd + ref
    check = str(_luhn_checksum(body + "0"))
    return body + check


def _make_txn(idx: int, scenario: Tuple[int, str, str, str], business_date: str,
              network: str, rng: random.Random, rrn_start: int) -> ReconTxn:
    sid, cbs_s, gl_s, net_s = scenario
    pan_prefix = {"VISA": "4", "MC": "5", "RUPAY": "6", "NFS": "6"}.get(network.upper(), "5")
    pan = _luhn_complete(pan_prefix, 16)
    rrn = str(rrn_start + idx).zfill(12)
    arn = _make_arn(rng, business_date)
    amount = rng.randint(50_000, 2_500_000)
    name, city, country, mcc = rng.choice(_MERCHANTS)
    auth = str(rng.randint(100000, 999999))
    time_str = f"{rng.randint(0,23):02d}{rng.randint(0,59):02d}{rng.randint(0,59):02d}"
    terminal = f"S5NL{rng.randint(10**5, 10**6 - 1)}"

    return ReconTxn(
        txn_id=idx, scenario_id=sid,
        cbs_state=cbs_s, gl_state=gl_s, network_state=net_s,
        pan=pan, rrn=rrn, arn=arn, amount_paise=amount,
        date_yyyymmdd=business_date, time_hhmmss=time_str,
        terminal_id=terminal, auth_code=auth, mcc=mcc,
        merchant_name=name, merchant_city=city, merchant_country=country,
        network=network,
    )


# ---------------------------------------------------------------------------
# Per-file emitters
# ---------------------------------------------------------------------------

def _amount_17(paise: int) -> str:
    return f"{paise/100:017.2f}"


def _ddmmyyyy(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}-{yyyymmdd[4:6]}-{yyyymmdd[:4]}"


def _ddmmyyyy_compact(yyyymmdd: str) -> str:
    return f"{yyyymmdd[6:8]}{yyyymmdd[4:6]}{yyyymmdd[:4]}"


def _mask_pan(pan: str) -> str:
    return f"{pan[:6]}{'*'*7}{pan[-3:]}"


def emit_cbs(t: ReconTxn) -> List[str]:
    """Return CBS records for this txn based on cbs_state."""
    src_code = "VDS" if t.network == "VISA" else "MDS"
    state = t.cbs_state
    out: List[str] = []

    def line(amt: int, dr_cr: str, tran_type: str, suffix: str = "") -> str:
        return "|".join([
            "20112",
            f"{amt/100:.2f}",
            dr_cr,
            _mask_pan(t.pan),
            t.rrn,
            f"{int(t.rrn) % 10**14:014d}",
            _ddmmyyyy(t.date_yyyymmdd),
            f"{t.time_hhmmss[:2]}:{t.time_hhmmss[2:4]}:{t.time_hhmmss[4:6]}",
            tran_type, src_code, "00000000",
            f"{t.txn_id+100000000:09d}",
            _ddmmyyyy(t.date_yyyymmdd),
            "0", "0", "0", "I",
        ])

    if state == "Successful":
        out.append(line(t.amount_paise, "D", "PRDR"))
    elif state == "Declined":
        out.append(line(0, "D", "PRDR"))
    elif state == "Successful & Reversed":
        out.append(line(t.amount_paise, "D", "PRDR"))
        out.append(line(t.amount_paise, "C", "PRCR"))
    elif state == "Declined & Reversed":
        out.append(line(0, "D", "PRDR"))
        out.append(line(0, "C", "PRCR"))
    # "Transaction Missing" → emit nothing
    return out


def emit_gl(t: ReconTxn) -> List[str]:
    """Return FSS GL OUT D-records for this txn based on gl_state."""
    state = t.gl_state
    out: List[str] = []
    gl_account = "0000098095102016"   # POS PAYABLE
    date_dd = _ddmmyyyy_compact(t.date_yyyymmdd)

    net_tag = "MC" if t.network == "MC" else "VISA"

    def line(amt: int, dr_cr: str, tran_type: str, narration: str) -> str:
        return "|".join([
            "D", gl_account, "10201", "9900001", date_dd, date_dd, dr_cr,
            _amount_17(amt), f"{t.txn_id+100000000:09d}", narration,
            "", "", "", net_tag, tran_type, t.terminal_id, t.rrn, t.time_hhmmss,
            "", "", "", "", "",
        ])

    if state == "Successful":
        out.append(line(t.amount_paise, "DR", "PRDR", f" REF/POS/{t.merchant_name[:15]}/{t.rrn}"))
    elif state == "Declined":
        out.append(line(0, "DR", "PRDR", f"DECLINED/{t.rrn}"))
    elif state == "Successful & Reversed":
        out.append(line(t.amount_paise, "DR", "PRDR", f" REF/POS/{t.merchant_name[:15]}/{t.rrn}"))
        out.append(line(t.amount_paise, "CR", "PRCR", f"REVERSAL/{t.rrn}"))
    elif state == "Declined & Reversed":
        out.append(line(0, "DR", "PRDR", f"DECLINED/{t.rrn}"))
        out.append(line(0, "CR", "PRCR", f"DECLINED REVERSAL/{t.rrn}"))
    elif state == "Failed":
        out.append(line(0, "DR", "PRDR", f"FAILED/{t.rrn}"))
    # "Transaction Missing" / "Not In Interchange" → emit nothing
    return out


def emit_network_visa(t: ReconTxn) -> List[str]:
    """Return EPIN records (TC05 family) for this txn based on network_state."""
    state = t.network_state
    out: List[str] = []

    # TCR0 base — Visa BASE II 168 chars
    def tcr0(tc_code: str, action: str = "approved") -> str:
        # action: approved/declined/failed
        # We modify the "Action Code" / response area in the template
        # Use existing TCR0 template approach: start from a 168-char skeleton
        rec = list(" " * 168)
        # pos 0-1: tc_code
        for i, ch in enumerate(tc_code):
            rec[i] = ch
        # pos 2: TC qualifier "0"
        rec[2] = "0"
        # pos 3: TCR number "0"
        rec[3] = "0"
        # pos 4-19 (16): PAN
        for i, ch in enumerate(t.pan[:16]):
            rec[4+i] = ch
        # pos 26-48 (23): ARN
        for i, ch in enumerate(t.arn[:23]):
            rec[26+i] = ch
        # pos 50-57: filler
        # pos 57-60 (4): Purchase Date MMDD
        mmdd = t.date_yyyymmdd[4:8]
        for i, ch in enumerate(mmdd):
            rec[57+i] = ch
        # pos 61-72 (12): Destination Amount paise (zfill)
        amt = str(t.amount_paise).zfill(12)
        for i, ch in enumerate(amt[-12:]):
            rec[61+i] = ch
        # pos 73-75 (3): Destination Currency 356
        rec[73:76] = "3", "5", "6"
        # pos 76-87 (12): Source Amount
        for i, ch in enumerate(amt[-12:]):
            rec[76+i] = ch
        rec[88:91] = "3", "5", "6"
        # pos 91-115 (25): Merchant Name
        name = t.merchant_name.ljust(25)[:25]
        for i, ch in enumerate(name):
            rec[91+i] = ch
        # pos 116-128 (13): Merchant City
        city = t.merchant_city.ljust(13)[:13]
        for i, ch in enumerate(city):
            rec[116+i] = ch
        # pos 129-130 (2): Merchant Country
        country = t.merchant_country.ljust(2)[:2]
        rec[129] = country[0]; rec[130] = country[1] if len(country) > 1 else " "
        # pos 132-135 (4): MCC
        for i, ch in enumerate(t.mcc[:4]):
            rec[132+i] = ch
        # pos 152-157 (6): Auth Code
        for i, ch in enumerate(t.auth_code[:6]):
            rec[152+i] = ch
        # pos 161-162 (2): POS Entry Mode
        rec[161] = "0"; rec[162] = "5"

        # Encode action: we use a sentinel char at pos 167 (last char) to mark state
        # Real BASE II uses the response/reason field; for test purposes we just stamp
        # pos 159-160 with 00=approved, 05=declined, 96=failed
        if action == "declined":
            rec[159], rec[160] = "0", "5"
        elif action == "failed":
            rec[159], rec[160] = "9", "6"
        elif action == "reversed":
            rec[159], rec[160] = "1", "9"
        else:
            rec[159], rec[160] = "0", "0"
        return "".join(rec)

    if state == "Successful":
        out.append(tcr0("05", "approved"))
    elif state == "Declined":
        out.append(tcr0("05", "declined"))
    elif state == "Successful & Reversed":
        out.append(tcr0("05", "approved"))
        out.append(tcr0("25", "reversed"))   # TC25 = sales reversal
    elif state == "Declined & Reversed":
        out.append(tcr0("05", "declined"))
        out.append(tcr0("25", "reversed"))
    elif state == "Failed":
        out.append(tcr0("05", "failed"))
    # "Transaction Missing" / "Not In Interchange" → emit nothing in EPIN
    return out


def emit_network_mc(t: ReconTxn, msg_base: int = 1) -> List[str]:
    """Return T112 IPM message blocks for MC, keyed by DE37=RRN.

    Follows the multi-line block format produced by mc_t112_generator.py so
    recon software can parse it with the same reader.
      1240 = First Presentment  (purchase / declined purchase)
      1420 = Reversal Request   (full reversal of a prior 1240)
    DE39 response codes: 00=approved, 57=declined, 96=system failure
    """
    state = t.network_state
    SEP = "=" * 80
    mmdd = t.date_yyyymmdd[4:8]

    def block(mti: str, resp: str, amt: int, msg_num: int) -> List[str]:
        return [
            SEP,
            f"Message number: {msg_num} (offset 0x{(msg_num - 1) * 256:08X})",
            "",
            f"MTI: {mti}",
            "",
            f"Primary bitmap: A028004000C08010",
            f"Secondary bitmap: 0000000000000000",
            f"DE2 (Offset 0x04): '{t.pan}'",
            f"DE3 (Offset 0x12): '000000'",
            f"DE4 (Offset 0x14): '{amt:012d}'",
            f"DE12 (Offset 0x24): '{t.time_hhmmss}'",
            f"DE13 (Offset 0x2A): '{mmdd}'",
            f"DE37 (Offset 0x45): '{t.rrn}'",
            f"DE38 (Offset 0x51): '{t.auth_code}'",
            f"DE39 (Offset 0x57): '{resp}'",
            f"DE41 (Offset 0x59): '{t.terminal_id[:8]}'",
            f"DE43 (Offset 0x63): '{t.merchant_name[:22]:<22}{t.merchant_city[:13]:<13}IN'",
            f"DE49 (Offset 0x69): '356'",
            "",
        ]

    lines: List[str] = []
    if state == "Successful":
        lines = block("1240", "00", t.amount_paise, msg_base)
    elif state == "Declined":
        lines = block("1240", "57", 0, msg_base)
    elif state == "Successful & Reversed":
        lines  = block("1240", "00", t.amount_paise, msg_base)
        lines += block("1420", "00", t.amount_paise, msg_base + 1)
    elif state == "Declined & Reversed":
        lines  = block("1240", "57", 0, msg_base)
        lines += block("1420", "57", 0, msg_base + 1)
    elif state == "Failed":
        lines = block("1240", "96", 0, msg_base)
    # "Transaction Missing" → emit nothing
    return lines


# ---------------------------------------------------------------------------
# RuPay emitters — XML + 863 DAT
# ---------------------------------------------------------------------------

def _rupay_xml_txn(t: ReconTxn, seq: int, mti: str, actn_cd: str,
                   amount: int, dc_ind: str) -> ET.Element:
    """Build one RuPay XML <Txn> element."""
    ard = (t.rrn + "00000000000").zfill(23)[:23]
    el = ET.Element("Txn")
    ET.SubElement(el, "nSeq").text           = str(seq).zfill(6)
    ET.SubElement(el, "nMTI").text           = mti
    ET.SubElement(el, "nFunCd").text         = "200"
    ET.SubElement(el, "nPAN").text           = t.pan
    ET.SubElement(el, "nARD").text           = ard
    ET.SubElement(el, "nRRN").text           = t.rrn          # join key
    ET.SubElement(el, "nAcqInstCd").text     = "00000000000"
    ET.SubElement(el, "nApprvlCd").text      = t.auth_code
    ET.SubElement(el, "nCrdAcptTrmId").text  = t.terminal_id[:8]
    ET.SubElement(el, "nAmtTxn").text        = str(amount).zfill(12)
    ET.SubElement(el, "nCcyCdTxn").text      = "356"
    ET.SubElement(el, "nProcCd").text        = "000000"
    ET.SubElement(el, "nCrdAcpBussCd").text  = t.mcc
    ET.SubElement(el, "nActnCd").text        = actn_cd
    ET.SubElement(el, "nSetDCInd").text      = dc_ind
    ET.SubElement(el, "nDtSet").text         = t.date_yyyymmdd[2:]   # YYMMDD
    ET.SubElement(el, "nAmtSet").text        = str(amount).zfill(12)
    ET.SubElement(el, "nCrsBrdFlg").text     = "N"
    return el


_RUPAY_863_REC_LEN = 340

def _rupay_863_record(t: ReconTxn, msg_type: str, action: str, resp: str, amount: int) -> str:
    """Build one RuPay 863 fixed-width record (340 chars).

    msg_type: "01"=SMS purchase, "04"=reversal
    action:   "A"=approved, "D"=declined
    resp:     "00"=approved, "05"=declined, "96"=failed
    """
    buf = list(" " * _RUPAY_863_REC_LEN)

    def _place(start: int, length: int, value: str, rjust: bool = False) -> None:
        value = str(value)
        if rjust:
            value = value.zfill(length)[:length]
        else:
            value = value[:length].ljust(length)
        for i, ch in enumerate(value):
            if start + i < len(buf):
                buf[start + i] = ch

    mm = t.date_yyyymmdd[4:6]
    dd = t.date_yyyymmdd[6:8]
    yyyy = t.date_yyyymmdd[:4]

    _place( 0, 2, msg_type)          # Message_Type_Function
    _place( 2, 3, "POS")             # Product_Id
    _place( 5, 2, "25")              # Transaction_Type (Purchase SMS)
    _place( 7, 2, "00")              # From_Account_Type
    _place( 9, 2, "00")              # To_Account_Type
    _place(11, 1, action)            # Action_Code A/D
    _place(12, 2, resp)              # Response_Code
    _place(14, 19, t.pan.ljust(19))  # PAN_Number (19 chars)
    _place(33, 6, t.auth_code[:6])   # Approval_Number
    _place(39, 12, t.rrn)            # Retrieval_Reference_Number → join key
    _place(51, 7, mm + dd + yyyy)    # Transaction_Date MMDDYYYY
    _place(58, 6, t.time_hhmmss)     # Transaction_Time
    _place(64, 4, t.mcc)             # Merchant_Category_Code
    _place(68, 15, t.terminal_id[:15])         # Card_Acceptor_ID
    _place(83, 8, t.terminal_id[:8])           # Card_Acceptor_Terminal_ID
    _place(91, 40, f"{t.merchant_name:<22}{t.merchant_city:<13}IN"[:40])  # Location
    _place(131, 11, "00000001234")   # Acquirer_ID
    _place(142, 3, "356")            # Transaction_Currency
    _place(145, 15, str(amount), rjust=True)   # Transaction_Amount
    _place(160, 3, "356")            # Cardholder_Billing_Currency
    _place(163, 15, str(amount), rjust=True)   # Cardholder_Billing_Amount
    _place(178, 2, "51")             # PAN_Entry_Mode (chip)
    _place(180, 1, "1")              # PIN_Entry_Capability
    _place(181, 2, "00")             # POS_Condition_Code
    _place(183, 3, "356")            # Acquirer_Country_Code

    return "".join(buf)


def emit_network_rupay(t: ReconTxn, seq_base: int = 1) -> Dict[str, list]:
    """Return RuPay network records for XML and 863 DAT.

    Returns dict:
      "xml_elements": list of ET.Element  (<Txn> nodes)
      "dat_records":  list of str         (340-char records)
    """
    state = t.network_state
    xml_els: List[ET.Element] = []
    dat_recs: List[str] = []

    if state == "Successful":
        xml_els.append(_rupay_xml_txn(t, seq_base, "1240", "00", t.amount_paise, "D"))
        dat_recs.append(_rupay_863_record(t, "01", "A", "00", t.amount_paise))

    elif state == "Declined":
        xml_els.append(_rupay_xml_txn(t, seq_base, "1240", "05", 0, "D"))
        dat_recs.append(_rupay_863_record(t, "01", "D", "05", 0))

    elif state == "Successful & Reversed":
        xml_els.append(_rupay_xml_txn(t, seq_base,     "1240", "00", t.amount_paise, "D"))
        xml_els.append(_rupay_xml_txn(t, seq_base + 1, "1420", "00", t.amount_paise, "C"))
        dat_recs.append(_rupay_863_record(t, "01", "A", "00", t.amount_paise))
        dat_recs.append(_rupay_863_record(t, "04", "A", "00", t.amount_paise))

    elif state == "Declined & Reversed":
        xml_els.append(_rupay_xml_txn(t, seq_base,     "1240", "05", 0, "D"))
        xml_els.append(_rupay_xml_txn(t, seq_base + 1, "1420", "05", 0, "C"))
        dat_recs.append(_rupay_863_record(t, "01", "D", "05", 0))
        dat_recs.append(_rupay_863_record(t, "04", "D", "05", 0))

    elif state == "Failed":
        xml_els.append(_rupay_xml_txn(t, seq_base, "1240", "96", 0, "D"))
        dat_recs.append(_rupay_863_record(t, "01", "D", "96", 0))

    # "Transaction Missing" → emit nothing in either file

    return {"xml_elements": xml_els, "dat_records": dat_recs}


def emit_network_nfs(t: ReconTxn) -> List[str]:
    """Return NFS records for this txn based on network_state.

    NFS ISS record — 407 fixed-width chars:
      Pos 1-3: Participant ID
      Pos 4-5: Transaction Type (31=ATM)
      Pos 6-9: Gap
      Pos 10-21: RRN (join key)
      Pos 22-23: Response Code (00=approved, 05=declined)
      Pos 24-42: PAN
      Pos 43-49: Gap
      Pos 50-61: STAN (join key)
      Pos 62-67: Date YYMMDD
      Pos 68-73: Time HHMMSS
      Pos 74-77: MCC
      Pos 78-98: Gap
      Pos 99-106: Terminal ID
      Pos 107-146: Gap
      Pos 147-157: Acquirer ID
      Pos 158-221: Gap
      Pos 222-236: Amount (right-aligned, zero-padded)
      Pos 237-251: Actual Amount (right-aligned, zero-padded) - join key
      Pos 252-266: Fee
      Pos 267-269: Currency (356=INR)
      Pos 270-284: Settlement Amount
      Pos 285-299: Settlement Fee
      Pos 300-407: Reserved
    """
    state = t.network_state
    out: List[str] = []
    ISS_LEN = 407

    def _place(buf: list, value: str, start: int, length: int) -> None:
        value = str(value)[:length].ljust(length)
        for i, ch in enumerate(value):
            if start + i < len(buf):
                buf[start + i] = ch

    def _place_r(buf: list, value: str, start: int, length: int) -> None:
        """Right-align numeric value, zero-pad."""
        value = str(value)[:length].zfill(length)
        for i, ch in enumerate(value):
            if start + i < len(buf):
                buf[start + i] = ch

    def build_nfs_record(resp_code: str) -> str:
        """Build NFS ISS record — 407 chars."""
        buf = list(" " * ISS_LEN)
        participant_id = "001"

        _place  (buf, participant_id,                0,  3)   # Participant ID (1–3)
        _place  (buf, "31",                          3,  2)   # Transaction Type (4–5): 31=ATM
        _place  (buf, t.rrn,                         9, 12)   # RRN (10–21)
        _place  (buf, resp_code,                    21,  2)   # Response Code (22–23)
        _place  (buf, t.pan.ljust(19),             23, 19)   # PAN (24–42)
        _place  (buf, t.terminal_id[:12].ljust(12), 49, 12)  # STAN (50–61) - use term_id for seq
        yy = t.date_yyyymmdd[2:4]
        mm = t.date_yyyymmdd[4:6]
        dd = t.date_yyyymmdd[6:8]
        _place  (buf, yy + mm + dd,                 61,  6)   # Date YYMMDD (62–67)
        _place  (buf, t.time_hhmmss,                67,  6)   # Time HHMMSS (68–73)
        _place  (buf, "6011",                       73,  4)   # MCC (74–77): 6011=ATM
        _place  (buf, t.terminal_id[:8],           98,  8)   # Terminal ID (99–106)
        _place  (buf, "00000001234",               146, 11)   # Acquirer ID (147–157)
        _place_r(buf, t.amount_paise,              221, 15)   # Amount (222–236)
        _place_r(buf, t.amount_paise,              236, 15)   # Actual Amount (237–251)
        _place_r(buf, 0,                           251, 15)   # Fee (252–266)
        _place  (buf, "356",                       266,  3)   # Currency (267–269)
        _place_r(buf, t.amount_paise,              269, 15)   # Settlement Amount (270–284)
        _place_r(buf, 0,                           284, 15)   # Settlement Fee (285–299)

        return "".join(buf)

    if state == "Successful":
        out.append(build_nfs_record("00"))
    elif state == "Declined":
        out.append(build_nfs_record("05"))
    elif state == "Successful & Reversed":
        out.append(build_nfs_record("00"))
        out.append(build_nfs_record("00"))  # Reversal with same response
    elif state == "Declined & Reversed":
        out.append(build_nfs_record("05"))
        out.append(build_nfs_record("05"))  # Declined reversal
    elif state == "Failed":
        out.append(build_nfs_record("96"))  # 96 = Failed
    # "Transaction Missing" / "Not In Interchange" → emit nothing
    return out


# ---------------------------------------------------------------------------
# Bundle writer
# ---------------------------------------------------------------------------

def write_bundle(txns: List[ReconTxn], out_dir: Path, network: str) -> Dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)

    # CBS file
    cbs_path = out_dir / f"cbs_{network.lower()}.txt"
    cbs_count = 0
    with open(cbs_path, "w") as f:
        f.write(f"FH{txns[0].date_yyyymmdd}000002\n")
        for t in txns:
            for line in emit_cbs(t):
                f.write(line + "\n")
                cbs_count += 1

    # FSS GL OUT
    gl_path = out_dir / "fss_gl_out.txt"
    gl_count = 0
    gl_lines: List[str] = []
    for t in txns:
        gl_lines.extend(emit_gl(t))
    total_dr = sum(t.amount_paise for t in txns
                   if t.gl_state in ("Successful", "Successful & Reversed"))
    date_dd = _ddmmyyyy_compact(txns[0].date_yyyymmdd)
    net_label = network.upper()
    with open(gl_path, "w") as f:
        f.write(f"H|{date_dd}|0000098094102017|{net_label} ATM PAYABLE A/C|{_amount_17(0)}|CR\n")
        f.write(f"H|{date_dd}|0000098095102016|{net_label} POS PAYABLE A/C|{_amount_17(total_dr)}|CR\n")
        f.write(f"H|{date_dd}|0000098106102018|{net_label} ACQUIRING RECEIVABLE|{_amount_17(0)}|DR\n")
        for line in gl_lines:
            f.write(line + "\n")
            gl_count += 1

    # Network file — format depends on network
    if network.upper() == "NFS":
        net_path = out_dir / "nfs_iss.txt"
        net_count = 0
        with open(net_path, "w") as f:
            for t in txns:
                for line in emit_network_nfs(t):
                    f.write(line + "\n")
                    net_count += 1
        net_paths = {"network": {"path": str(net_path), "records": net_count}}

    elif network.upper() == "MC":
        net_path = out_dir / "t112.txt"
        net_count = 0
        msg_num = 1
        with open(net_path, "w") as f:
            for t in txns:
                lines = emit_network_mc(t, msg_base=msg_num)
                for line in lines:
                    f.write(line + "\n")
                if t.network_state in ("Successful & Reversed", "Declined & Reversed"):
                    msg_num += 2; net_count += 2
                elif t.network_state != "Transaction Missing":
                    msg_num += 1; net_count += 1
        net_paths = {"t112": {"path": str(net_path), "records": net_count}}

    elif network.upper() == "RUPAY":
        # Two files: XML clearing + 863 fixed-width DAT
        xml_path = out_dir / "rupay_iss.xml"
        dat_path = out_dir / "rupay_863_iss.dat"

        # Build XML tree
        root = ET.Element("File")
        hdr = ET.SubElement(root, "Hdr")
        ET.SubElement(hdr, "nMTI").text        = "1644"
        ET.SubElement(hdr, "nDtSet").text      = txns[0].date_yyyymmdd[2:]
        ET.SubElement(hdr, "nMemInstCd").text  = "123456"
        ET.SubElement(hdr, "nUnFlNm").text     = f"RUPAY_ISS_{txns[0].date_yyyymmdd[2:]}_123456"
        ET.SubElement(hdr, "nFlCatg").text     = "ISS"
        ET.SubElement(hdr, "nFlRejInd").text   = "0"
        ET.SubElement(hdr, "nFlRejRsnCd").text = ""

        txn_block = ET.SubElement(root, "TxnBlock")
        dat_lines: List[str] = []
        seq = 1
        xml_count = 0
        dat_count = 0
        run_total = 0

        for t in txns:
            result = emit_network_rupay(t, seq_base=seq)
            for el in result["xml_elements"]:
                txn_block.append(el)
                xml_count += 1
                if el.find("nActnCd") is not None and el.find("nActnCd").text == "00":
                    amt_txt = el.find("nAmtTxn").text or "0"
                    run_total += int(amt_txt)
                seq += 1
            for rec in result["dat_records"]:
                dat_lines.append(rec)
                dat_count += 1

        trl = ET.SubElement(root, "Trl")
        ET.SubElement(trl, "nTxnCnt").text   = str(xml_count)
        ET.SubElement(trl, "nRnTtlAmt").text = str(run_total).zfill(12)

        raw_xml = ET.tostring(root, encoding="unicode")
        try:
            from xml.dom import minidom
            pretty = minidom.parseString(raw_xml).toprettyxml(indent="  ")
            pretty = "\n".join(l for l in pretty.splitlines() if l.strip())
        except Exception:
            pretty = raw_xml
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(pretty + "\n")

        # Write 863 DAT file
        now = datetime.now()
        yy_date = txns[0].date_yyyymmdd[2:]
        hdr_863 = f"HDR{now.strftime('%y%m%d')}{now.strftime('%H%M%S')}{yy_date}{'123456'.ljust(11)}P01.00"
        run_total_863 = sum(
            int(r[145:160]) for r in dat_lines if r[11] == "A"
        )
        trl_863 = f"TRL{str(dat_count).zfill(8)}{str(run_total_863).zfill(15)}"
        with open(dat_path, "w", encoding="utf-8") as f:
            f.write(hdr_863 + "\n")
            for rec in dat_lines:
                f.write(rec + "\n")
            f.write(trl_863 + "\n")

        net_paths = {
            "rupay_xml": {"path": str(xml_path), "records": xml_count},
            "rupay_863": {"path": str(dat_path), "records": dat_count},
        }

    else:  # VISA
        net_path = out_dir / f"epin_{network.lower()}.txt"
        net_count = 0
        with open(net_path, "w") as f:
            for t in txns:
                for line in emit_network_visa(t):
                    f.write(line + "\n")
                    net_count += 1
        net_paths = {"epin": {"path": str(net_path), "records": net_count}}

    # Master scenario table — what matched/missed where, RRN-keyed
    master_path = out_dir / "scenario_master_table.csv"
    with open(master_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["txn_id", "scenario_id", "rrn", "arn", "pan_masked",
                    "amount_paise", "date", "cbs_state", "gl_state", "network_state"])
        for t in txns:
            w.writerow([t.txn_id, t.scenario_id, t.rrn, t.arn, _mask_pan(t.pan),
                        t.amount_paise, t.date_yyyymmdd,
                        t.cbs_state, t.gl_state, t.network_state])

    # Scenario distribution summary
    file_counts = {
        "cbs":    {"path": str(cbs_path), "records": cbs_count},
        "fss_gl": {"path": str(gl_path),  "records": gl_count + 3},
        **net_paths,
    }
    summary = {
        "total_transactions": len(txns),
        "network": network,
        "business_date": txns[0].date_yyyymmdd,
        "scenario_distribution": _scenario_counts(txns),
        "file_counts": file_counts,
    }
    with open(out_dir / "scenario_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    return file_counts


def _scenario_counts(txns: List[ReconTxn]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for t in txns:
        out[t.scenario_id] = out.get(t.scenario_id, 0) + 1
    return out


# ---------------------------------------------------------------------------
# Distribution + main
# ---------------------------------------------------------------------------

def distribute(num_txns: int, scenario_ids: List[int], rng: random.Random) -> List[int]:
    """Spread num_txns across the given scenario_ids as evenly as possible."""
    n = len(scenario_ids)
    base = num_txns // n
    rem  = num_txns - base * n
    out: List[int] = []
    for i, sid in enumerate(scenario_ids):
        cnt = base + (1 if i < rem else 0)
        out.extend([sid] * cnt)
    rng.shuffle(out)
    return out


def _zip_and_deliver(out_dir: Path) -> Optional[Path]:
    import platform
    if not out_dir.exists() or not any(out_dir.iterdir()): return None
    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = downloads / f"{out_dir.name}_{ts}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in out_dir.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(out_dir.parent))
    if platform.system() == "Darwin":
        try: subprocess.run(["open", "--reveal", str(zip_path)], timeout=5)
        except Exception: pass
    return zip_path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Recon scenario matrix generator (50 cross-file states)")
    p.add_argument("--num-txns", type=int, default=1000)
    p.add_argument("--network", choices=["VISA", "MC", "NFS", "RUPAY"], default="VISA")
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--scenarios", default="all",
                   help="comma-separated scenario IDs (e.g. 1,2,3,49,50) or 'all' for 1-50")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output-dir", default="")
    p.add_argument("--no-download", action="store_true")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    # Pick scenarios
    if args.scenarios == "all":
        sids = [s[0] for s in SCENARIOS]
    else:
        sids = [int(x) for x in args.scenarios.split(",") if x.strip()]
    selected = [s for s in SCENARIOS if s[0] in sids]
    if not selected:
        print("error: no valid scenario IDs selected", file=sys.stderr); return 2

    rng = random.Random(args.seed)
    rrn_start = rng.randint(600_000_000_000, 699_999_999_999)

    assigned = distribute(args.num_txns, [s[0] for s in selected], rng)
    by_id = {s[0]: s for s in selected}

    txns = [_make_txn(i, by_id[assigned[i]], bdate, args.network, rng, rrn_start)
            for i in range(args.num_txns)]

    out_dir = Path(args.output_dir or f"out_recon_{bdate}_{args.network}")
    out_dir.mkdir(parents=True, exist_ok=True)

    file_counts = write_bundle(txns, out_dir, args.network)
    print(f"\n  Generated {args.num_txns} transactions across {len(selected)} scenarios → {out_dir}/")
    for fname, info in file_counts.items():
        print(f"    {fname:<8} → {info['records']:>5} records  {info['path']}")
    print(f"    {'master':<8} → scenario_master_table.csv")
    print(f"    {'summary':<8} → scenario_summary.json")

    if not args.no_download:
        zip_path = _zip_and_deliver(out_dir.resolve())
        if zip_path:
            print(f"\n  📦 Downloaded → {zip_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
