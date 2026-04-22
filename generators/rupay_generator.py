"""
rupay_generator.py
==================
NPCI RuPay XML clearing file generator.
Format: XML (knowledge repo section 7–8.3).

Structure:
  <File>
    <Hdr>   — one file header
    <TxnBlock>
      <Txn>  — one per transaction where in_network=True
    </TxnBlock>
    <Trl>   — one trailer with count + run total
  </File>

Key join fields (knowledge repo section 8.3):
  nRRN         → ATM_C.RR_NO  (12 chars)
  nPAN         → PAN across all files
  nApprvlCd   → approval code → ATM_C.STMT_NARRT
  nCrdAcptTrmId → terminal → ATM_C.STATION_ID
  nAmtTxn      → amount → ATM_C.TRAN_AMT (12 chars, paise)
  nActnCd      → "00"=approved (primary filter)

Validation rule (knowledge repo section 8.4):
  nTxnCnt = count of <Txn> records
  nRnTtlAmt = sum of nAmtTxn for nActnCd='00'
"""

import xml.etree.ElementTree as ET
from xml.dom import minidom
from typing import List
from data_generator import Transaction


def _make_txn_element(txn: Transaction, seq: int, file_catg: str) -> ET.Element:
    """Build a <Txn> element for one transaction."""
    t = ET.Element("Txn")
    ard = (txn.rrn + "00000000000").zfill(23)[:23]   # ARD = 23 chars

    # Processing code: "011000" for ATM withdrawal, "000000" for POS purchase
    proc_cd = "011000"   # both ATM and POS issuing use same code in RuPay

    ET.SubElement(t, "nSeq").text        = str(seq).zfill(6)
    ET.SubElement(t, "nMTI").text        = "1240"
    ET.SubElement(t, "nFunCd").text      = "200"
    ET.SubElement(t, "nPAN").text        = txn.pan
    ET.SubElement(t, "nARD").text        = ard
    ET.SubElement(t, "nRRN").text        = txn.rrn          # PRIMARY → ATM_C.RR_NO
    ET.SubElement(t, "nAcqInstCd").text  = "00000000000"
    ET.SubElement(t, "nApprvlCd").text   = txn.approval_code
    ET.SubElement(t, "nCrdAcptTrmId").text = txn.terminal_id[:8]  # 8 chars
    ET.SubElement(t, "nAmtTxn").text     = str(txn.amount).zfill(12)
    ET.SubElement(t, "nCcyCdTxn").text   = "356"             # INR
    ET.SubElement(t, "nProcCd").text     = proc_cd
    ET.SubElement(t, "nCrdAcpBussCd").text = txn.mcc
    ET.SubElement(t, "nActnCd").text     = "00"              # approved
    ET.SubElement(t, "nSetDCInd").text   = "D"               # debit for issuing
    ET.SubElement(t, "nDtSet").text      = txn.date_yymmdd
    ET.SubElement(t, "nAmtSet").text     = str(txn.amount).zfill(12)
    ET.SubElement(t, "nCrsBrdFlg").text  = "N"               # domestic
    return t


def generate(
    transactions: List[Transaction],
    config: dict,
    file_category: str,     # "ISS" | "ACQ"
    output_path: str,
) -> int:
    """Write RuPay XML file. Returns count of <Txn> records written."""
    member_id = config.get("rupay_member_id", "123456")
    net_txns  = [t for t in transactions if t.in_network]

    root = ET.Element("File")

    # Header
    hdr = ET.SubElement(root, "Hdr")
    date_str = net_txns[0].date_yymmdd if net_txns else "000000"
    ET.SubElement(hdr, "nMTI").text        = "1644"
    ET.SubElement(hdr, "nDtSet").text      = date_str
    ET.SubElement(hdr, "nMemInstCd").text  = member_id
    ET.SubElement(hdr, "nUnFlNm").text     = f"RUPAY_{file_category}_{date_str}_{member_id}"
    ET.SubElement(hdr, "nFlCatg").text     = file_category   # ISS or ACQ
    ET.SubElement(hdr, "nFlRejInd").text   = "0"             # not rejected — MUST check this first!
    ET.SubElement(hdr, "nFlRejRsnCd").text = ""

    # Transaction block
    txn_block = ET.SubElement(root, "TxnBlock")
    run_total = 0
    for i, txn in enumerate(net_txns):
        txn_block.append(_make_txn_element(txn, seq=i + 1, file_catg=file_category))
        run_total += txn.amount

    # Trailer — validate: nTxnCnt = len(net_txns), nRnTtlAmt = sum(nAmtTxn)
    trl = ET.SubElement(root, "Trl")
    ET.SubElement(trl, "nTxnCnt").text    = str(len(net_txns))
    ET.SubElement(trl, "nRnTtlAmt").text  = str(run_total).zfill(12)

    # Pretty-print XML
    raw_xml = ET.tostring(root, encoding="unicode")
    pretty  = minidom.parseString(raw_xml).toprettyxml(indent="  ")
    # Remove the <?xml...?> declaration added by toprettyxml (first line)
    lines = pretty.split("\n")
    if lines[0].startswith("<?xml"):
        lines = lines[1:]
    output = "\n".join(lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(output)

    return len(net_txns)
