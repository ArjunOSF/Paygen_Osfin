"""
mc_t112_generator.py
====================
Mastercard T112 IPM clearing file generator.
Ported from ipm_generator_v3.py and adapted to accept pre-generated Transaction objects
so that PAN / amount / RRN / approval code are consistent with all other files.

Key fix vs original: DE37 (RRN, 12 chars) is now populated from transaction.rrn.
This is REQUIRED for ATM_C.RR_NO → T112.DE37 join (knowledge repo section 8).

Output format: multi-line "Message number: N" block format matching the ISO 8583
parser output style (same as script_for_t112.js). Each message block contains:
  - Message number, offset, MTI
  - Primary / Secondary bitmap (hex bytes)
  - DE N (Offset 0xXX): 'value' lines
  - pds sub-field annotation lines
Messages are separated by "====..." lines; file opens with file-level header and
closes with "File checksum: 0" + parser attribution.
"""

import random
import string
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from data_generator import Transaction

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MTI_FIRST_PRESENT = "1240"
MTI_CONTROL       = "1644"

FC_FILE_HEADER         = "697"
FC_FILE_TRAILER        = "695"
FC_FIRST_PRESENTMENT   = "200"
FC_FINANCIAL_POSITION  = "685"
FC_SETTLEMENT_POSITION = "688"

PROC_MODE_TEST        = "T"
PROC_MODE_PRODUCTION  = "P"
SETTLE_IND_MC_NET     = "M"
DE25_FIRST_PRESENT    = "1401"
MRC_NOTIFY_RECONCILIATION = "6862"

CURRENCIES: Dict[str, Tuple[str, int]] = {
    "INR": ("356", 2),
    "USD": ("840", 2),
    "EUR": ("978", 2),
}

GCMS_PRODUCTS = {
    "standard": "MCC", "debit": "MDS", "world": "MWE",
    "platinum": "MPL", "business": "MBE", "prepaid": "MPP",
}

MERCHANT_TEMPLATES = [
    {"name": "Amazon Pay India Pvt L\\#26/1, BRIGADE GATEWAY\\BANGALORE    \\560055       IND", "mcc": "5999", "id_prefix": "AMZ"},
    {"name": "Swiggy Foods          \\BENGALURU TECH PARK\\BENGALURU    \\560103       IND",     "mcc": "5812", "id_prefix": "SWG"},
    {"name": "Zomato Media Pvt Ltd  \\22ND FLOOR, ONE HORIZON CENTRE\\GURGAON      \\122009       IND", "mcc": "5812", "id_prefix": "ZOM"},
    {"name": "MakeMyTrip            \\TOWER B, SECTOR 24, DLF PHASE 3\\GURUGRAM     \\122002       IND", "mcc": "4722", "id_prefix": "MMT"},
    {"name": "Apollo Pharmacy       \\APOLLO HEALTH CITY, JUBILEE HILLS\\HYDERABAD    \\500033       IND", "mcc": "5912", "id_prefix": "APL"},
]

ATM_MERCHANT = {"name": "HDFC LAB BRANCH       \\SECTOR 17, CBD BELAPUR\\MUMBAI       \\400614       IND", "mcc": "6011", "id_prefix": "ATM"}

# ---------------------------------------------------------------------------
# PDS / DE builders (unchanged business logic)
# ---------------------------------------------------------------------------

def build_de48(pds_dict: Dict[str, str]) -> str:
    parts = []
    for tag in sorted(pds_dict.keys()):
        val = pds_dict[tag]
        parts.append(f"{tag}{len(val):03d}{val}")
    return "".join(parts)


def build_pds0105(member_id: str, business_date: str, file_seq: str = "003") -> str:
    date_yymmdd   = business_date[2:]
    member_padded = member_id.zfill(11)
    seq           = file_seq[-2:].zfill(2)
    return f"001{date_yymmdd}{member_padded}{seq}301"


def build_pds0148(txn_cur_num: str, txn_exp: int, ch_cur_num: str, ch_exp: int) -> str:
    return f"{txn_cur_num}{txn_exp}{ch_cur_num}{ch_exp}"


def build_pds0158(business_date: str, txn_cur_num: str = "356",
                  clearing_code: str = "MCC4", cycle: str = "030", proc_flag: str = "PF") -> str:
    date_ymd = business_date[2:]
    return f"{clearing_code}{txn_cur_num}001{proc_flag}{date_ymd}{cycle}   NNNNNNN"


def build_pds0159(member_id: str, business_date: str,
                  settlement_account: str = "015020110000305             3AP000356",
                  cycle: str = "03") -> str:
    date_ymd  = business_date[2:]
    next_day  = (datetime.strptime(business_date, "%Y%m%d") + timedelta(days=1)).strftime("%y%m%d")
    inst      = member_id.ljust(11)[:11]
    result    = f"{inst}{settlement_account}01N{date_ymd}{cycle}{next_day}01"
    return result[:64].ljust(64)


def build_de22(pan_entry: str, rng: random.Random) -> str:
    entry_map = {
        "05": "09045079900A",
        "07": "600550S99000",
        "02": "09022079900A",
        "01": "05005019900A",
        "00": "09000079900A",
    }
    return entry_map.get(pan_entry, "09045079900A")


def build_de63(product_code: str, rng: random.Random, business_date: str) -> str:
    date_mmdd = business_date[4:8]
    ref = "".join(rng.choices(string.ascii_uppercase + string.digits, k=6))
    return f" {product_code}{ref}{date_mmdd}  "


def dc_amount(amount: int, is_debit: bool, length: int = 15) -> str:
    return ("D" if is_debit else "C") + str(abs(amount)).zfill(length)


def format_amount(amount: int, length: int = 12) -> str:
    return str(abs(amount)).zfill(length)


# ---------------------------------------------------------------------------
# Bitmap computation
# ---------------------------------------------------------------------------

def compute_bitmap(des: List[int]) -> Tuple[str, str]:
    """Return (primary_hex, secondary_hex) for the given list of DE numbers."""
    bits = [0] * 128
    if any(d > 64 for d in des):
        bits[0] = 1  # secondary bitmap present
    for d in des:
        bits[d - 1] = 1

    def to_hex_bytes(start: int) -> str:
        result = []
        for i in range(start, start + 8):
            b = 0
            for j in range(8):
                b = (b << 1) | bits[i * 8 + j]
            result.append(f"{b:02X}")
        return " ".join(result)

    return to_hex_bytes(0), to_hex_bytes(8)


# ---------------------------------------------------------------------------
# Message block formatter
# ---------------------------------------------------------------------------

def _format_message_block(
    msg_num: int,
    msg_offset: int,
    mti: str,
    des_present: List[int],
    field_entries: List[Tuple[int, int, str]],   # (de_num, field_offset, value)
    pds_annotations: List[str],
    is_first: bool = False,
) -> List[str]:
    """Return the lines for one complete message block."""
    primary, secondary = compute_bitmap(des_present)
    L = []
    if not is_first:
        L.append("=========================================================")
    L.append(f"Message number: {msg_num}")
    L.append(f"Offset: 0x{msg_offset:x}")
    L.append(f"MTI: {mti}")
    L.append(f"Primary bitmap: {primary}")
    L.append(f"Secondary bitmap: {secondary}")
    L.append("Fields: ")
    for de_num, foff, value in field_entries:
        L.append(f"DE {de_num} (Offset 0x{foff:x}): '{value}'")
    L.extend(pds_annotations)
    L.append("")
    return L


# ---------------------------------------------------------------------------
# Per-message-type builders
# ---------------------------------------------------------------------------

def _file_header_block(msg_num: int, msg_offset: int, pds0105: str, proc_mode: str) -> List[str]:
    de48_val = build_de48({"0105": pds0105, "0122": proc_mode})
    foff = msg_offset + 0x10
    entries = [
        (24, foff,                   FC_FILE_HEADER),
        (48, foff + 3,               de48_val),
        (71, foff + 3 + len(de48_val), "00000001"),
    ]
    pds_ann = [
        f"pds0105: '{pds0105}'",
        f"pds0122: '{proc_mode}'",
    ]
    return _format_message_block(msg_num, msg_offset, MTI_CONTROL, [24, 48, 71], entries, pds_ann, is_first=True)


def _txn_1240_block(
    txn: Transaction,
    msg_num: int,
    msg_offset: int,
    pds0105: str,
    member_id: str,
    acquiring_ica: str,
    business_date: str,
    merchant: dict,
    processing_code: str,
    rng: random.Random,
) -> List[str]:
    txn_cur_num, txn_exp = CURRENCIES["INR"]
    product_code = "MDS"
    pan_entry    = "05"

    pds_dict = {
        "0002": product_code,
        "0003": product_code,
        "0015": f"{business_date[2:]}1",
        "0023": "CT6",
        "0052": "212",
        "0148": build_pds0148(txn_cur_num, txn_exp, txn_cur_num, txn_exp),
        "0158": build_pds0158(business_date, txn_cur_num=txn_cur_num),
        "0159": build_pds0159(member_id, business_date),
        "0165": SETTLE_IND_MC_NET,
        "0177": "N ",
        "0191": "2",
    }
    de48_val = build_de48(pds_dict)

    merchant_id_str = (merchant["id_prefix"] + txn.terminal_id[:8])[:15].ljust(15)
    de22_val = build_de22(pan_entry, rng)
    de63_val = build_de63(product_code, rng, business_date)
    txn_dt   = txn.date_yymmdd + txn.time_hhmmss
    amt12    = format_amount(txn.amount)
    expiry   = f"{rng.randint(26, 30):02d}{rng.randint(1, 12):02d}"

    des = [2, 3, 4, 5, 6, 9, 10, 12, 22, 24, 25, 26, 31, 32, 33, 37, 38, 41, 42, 43, 48, 49, 50, 51, 63, 71, 93, 94, 100]

    # Track field offsets linearly within the message
    foff = msg_offset + 0x0a
    field_specs = [
        (2,   len(str(txn.pan)),            str(txn.pan)),
        (3,   6,                             processing_code),
        (4,   12,                            amt12),
        (5,   12,                            amt12),
        (6,   12,                            amt12),
        (9,   8,                             "10000000"),
        (10,  8,                             "10000000"),
        (12,  12,                            txn_dt),
        (22,  len(de22_val),                 de22_val),
        (24,  3,                             FC_FIRST_PRESENTMENT),
        (25,  len(DE25_FIRST_PRESENT),       DE25_FIRST_PRESENT),
        (26,  4,                             txn.mcc),
        (31,  23,                            txn.rrn.zfill(23)[:23]),
        (32,  len(acquiring_ica.zfill(11)),  acquiring_ica.zfill(11)),
        (33,  6,                             acquiring_ica[-6:].zfill(6)),
        (37,  len(txn.rrn),                  txn.rrn),          # CRITICAL JOIN KEY → ATM_C.RR_NO
        (38,  6,                             txn.approval_code),
        (41,  8,                             txn.terminal_id[:8].ljust(8)),
        (42,  15,                            merchant_id_str),
        (43,  len(merchant["name"]),         merchant["name"]),
        (48,  len(de48_val),                 de48_val),
        (49,  3,                             txn_cur_num),
        (50,  3,                             txn_cur_num),
        (51,  3,                             "840"),
        (63,  len(de63_val),                 de63_val),
        (71,  8,                             str(msg_num).zfill(8)),
        (93,  11,                            member_id.zfill(6)),
        (94,  11,                            acquiring_ica.zfill(6)),
        (100, 6,                             member_id.zfill(6)),
    ]

    entries = []
    for de_num, de_len, de_val in field_specs:
        entries.append((de_num, foff, de_val))
        foff += de_len

    pds_descriptions = {
        "0002": "GCMS Product Identifier",
        "0003": "Licensed Product Identifier",
        "0148": f"Currency {txn_cur_num} exponent {txn_exp}",
        "0165": "Settlement: Mastercard",
        "0177": "Cross-border: domestic",
    }
    pds_ann = []
    for tag in sorted(pds_dict.keys()):
        val  = pds_dict[tag]
        desc = pds_descriptions.get(tag)
        line = f"pds{tag}: '{val}'"
        if desc:
            line += f" ({desc})"
        pds_ann.append(line)

    # Reversal (CHANGE 8): MTI 1420 + DE24=400 for reversal records
    mti_used = "1420" if txn.is_reversal else MTI_FIRST_PRESENT
    if txn.is_reversal:
        # Flip DE24 from 200 (1st presentment) to 400 (reversal)
        entries = [
            (de_num, foff, "400") if de_num == 24 else (de_num, foff, value)
            for (de_num, foff, value) in entries
        ]
    return _format_message_block(msg_num, msg_offset, mti_used, des, entries, pds_ann)


def _financial_position_block(
    msg_num: int, msg_offset: int, pds0105: str,
    member_id: str, acquiring_ica: str, business_date: str,
    total_debit: int, total_credit: int, debit_count: int, credit_count: int,
) -> List[str]:
    net = total_credit - total_debit
    net_is_debit = net <= 0
    txn_cur_num, txn_exp = CURRENCIES["INR"]
    pds_dict = {
        "0148": build_pds0148(txn_cur_num, txn_exp, txn_cur_num, txn_exp),
        "0165": SETTLE_IND_MC_NET,
        "0300": pds0105,
        "0302": "I",
        "0372": "1240200",
        "0374": "00",
        "0378": "O",
        "0380": dc_amount(total_debit,  True),
        "0381": dc_amount(total_credit, False),
        "0384": dc_amount(abs(net), net_is_debit),
        "0390": dc_amount(total_debit,  True),
        "0391": dc_amount(total_credit, False),
        "0392": "00" + dc_amount(0, True),
        "0393": "00" + dc_amount(abs(net) if not net_is_debit else 0, False),
        "0394": dc_amount(abs(net), net_is_debit),
        "0400": str(debit_count).zfill(10),
        "0401": str(credit_count).zfill(10),
        "0402": str(debit_count + credit_count).zfill(10),
    }
    de48_val = build_de48(pds_dict)
    foff = msg_offset + 0x10
    entries = [
        (24,  foff,                  FC_FINANCIAL_POSITION),
        (25,  foff + 3,              MRC_NOTIFY_RECONCILIATION),
        (48,  foff + 7,              de48_val),
        (49,  foff + 7  + len(de48_val), txn_cur_num),
        (50,  foff + 10 + len(de48_val), txn_cur_num),
        (71,  foff + 13 + len(de48_val), str(msg_num).zfill(8)),
        (93,  foff + 21 + len(de48_val), member_id.zfill(6)),
        (100, foff + 32 + len(de48_val), member_id.zfill(6)),
    ]
    pds_ann = [f"pds{k}: '{v}'" for k, v in sorted(pds_dict.items())]
    return _format_message_block(msg_num, msg_offset, MTI_CONTROL, [24, 25, 48, 49, 50, 71, 93, 100], entries, pds_ann)


def _settlement_position_block(
    msg_num: int, msg_offset: int, pds0105: str,
    member_id: str, business_date: str,
    total_debit: int, total_credit: int,
) -> List[str]:
    net = total_credit - total_debit
    net_is_debit = net <= 0
    txn_cur_num, txn_exp = CURRENCIES["INR"]
    pds_dict = {
        "0148": build_pds0148(txn_cur_num, txn_exp, txn_cur_num, txn_exp),
        "0300": pds0105,
        "0302": "I",
        "0359": build_pds0159(member_id, business_date),
        "0367": "MCC",
        "0368": "FP",
        "0390": dc_amount(total_debit,  True),
        "0391": dc_amount(total_credit, False),
        "0392": "00" + dc_amount(0, True),
        "0393": "00" + dc_amount(abs(net) if not net_is_debit else 0, False),
        "0394": dc_amount(abs(net), net_is_debit),
    }
    de48_val = build_de48(pds_dict)
    foff = msg_offset + 0x10
    entries = [
        (24,  foff,                  FC_SETTLEMENT_POSITION),
        (25,  foff + 3,              MRC_NOTIFY_RECONCILIATION),
        (48,  foff + 7,              de48_val),
        (49,  foff + 7  + len(de48_val), txn_cur_num),
        (50,  foff + 10 + len(de48_val), txn_cur_num),
        (71,  foff + 13 + len(de48_val), str(msg_num).zfill(8)),
        (93,  foff + 21 + len(de48_val), member_id.zfill(6)),
        (100, foff + 32 + len(de48_val), member_id.zfill(6)),
    ]
    pds_ann = [f"pds{k}: '{v}'" for k, v in sorted(pds_dict.items())]
    return _format_message_block(msg_num, msg_offset, MTI_CONTROL, [24, 25, 48, 49, 50, 71, 93, 100], entries, pds_ann)


def _file_trailer_block(
    msg_num: int, msg_offset: int, pds0105: str,
    member_id: str, acquiring_ica: str,
    de4_checksum: int, total_msg_count: int, effective_txn_count: int,
) -> List[str]:
    total_amt_str = format_amount(de4_checksum, 12)
    total_msg_str = str(total_msg_count).zfill(8)
    de72_val      = f"FILE CONTAINS {effective_txn_count} FINANCIAL TRANSACTIONS"
    pds_dict = {
        "0105": pds0105,
        "0301": total_amt_str,
        "0306": total_msg_str,
    }
    de48_val = build_de48(pds_dict)
    foff = msg_offset + 0x10
    extra = len(de48_val)
    entries = [
        (24,  foff,                   FC_FILE_TRAILER),
        (25,  foff + 3,               "9900"),
        (48,  foff + 7,               de48_val),
        (71,  foff + 7  + extra,      total_msg_str),
        (72,  foff + 15 + extra,      de72_val),
        (93,  foff + 15 + extra + len(de72_val),      f"00000{member_id}"),
        (94,  foff + 26 + extra + len(de72_val),      f"00000{acquiring_ica}"),
        (100, foff + 37 + extra + len(de72_val),      member_id.zfill(6)),
    ]
    pds_ann = [
        f"pds0105: '{pds0105}'",
        f"pds0301: '{total_amt_str}' (Total amount checksum)",
        f"pds0306: '{total_msg_str}' (Total message count)",
    ]
    return _format_message_block(msg_num, msg_offset, MTI_CONTROL, [24, 25, 48, 71, 72, 93, 94, 100], entries, pds_ann)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate(
    transactions: List[Transaction],
    config: dict,
    business_date: str,
    channel: str,
    output_path: str,
    seed: Optional[int] = None,
) -> int:
    """Write T112 file in multi-line ISO 8583 message block format. Returns count of 1240 messages written."""
    member_id     = config.get("member_id", "021577")
    acquiring_ica = config.get("acquiring_ica", "008653")
    pds0105       = build_pds0105(member_id, business_date)
    rng           = random.Random(seed or 42)
    is_atm        = channel.upper() == "ATM"
    processing_code = "011000" if is_atm else "000000"

    now       = datetime.now()
    file_time = now.strftime("%H%M%S")

    msg_num    = [1]
    msg_offset = [0]

    def next_msg() -> Tuple[int, int]:
        n   = msg_num[0];    msg_num[0]    += 1
        off = msg_offset[0]; msg_offset[0] += 0xc0 + rng.randint(0, 0x40)
        return n, off

    total_debit  = 0
    total_credit = 0
    debit_count  = 0
    credit_count = 0
    de4_checksum = 0
    written      = 0
    all_lines: List[str] = []

    # File-level header (matches ISO 8583 parser output header)
    all_lines.append(f"ISO 8583 file: MCI.AR.T112.M.E{member_id}.D{business_date}.T{file_time}.A001")
    all_lines.append("Encoding: EBCDIC")
    all_lines.append("Container (layout): MC1014")
    all_lines.append("Structure definition: MASTERCARD")
    all_lines.append("")
    all_lines.append("Messages:")
    all_lines.append("")

    # Message 1 — file header (MTI 1644, FC 697)
    hdr_n, hdr_off = next_msg()
    all_lines.extend(_file_header_block(hdr_n, hdr_off, pds0105, PROC_MODE_TEST))

    # One 1240 message per in_network transaction
    for i, txn in enumerate(transactions):
        if not txn.in_network:
            continue
        merchant = ATM_MERCHANT if is_atm else MERCHANT_TEMPLATES[i % len(MERCHANT_TEMPLATES)]
        mn, moff = next_msg()
        all_lines.extend(_txn_1240_block(
            txn=txn, msg_num=mn, msg_offset=moff,
            pds0105=pds0105, member_id=member_id, acquiring_ica=acquiring_ica,
            business_date=business_date, merchant=merchant,
            processing_code=processing_code, rng=rng,
        ))
        total_debit  += txn.amount
        debit_count  += 1
        de4_checksum += txn.amount
        written      += 1

    # Financial position (MTI 1644, FC 685)
    fp_n, fp_off = next_msg()
    all_lines.extend(_financial_position_block(
        fp_n, fp_off, pds0105, member_id, acquiring_ica, business_date,
        total_debit, total_credit, debit_count, credit_count,
    ))

    # Settlement position (MTI 1644, FC 688)
    sp_n, sp_off = next_msg()
    all_lines.extend(_settlement_position_block(
        sp_n, sp_off, pds0105, member_id, business_date, total_debit, total_credit,
    ))

    # File trailer (MTI 1644, FC 695)
    total_msgs   = msg_num[0] - 1
    ft_n, ft_off = next_msg()
    all_lines.extend(_file_trailer_block(
        ft_n, ft_off, pds0105, member_id, acquiring_ica,
        de4_checksum, total_msgs + 1, written,
    ))

    # File-level footer
    all_lines.append("File checksum: 0")
    all_lines.append("")
    all_lines.append("ISO 8583 parser by Sergey V. Shakshin")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(all_lines) + "\n")

    return written
