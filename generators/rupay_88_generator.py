"""
rupay_88_generator.py
=====================
RuPay 88 clearing file generator (XML).
Source: real sample 883IDFC75100012600100.xml.

Filename pattern: 88{version}{institution_code}{date_seq}.xml
  Example: 883IDFC75100012600100.xml
            ^^ version 3
              ^^^^^^^^^^^ institution code (IDFC7510001)
                         ^^^^^^^ date+sequence (2600100)

XML structure (verified from real sample):

<File>
  <Hdr>
    <nMTI>1644</nMTI>
    <nFunCd>670</nFunCd>
    <nRecNum>00000001</nRecNum>
    <nDtTmFlGen>0101161158</nDtTmFlGen>          MMDDHHMMSS
    <nMemInstCd>IDFC7510001</nMemInstCd>
    <nUnFlNm>883IDFC75100012600100</nUnFlNm>
    <nDtSet>260101</nDtSet>                      YYMMDD settlement date
    <nProdCd>POS01</nProdCd>                     POS / ATM01 etc.
    <nSetBIN>IDFC01</nSetBIN>
    <nFlCatg>P</nFlCatg>                         P=POS, A=ATM
    <nVerNum>01.00</nVerNum>
  </Hdr>
  <TxnBlock>
    <Txn>                                        ← repeated per transaction
      <nMTI>0100</nMTI>
      <nFunCd>100</nFunCd>
      <nRecNum>00000002</nRecNum>                seq increments per Txn
      <nDtTmLcTxn>260101110342</nDtTmLcTxn>      YYMMDDHHMMSS
      <nPAN>6081160307941287</nPAN>              RuPay PAN (starts 6/65/81/82/508)
      <nRRN>600111089484</nRRN>                  KEY JOIN
      <nAcqInstCd>720212</nAcqInstCd>
      <nApprvlCd>707903</nApprvlCd>
      <nCrdAcptTrmId>PR514760</nCrdAcptTrmId>
      <nAmtTxn>10000</nAmtTxn>                   amount in minor units (paise)
      <nCcyCdTxn>356</nCcyCdTxn>
      <nTxnOrgInstCd>RATN1760004</nTxnOrgInstCd>
      <nTxnDesInstCd>IDFC7510001</nTxnDesInstCd>
      <nDtSet>260101</nDtSet>
      <nAmtSet>0</nAmtSet>
      <nCcyCdSet>356</nCcyCdSet>
      <nConvRtSet>00000001</nConvRtSet>
      <nRGCSRcvdDt>260101</nRGCSRcvdDt>
      <nATD> </nATD>
      <Fee>
        <nFeeAmt>15</nFeeAmt>
        <nFeeCcy>356</nFeeCcy>
        <nFeeDCInd>D</nFeeDCInd>                 D=debit fee, C=credit
        <nFeeTpCd>3530</nFeeTpCd>                interchange fee type
      </Fee>
    </Txn>
    ...
  </TxnBlock>
</File>

RuPay PAN ranges: 6, 60, 65, 81, 82, 508 prefixes (we use "6" for simplicity).
"""

from __future__ import annotations
import argparse, csv, json, os, random, re, sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple


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


# Acquiring institution codes seen in real sample / typical
_ACQ_INSTS = ["RATN1760004", "HDFC1234567", "ICIC8901234", "AXIS5678901",
              "SBIN0000001", "KKBK0000456", "PUNB0123456", "BARB0789012"]


@dataclass
class RupayTxn:
    rec_num: int
    pan: str
    rrn: str           # 12-digit
    amount_paise: int
    business_date: str # YYYYMMDD
    txn_time: str      # HHMMSS
    acq_inst_cd: str
    approval_code: str
    terminal_id: str
    fee_amount: int
    fee_dc_ind: str    # D or C
    is_reversal: bool = False


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _make_txn(idx: int, business_date: str, rng: random.Random,
              rrn_start: int) -> RupayTxn:
    pan = _luhn_complete("6", 16)
    rrn = str(rrn_start + idx).zfill(12)
    amount = rng.randint(50_000, 5_000_000)   # Rs 500 to 50,000
    hh = rng.randint(0, 23); mm = rng.randint(0, 59); ss = rng.randint(0, 59)
    time_str = f"{hh:02d}{mm:02d}{ss:02d}"
    fee = max(1, int(amount * 0.0015))         # ~0.15% interchange

    return RupayTxn(
        rec_num=idx + 2,                       # +2 because Hdr is rec 1
        pan=pan, rrn=rrn, amount_paise=amount,
        business_date=business_date, txn_time=time_str,
        acq_inst_cd=rng.choice(_ACQ_INSTS),
        approval_code=str(rng.randint(100000, 999999)),
        terminal_id=f"PR{rng.randint(100000, 999999):06d}",
        fee_amount=fee, fee_dc_ind="D",
    )


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def _yymmdd(yyyymmdd: str) -> str:
    return yyyymmdd[2:]


def _build_header(num_txns: int, business_date: str,
                  member_inst_cd: str, file_category: str,
                  file_seq: str, version: str, file_name: str) -> str:
    """File header block — first record."""
    yymmdd = _yymmdd(business_date)
    now = datetime.now()
    gen_mmddhhmmss = now.strftime("%m%d%H%M%S")
    prod_cd = "POS01" if file_category == "P" else "ATM01"
    set_bin = member_inst_cd[:6]    # first 6 of member as settlement BIN
    return f"""  <Hdr>
    <nMTI>1644</nMTI>
    <nFunCd>670</nFunCd>
    <nRecNum>00000001</nRecNum>
    <nDtTmFlGen>{gen_mmddhhmmss}</nDtTmFlGen>
    <nMemInstCd>{member_inst_cd}</nMemInstCd>
    <nUnFlNm>{file_name}</nUnFlNm>
    <nDtSet>{yymmdd}</nDtSet>
    <nProdCd>{prod_cd}</nProdCd>
    <nSetBIN>{set_bin}</nSetBIN>
    <nFlCatg>{file_category}</nFlCatg>
    <nVerNum>{version}</nVerNum>
  </Hdr>"""


def _build_txn(t: RupayTxn, member_inst_cd: str) -> str:
    yymmdd = _yymmdd(t.business_date)
    dttm = yymmdd + t.txn_time
    return f"""    <Txn>
      <nMTI>0100</nMTI>
      <nFunCd>100</nFunCd>
      <nRecNum>{t.rec_num:08d}</nRecNum>
      <nDtTmLcTxn>{dttm}</nDtTmLcTxn>
      <nPAN>{t.pan}</nPAN>
      <nRRN>{t.rrn}</nRRN>
      <nAcqInstCd>{t.acq_inst_cd}</nAcqInstCd>
      <nApprvlCd>{t.approval_code}</nApprvlCd>
      <nCrdAcptTrmId>{t.terminal_id}</nCrdAcptTrmId>
      <nAmtTxn>{t.amount_paise}</nAmtTxn>
      <nCcyCdTxn>356</nCcyCdTxn>
      <nTxnOrgInstCd>{t.acq_inst_cd}</nTxnOrgInstCd>
      <nTxnDesInstCd>{member_inst_cd}</nTxnDesInstCd>
      <nDtSet>{yymmdd}</nDtSet>
      <nAmtSet>0</nAmtSet>
      <nCcyCdSet>356</nCcyCdSet>
      <nConvRtSet>00000001</nConvRtSet>
      <nRGCSRcvdDt>{yymmdd}</nRGCSRcvdDt>
      <nATD> </nATD>
      <Fee>
        <nFeeAmt>{t.fee_amount}</nFeeAmt>
        <nFeeCcy>356</nFeeCcy>
        <nFeeDCInd>{t.fee_dc_ind}</nFeeDCInd>
        <nFeeTpCd>3530</nFeeTpCd>
      </Fee>
    </Txn>"""


def generate(num_txns: int, business_date: str,
             member_inst_cd: str = "IDFC7510001",
             file_category: str = "P",
             version: str = "01.00",
             file_seq: str = "00100",
             seed: Optional[int] = None) -> Tuple[List[RupayTxn], str, str]:
    """Returns (txns, xml_content, file_name)."""
    rng = random.Random(seed if seed is not None else int(datetime.now().timestamp()))
    rrn_start = rng.randint(600_000_000_000, 699_999_999_999)

    txns = [_make_txn(i, business_date, rng, rrn_start) for i in range(num_txns)]

    yymmdd = _yymmdd(business_date)
    file_name = f"883{member_inst_cd}{yymmdd}{file_seq}"

    parts: List[str] = ["<File>"]
    parts.append(_build_header(num_txns, business_date, member_inst_cd,
                               file_category, file_seq, version, file_name))
    parts.append("  <TxnBlock>")
    for t in txns:
        parts.append(_build_txn(t, member_inst_cd))
    parts.append("  </TxnBlock>")
    parts.append("</File>")

    return txns, "\n".join(parts) + "\n", file_name


def write_outputs(txns: List[RupayTxn], xml: str, file_name: str,
                  out_path: str, business_date: str) -> None:
    base, _ = os.path.splitext(out_path)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(xml)

    with open(base + "_master_table.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rec_num", "rrn", "pan_masked", "amount_paise", "fee_amount",
                    "txn_time", "acq_inst_cd", "approval_code", "terminal_id",
                    "is_reversal"])
        for t in txns:
            pan_m = t.pan[:6] + "X"*6 + t.pan[-4:]
            w.writerow([t.rec_num, t.rrn, pan_m, t.amount_paise, t.fee_amount,
                        t.txn_time, t.acq_inst_cd, t.approval_code, t.terminal_id,
                        t.is_reversal])

    totals = {
        "business_date": business_date,
        "file_name": file_name,
        "num_txns": len(txns),
        "total_amount_paise": sum(t.amount_paise for t in txns),
        "total_fees_paise": sum(t.fee_amount for t in txns),
        "currency": "INR (356)",
    }
    with open(base + "_expected_totals.json", "w", encoding="utf-8") as f:
        json.dump(totals, f, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="RuPay 88 clearing file generator (XML)")
    p.add_argument("--num-txns", type=int, default=10)
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--member-inst-cd", default="IDFC7510001")
    p.add_argument("--category", choices=["P", "A"], default="P",
                   help="P=POS, A=ATM")
    p.add_argument("--version", default="01.00")
    p.add_argument("--file-seq", default="00100")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--random", action="store_true")
    p.add_argument("--output", default="rupay_88.xml")
    args = p.parse_args(argv)

    try: bdate = _normalize_date(args.date)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr); return 2

    txns, xml, fname = generate(args.num_txns, bdate, args.member_inst_cd,
                                 args.category, args.version, args.file_seq, args.seed)
    write_outputs(txns, xml, fname, args.output, bdate)

    print(f"  wrote {len(txns)} <Txn> blocks → {args.output}")
    print(f"  filename suggestion: {fname}.xml")
    print(f"  master table → {os.path.splitext(args.output)[0]}_master_table.csv")
    print(f"  totals       → {os.path.splitext(args.output)[0]}_expected_totals.json")

    # Validate XML well-formedness
    try:
        import xml.etree.ElementTree as ET
        ET.fromstring(xml)
        print(f"  validate     → XML is well-formed [OK]")
    except ET.ParseError as e:
        print(f"  validate     → XML PARSE ERROR: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
