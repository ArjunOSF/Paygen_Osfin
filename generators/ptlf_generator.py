"""
ptlf_generator.py
=================
PTLF — POS Transaction Log File — PROCESSOR-SPECIFIC FORMAT.

CONFIRMED FROM REAL SAMPLE FILE (March 2026):
  Source: ptlfxIDFC20260329_26033000504708.txt (IDFC Bank POS switch)
  Record length: 1904 chars (NOT BASE24 2610)
  Header line:   2052 chars (file header, one per file)

Positions confirmed (0-indexed):
  0     6   Record length       always "001904"
  6     6   Body length         always "001898"
  12    2   Direction           "DR"=debit
  14   18   Processor ID        "172145892515000000" (fixed IDFC processor)
  44   14   Retailer Terminal ID
  65    4   Acquiring Inst Code "IDFE"=IDFC switched, "MDUI"=MC MIP direct
  69    4   Issuing Inst Code   (mirrors acquiring)
  81   24   Merchant ref / Retailer ID
  197   4   MessageType         "0210"=auth response
  201   4   Flag                "0073" or "0075" (not a simple response code)
  204  12   SEQ_NO              primary join key → ATM_C.SEQ_NO
  264   6   Date                YYMMDD
  270   8   Time                HHMMSSCC (centiseconds)
  308  25   Merchant Name
  333  13   Merchant City (first 13 of name)
  384  12   Amount              paise, zero-padded
  427   4   Card BIN            first 4 of PAN
  431   4   Network text        "VISA" or "XXXX" (masked MC/others)

UNCONFIRMED POSITIONS (flagged — generator leaves template values):
  - ResponseCode       (success/fail indicator — pos 201 may encode this)
  - IssuerCode         (on-us vs not-on-us)
  - PANEntryMode       (physical chip vs ecom)
  - ApprovalCode
  - OriginalSeqNum     (for reversals)

SEQ_NO uniqueness note: in the real sample, pos 204 SEQ_NO repeats across
records within the same batch window. True per-txn uniqueness may require
(terminal_id + time). We use txn.seq_no here — recon joins at SEQ_NO level.
"""

from typing import List
from data_generator import Transaction

RECORD_LEN = 1904

# Template record — captured verbatim from real IDFC PTLF sample.
# Variable fields are overwritten per transaction; static regions preserved.
_TEMPLATE = (
    "001904001898DR172145892515000000001PRO2IDFC4235799100157581   000IDFEIDFE0000000"
    "089051754       000089051754        000000IDFEIDFE89051754        23301200890517"
    "54        589051754       0000      002100075991774720812330      1774720812701 "
    "     1774720812701      26032823301200260328260328260328608718775269AMAZONIN    "
    "             AMAZONIN              2233554466   XXXINIDFE   00330000004332740000"
    "0000000                    5411VISA0000102110V 00000010186317320  00100000000000"
    "0012980000000000000000000003102XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX             "
    "       000000000000                                     000000544236  6        0"
    "000                    XX000005910003563560000000100061000000XXXXXXXXXXXXXXXXXXX"
    "1220000    0000000000089051754   XXXXXXXXXXXXXXX233012000328            XXXX0000"
    "                              10                                                "
    "0012396087648125069XXXXXXXXXXXX                                                 "
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
assert len(_TEMPLATE) == RECORD_LEN, f"Template length {len(_TEMPLATE)} != {RECORD_LEN}"


# Acquiring institution code by network
_ACQ_INST_MAP = {
    "VISA":  "IDFE",
    "MC":    "MDUI",
    "RUPAY": "IDFE",
    "NFS":   "IDFE",
}

# Network text at pos 431 — Visa shows plain, MC/others masked
_NETWORK_TEXT_MAP = {
    "VISA":  "VISA",
    "MC":    "XXXX",
    "RUPAY": "RPAY",
    "NFS":   "XXXX",
}


def _place(buf: list, value: str, start: int, length: int) -> None:
    value = str(value)[:length].ljust(length)
    for i, ch in enumerate(value):
        if start + i < RECORD_LEN:
            buf[start + i] = ch


def _place_r(buf: list, value, start: int, length: int) -> None:
    value = str(value)[:length].zfill(length)
    for i, ch in enumerate(value):
        if start + i < RECORD_LEN:
            buf[start + i] = ch


def _build_header(business_date_yymmdd: str) -> str:
    """File header — one line per file, 2052 chars.
    Confirmed from real sample prefix.
    """
    # The real header structure is processor-specific and mostly static.
    # We reconstruct a plausible header with the business date substituted.
    prefix = (
        f"002052000072THA{business_date_yymmdd}00505287PRO260        PTLF"
        "                               "
        f"000076FH {business_date_yymmdd}00505287PRO260PTLF    "
        f"TANGO.$IDFCDS.TGPTIDFC.PO{business_date_yymmdd}    D121"
    )
    return prefix.ljust(2052)


def _build_record(txn: Transaction, pos_type: str = "PHYSICAL") -> str:
    buf = list(_TEMPLATE)

    # Acquiring / Issuing inst code (pos 65, 69) — 4 chars each
    acq = _ACQ_INST_MAP.get(txn.network.upper(), "IDFE")
    _place(buf, acq, 65, 4)
    _place(buf, acq, 69, 4)

    # Retailer/Terminal ID (pos 44, 14 chars)
    _place(buf, txn.terminal_id[:14], 44, 14)

    # Merchant ref (pos 81, 24 chars) — construct from terminal + padding
    merch_ref = f"MERCH{txn.terminal_id[:7]:<7}{'0'*12}"[:24]
    _place(buf, merch_ref, 81, 24)

    # MessageType (pos 197, 4 chars). Reversal (CHANGE 8): 0420 for reversal records.
    msg_type = "0420" if txn.is_reversal else "0210"
    _place(buf, msg_type, 197, 4)

    # Flag at pos 201 (4 chars) — template has "0073"/"0075". Keep "0075".
    _place(buf, "0075", 201, 4)

    # SEQ_NO (pos 204, 12 chars) — PRIMARY JOIN KEY
    _place(buf, txn.seq_no[:12].zfill(12), 204, 12)

    # Date (pos 264, 6 chars) YYMMDD
    _place(buf, txn.date_yymmdd[:6], 264, 6)

    # Also repeated at 276, 282, 288 in template — update those too
    _place(buf, txn.date_yymmdd[:6], 276, 6)
    _place(buf, txn.date_yymmdd[:6], 282, 6)
    _place(buf, txn.date_yymmdd[:6], 288, 6)

    # Time (pos 270, 8 chars) HHMMSSCC (pad with "00" centiseconds)
    time_cc = (txn.time_hhmmss[:6] + "00").ljust(8, "0")
    _place(buf, time_cc, 270, 8)

    # Merchant Name (pos 308, 25 chars)
    # Derive from terminal_id or use a generic name
    merch_name = f"MERCH {txn.terminal_id[:10]}".ljust(25)[:25]
    _place(buf, merch_name, 308, 25)

    # Merchant City (pos 333, 13 chars) — first 13 of merch name
    _place(buf, merch_name[:13], 333, 13)

    # Amount (pos 384, 12 chars) — zero-padded paise
    _place_r(buf, txn.amount, 384, 12)

    # Card BIN (pos 427, 4 chars) — first 4 of PAN
    _place(buf, txn.pan[:4], 427, 4)

    # Network text (pos 431, 4 chars)
    net_text = _NETWORK_TEXT_MAP.get(txn.network.upper(), "XXXX")
    _place(buf, net_text, 431, 4)

    # UNCONFIRMED — pos_type would affect PANEntryMode but position unknown.
    # Leaving template value. Flag: PANEntryMode position unconfirmed.
    _ = pos_type

    return "".join(buf)


def generate(
    transactions: List[Transaction],
    config: dict,
    role: str,
    output_path: str,
    pos_type: str = "PHYSICAL",
) -> int:
    """Write PTLF processor-specific fixed-width file. Returns count of records written."""
    _ = config, role   # retained for signature compatibility; not used in record
    written = 0

    # Determine business date from first in-switch txn (for header)
    bdate = "260101"
    for t in transactions:
        if t.in_switch:
            bdate = t.date_yymmdd[:6]
            break

    with open(output_path, "w", encoding="utf-8") as f:
        # File header (one line, 2052 chars)
        f.write(_build_header(bdate) + "\n")

        for txn in transactions:
            if not txn.in_switch:
                continue
            record = _build_record(txn, pos_type)
            assert len(record) == RECORD_LEN, f"PTLF record length {len(record)} != {RECORD_LEN}"
            f.write(record + "\n")
            written += 1

    return written
