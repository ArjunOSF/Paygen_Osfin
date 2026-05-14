"""NFS recon orchestrator.

Builds one shared list of transactions (same RRN + amount + date + ATM) then
emits ISSRPIDF + TLF + CBS + FSS GL OUT + NTSL with cross-file consistency.
Used by paygen_nl.py for NFS prompts so the resulting zip is fully recon-able.

18-case matrix (CBS in {1, NULL} × Switch in {1, 0, NULL} × NPCI in {1, 0, NULL}).
"""
import argparse, csv, hashlib, json, os, random, sys, zipfile
from datetime import datetime, timedelta
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_REPO_DIR))
from issrpidf_generator import (build_record, _build_values, COMMON_FIELDS,
                                  ISSUER_FIELDS, ACQUIRER_FIELDS,
                                  ISSUER_LEN, ACQUIRER_LEN, ATM_PREFIXES,
                                  write_verifreversal)
from tlf_generator import Txn as TlfTxn, build_line as tlf_build_line, build_header as tlf_build_header, LINE_LEN as TLF_LINE_LEN

# Defaults — argparse overrides these when run as CLI
SEED = 42
DATE = "20260401"
REV_DATE = "20260402"
N = 1000
ROLE_LABEL = "issuer"   # 'issuer' or 'acquirer'
OUT_ROOT = Path("/tmp/nfs_matrix_out")
DOWNLOADS = Path.home() / "Downloads"

# ------------------------------------------------------------- case matrices
# 18-case matrix (NFS-OSG aligned): CBS in {1, NULL} × Switch in {1, 0, NULL} ×
# NPCI in {1, 0, NULL}. CBS=0 excluded — auth is real-time, no failed CBS.
CASES_STD = [
    (1,    1,    1),     # 1  CLOSED — all matched
    (1,    1,    0),     # 2  Refund to customer (GL→CASA)
    (1,    1,    None),  # 3  Refund to customer (GL→CASA)
    (1,    0,    1),     # 4  Force match — CLOSED
    (1,    0,    0),     # 5  Refund to customer
    (1,    0,    None),  # 6  Refund to customer
    (1,    None, 1),     # 7  Force match — CLOSED
    (1,    None, 0),     # 8  Refund to customer
    (1,    None, None),  # 9  Refund to customer
    (None, 1,    1),     # 10 Recovery from customer (CASA→GL)
    (None, 1,    0),     # 11 No action — CLOSED
    (None, 1,    None),  # 12 No action — CLOSED
    (None, 0,    1),     # 13 Recovery from customer
    (None, 0,    0),     # 14 No action — CLOSED
    (None, 0,    None),  # 15 No action — CLOSED
    (None, None, 1),     # 16 Recovery from customer
    (None, None, 0),     # 17 No action — CLOSED
    (None, None, None),  # 18 No action — CLOSED
]
# ZIP 2 — 16 cases — (cbs, switch, npci); 1000 split as 63x8 + 62x8 = 1000
CASES_ICCW = [
    (1, 1, 1),
    (1, 1, 0),
    (1, 0, 1),
    (None, 1, 1),
    (1, 0, 0),
    (None, 0, 1),
    (None, 1, 0),
    (-1, -1, 1),
    (-1, -1, 0),
    (1, None, 1),
    (1, None, 0),
    (None, None, 1),
    (1, 1, None),
    (1, 0, None),
    (None, 1, None),
    (1, None, None),
]

def split_counts(total, n_cases):
    base = total // n_cases
    rem = total - base * n_cases
    return [base + (1 if i < rem else 0) for i in range(n_cases)]


# ------------------------------------------------------------- core records
def build_rows(n, mcc, cases):
    rng = random.Random(SEED)
    nfs_start = rng.randint(600_000_000_000, 699_999_999_999)
    upi_start = rng.randint(900_000_000_000, 999_999_999_999)
    counts = split_counts(n, len(cases))
    rows = []
    idx = 0
    for ci, ((cbs, sw, npci), cnt) in enumerate(zip(cases, counts), start=1):
        for _ in range(cnt):
            atm_pfx = rng.choice(ATM_PREFIXES)
            amount = rng.randint(5, 500) * 10000   # ₹500–50000
            mobile = f"91{rng.randint(7000000000, 9999999999)}"
            pan = "554534" + "".join(str(rng.randint(0,9)) for _ in range(7)) + str(rng.randint(0,999)).zfill(3)
            pan_masked = f"{pan[:6]}*******{pan[-3:]}"
            atm_id = f"{atm_pfx.strip()}{rng.randint(10000000, 99999999):08d}"
            hh = rng.randint(0, 11); mm = rng.randint(0, 59); ss = rng.randint(0, 59)
            ampm = rng.choice(["AM", "PM"])
            rows.append({
                "case": ci, "cbs": cbs, "sw": sw, "npci": npci,
                "nfs_rrn": str(nfs_start + idx).zfill(12),
                "upi_rrn": str(upi_start + idx).zfill(12) if mcc == "6013" else "",
                "amount_paise": amount,
                "atm_prefix": atm_pfx, "atm_id": atm_id,
                "pan": pan, "pan_masked": pan_masked,
                "mobile": mobile, "hh": hh, "mm": mm, "ss": ss, "ampm": ampm,
                "mcc": mcc,
            })
            idx += 1
    return rows


# ------------------------------------------------------------- file builders
def write_issrpidf(rows, out, mcc, role="issuer"):
    """Emit ISSRPIDF using new NPCI spec-based builder. role='issuer' → 407-char,
    'acquirer' → 274-char. NPCI=NULL omits record. NPCI=0 emits with declined resp.
    NPCI=-1 (reversal) is encoded by setting resp=26 on the original record."""
    import random
    iss_fields = COMMON_FIELDS + (ISSUER_FIELDS if role == "issuer" else ACQUIRER_FIELDS)
    total_len = ISSUER_LEN if role == "issuer" else ACQUIRER_LEN
    rng = random.Random(SEED + (101 if role == "issuer" else 202))
    lines = []
    for r in rows:
        if r["npci"] is None:
            continue
        # Map npci flag to spec resp_code
        if r["npci"] == 1:
            npci_flag = 1
        elif r["npci"] == 0:
            npci_flag = 0
        else:  # -1 = reversal, leave amount populated, resp=26 below
            npci_flag = 1
        vals = _build_values(r["nfs_rrn"], r["pan"], r["amount_paise"],
                              DATE, rng, npci_flag, role)
        # Override MCC for ICCW
        vals["mcc"] = mcc
        if r["npci"] == -1:
            vals["resp_code"] = "26"
        line = build_record(vals, iss_fields, total_len)
        assert len(line) == total_len
        lines.append(line)
    with open(out, "w") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


def write_cbs(rows, out, mcc):
    """Pipe-delimited CBS — FH header + records. Skip if cbs=None. Reversal → 2 rows."""
    def fmt(r, dr_cr, tran_type, rrn, date_ddmm, hhmmss):
        return "|".join([
            "21561", f"{r['amount_paise']/100:.2f}", dr_cr,
            r["pan_masked"], rrn,
            str(rng_stan + r["case"]).zfill(14),
            f"{date_ddmm[:2]}-{date_ddmm[2:4]}-{date_ddmm[4:8]}",
            hhmmss, tran_type, "NFS", "00000000",
            str(885748000 + r["case"]).zfill(9),
            f"{date_ddmm[:2]}-{date_ddmm[2:4]}-{date_ddmm[4:8]}",
            "0", "0", "0", "I",
        ])
    rng_stan = 100000
    date_ddmm = DATE[6:8] + DATE[4:6] + DATE[:4]
    rev_ddmm = REV_DATE[6:8] + REV_DATE[4:6] + REV_DATE[:4]
    out_lines = [f"FH{DATE}000001"]
    for r in rows:
        if r["cbs"] is None:
            continue
        hhmmss = f"{r['hh']:02d}:{r['mm']:02d}:{r['ss']:02d}"
        if r["cbs"] == 1:
            out_lines.append(fmt(r, "D", "CWDR", r["nfs_rrn"], date_ddmm, hhmmss))
        elif r["cbs"] == -1:
            out_lines.append(fmt(r, "D", "CWDR", r["nfs_rrn"], date_ddmm, hhmmss))
            out_lines.append(fmt(r, "C", "CWRR", r["nfs_rrn"], rev_ddmm, hhmmss))
    with open(out, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    return len(out_lines) - 1


def write_fssglout(rows, out, mcc):
    """H+D — header total = sum of D amounts."""
    GL_ACCT = "0000098133102016"
    GL_NAME = "NFS ISSUING PAYABLE-ATM/MATM BGL"
    date_ddmm = DATE[6:8] + DATE[4:6] + DATE[:4]
    rev_ddmm = REV_DATE[6:8] + REV_DATE[4:6] + REV_DATE[:4]
    details = []
    total = 0
    for r in rows:
        if r["cbs"] is None and r["npci"] is None and r["sw"] is None:
            continue   # nothing posts to GL
        # post original
        amt = r["amount_paise"]
        if r["cbs"] is not None:
            dr_cr = "DR" if r["cbs"] == 1 else "CR"
            d = "|".join([
                "D", GL_ACCT, "10201", "0229136", date_ddmm, date_ddmm, dr_cr,
                f"{amt/100:017.2f}", str(29500465 + len(details)).zfill(9),
                f"REF/ATM-NFS/{r['atm_id']}/{r['nfs_rrn']}/{date_ddmm[:6]}",
                "", "", "", "NFS", "CWDR" if r["cbs"] == 1 else "CWRR",
                r["atm_id"], r["nfs_rrn"],
                f"{r['hh']:02d}{r['mm']:02d}{r['ss']:02d}",
                "", "", "", "", "",
            ])
            details.append(d)
            total += amt
            if r["cbs"] == -1:
                # reversal entry
                d2 = d.replace(date_ddmm, rev_ddmm, 1).replace("DR", "CR", 1)
                details.append(d2)
                total += amt
    header = f"H|{date_ddmm}|{GL_ACCT}|{GL_NAME}|{total/100:017.2f}|CR"
    with open(out, "w") as f:
        f.write(header + "\n")
        for d in details:
            f.write(d + "\n")
    return len(details)


def write_tlf(rows, out, mcc):
    """Use real tlf_generator Txn + build_line: 3462-char data lines, 4 txns/line, 3610-char header."""
    txns = []
    yymmdd = DATE[2:8]
    yymmdd_rev = REV_DATE[2:8]
    for r in rows:
        if r["sw"] is None:
            continue
        is_rev = r["sw"] == -1
        msg_type = "0420" if is_rev else "0210"
        resp = "000" if r["sw"] == 1 else "091"
        time6 = f"{r['hh']:02d}{r['mm']:02d}{r['ss']:02d}"
        pan_padded = (r["pan"]).ljust(19)
        seq = r["nfs_rrn"]
        terminal = r["atm_id"].ljust(16)[:16]
        t = TlfTxn(
            pan=pan_padded, seq_num=seq, tran_date=yymmdd, tran_time=time6,
            amount_paise=r["amount_paise"], mcc="6011",
            merchant_name="NFS WITHDRAWAL"[:25],
            merchant_city="MUMBAI"[:13], merchant_state="MH",
            terminal_id=terminal, msg_type=msg_type, resp_code=resp,
            rvsl_code="01" if is_rev else "00",
            currency="356", acq_inst_id="00000001234", rcv_inst_id="00000005678",
            rrn=seq, auth_resp_id=str(50000 + (r["case"] * 100))[:6],
            is_reversal=is_rev, test_case="random",
        )
        txns.append(t)
        if r["sw"] == -1:
            # add reversal record
            t_rev = TlfTxn(**{**t.__dict__, "msg_type": "0420", "rvsl_code": "01",
                              "is_reversal": True, "tran_date": yymmdd_rev})
            txns.append(t_rev)
    out_lines = [tlf_build_header(DATE)]
    for i in range(0, len(txns), 4):
        chunk = txns[i:i+4]
        while len(chunk) < 4:
            chunk.append(None)
        out_lines.append(tlf_build_line(chunk))
    # TLF requires CRLF line endings (real file uses \r\n) for injection-board parsing
    with open(out, "wb") as f:
        for l in out_lines:
            f.write((l + "\r\n").encode("utf-8"))
    return len(out_lines)


def write_upi_switch(rows, out):
    """SHA + FHIM + records + FT. UPI_RRN is 12 numeric per real sample.
    PROMPT 11 FIX 1: ~10% retry — dup NFS_RRN with distinct UPI_RRN, same amount."""
    body = [f"FHIM{DATE}"]
    rng = random.Random(SEED + 99)
    rendered = [r for r in rows if r["sw"] is not None]
    n_retry = max(1, int(len(rendered) * 0.10))
    retry_idx = set(rng.sample(range(1, len(rendered)), min(n_retry, len(rendered)-1))) if rendered else set()
    upi_seq = 100
    for pos, r in enumerate(rendered):
        if r["sw"] is None:
            continue
        if pos in retry_idx:
            prev = rendered[pos-1]
            r = {**r, "nfs_rrn": prev["nfs_rrn"], "amount_paise": prev["amount_paise"],
                 "upi_rrn": str(900_000_000_000 + upi_seq).zfill(12)}
            upi_seq += 1
        # original loop body resumes below
        flag_cf = "F" if r["sw"] == 0 else "C"
        if r["sw"] == 1:
            tt = "DEBIT"
        elif r["sw"] == -1:
            tt = "DEBIT-AUTOREVERSAL"
        else:
            tt = "DEBIT"   # flag F marks failure
        upi_txn_id = "".join(str(rng.randint(0, 9)) for _ in range(18))
        rec = "|".join([
            f"IPM{'0' * 20}{r['nfs_rrn']}",
            r["upi_rrn"], r["nfs_rrn"], upi_txn_id,
            f"{DATE[6:8]}/{DATE[4:6]}/{DATE[:4]} {r['hh']:02d}:{r['mm']:02d}:{r['ss']:02d} {r['ampm']}",
            r["mobile"], "Customer Name", "iccwpaysis@india1",
            "INDIA1 PAYMENTS LIMITED",
            f"{r['amount_paise']:012d}",
            str(rng.randint(10**9, 10**10 - 1)),
            "95821102016", str(rng.randint(10**6, 10**7 - 1)),
            flag_cf, "I", tt, "FR", "Y", "MOB",
        ])
        body.append(rec)
    body.append(f"FT{len(body)-1:09d}")
    body_str = "\n".join(body) + "\n"
    sha = hashlib.sha1(body_str.encode()).hexdigest()
    with open(out, "w") as f:
        f.write(sha + "\n")
        f.write(body_str)
    return len(body) - 2


def write_ntsl(rows, out, mcc):
    """xlsx with one Issuer WDL block summarising the 1000 txns."""
    try:
        from openpyxl import Workbook
    except ImportError:
        os.system("pip3 install -q openpyxl >/dev/null 2>&1")
        from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "NTSL"
    ws.append(["Description", "No of Txns", "Debit", "Credit"])
    succ = sum(1 for r in rows if r["cbs"] == 1)
    decl = sum(1 for r in rows if r["cbs"] == 0 or r["cbs"] is None)
    rev  = sum(1 for r in rows if r["cbs"] == -1)
    total_amt = sum(r["amount_paise"] for r in rows if r["cbs"] == 1) / 100
    suffix = " (ICCW-ATM)" if mcc == "6013" else ""
    ws.append([f"Issuer WDL Transaction Amount{suffix}", succ, total_amt, 0])
    ws.append([f"Issuer WDL Approved Fee{suffix}", succ, 0, round(succ * 10, 2)])
    ws.append([f"Issuer WDL Approved Fee - GST{suffix}", succ, 0, round(succ * 10 * 0.18, 2)])
    ws.append([f"Issuer WDL Declined{suffix}", decl, 0, 0])
    ws.append([f"Issuer WDL Reversal{suffix}", rev, 0, 0])
    ws.append(["Settlement Amount", "", round(total_amt - succ * 10 * 1.18, 2), 0])
    wb.save(out)


def write_master(rows, out, flow):
    cols = ["case_num","nfs_rrn","upi_rrn","pan_masked","amount","date","mcc",
            "cbs_flag","switch_flag","npci_flag","action","closable","voucher_type"]
    ACTIONS = {  # (cbs, sw, npci) → (action, closable, voucher)
        (1,1,1):("Matched — CLOSED","TRUE",""),
        (1,1,0):("Refund to customer","FALSE","GL TO CASA"),
        (1,0,1):("Force Match — CLOSED","TRUE",""),
        (-1,-1,1):("Recovery from customer","FALSE","CASA TO GL"),
        (1,0,0):("Refund to customer","FALSE","GL TO CASA"),
        (-1,-1,0):("CLOSED — Declined","TRUE",""),
        (None,1,1):("Recovery from customer","FALSE","CASA TO GL"),
        (None,1,0):("CLOSED — Declined","TRUE",""),
        (None,0,1):("Recovery from customer","FALSE","CASA TO GL"),
        (None,0,0):("CLOSED — Declined","TRUE",""),
        (1,None,1):("Matched","TRUE",""),
        (1,None,0):("Refund to Customer","FALSE","GL TO CASA"),
        (None,None,1):("Recovery From Customer","FALSE","CASA TO GL"),
        (1,1,None):("Refund to Customer","FALSE","GL TO CASA"),
        (1,0,None):("Refund to Customer","FALSE","GL TO CASA"),
        (None,1,None):("No Action","TRUE",""),
        (1,None,None):("Refund to Customer","FALSE","GL TO CASA"),
    }
    with open(out, "w", newline="") as f:
        w = csv.writer(f, delimiter="|")
        w.writerow(cols)
        for r in rows:
            key = (r["cbs"], r["sw"], r["npci"])
            act, closable, vt = ACTIONS.get(key, ("?", "?", ""))
            w.writerow([r["case"], r["nfs_rrn"], r["upi_rrn"] or "",
                        r["pan_masked"], f"{r['amount_paise']/100:.2f}", DATE,
                        r["mcc"],
                        "" if r["cbs"] is None else r["cbs"],
                        "" if r["sw"]  is None else r["sw"],
                        "" if r["npci"] is None else r["npci"],
                        act, closable, vt])


# ------------------------------------------------------------- run both zips
def run_zip1():
    rows = build_rows(N, "6011", CASES_STD)
    d = OUT_ROOT / "zip1"; d.mkdir(exist_ok=True)
    base = f"{ROLE_LABEL}_{N}txns_18cases"
    n_iss = write_issrpidf(rows, d / f"issrpidf_nfs_std_{base}.txt", "6011", role=ROLE_LABEL)
    n_tlf = write_tlf(rows, d / f"tlf_nfs_std_{base}.txt", "6011")
    n_cbs = write_cbs(rows, d / f"cbs_nfs_std_{base}.txt", "6011")
    n_gl  = write_fssglout(rows, d / f"fss_gl_out_nfs_std_{base}.txt", "6011")
    write_ntsl(rows, d / f"ntsl_nfs_std_{base}.xlsx", "6011")
    write_master(rows, d / f"master_table_nfs_std_{base}.csv", "STD")
    print(f"  ZIP1: ISSRPIDF={n_iss} TLF={n_tlf}lines CBS={n_cbs} GL={n_gl} NTSL+master")
    return d, base

def run_zip2():
    rows = build_rows(N, "6013", CASES_ICCW)
    d = OUT_ROOT / "zip2"; d.mkdir(exist_ok=True)
    base = "1000txns_16cases"
    n_iss = write_issrpidf(rows, d / f"issrpidf_nfs_iccw_{base}.txt", "6013")
    n_upi = write_upi_switch(rows, d / f"upi_switch_iccw_{base}.txt")
    n_cbs = write_cbs(rows, d / f"cbs_nfs_iccw_{base}.txt", "6013")
    n_gl  = write_fssglout(rows, d / f"fss_gl_out_nfs_iccw_{base}.txt", "6013")
    write_ntsl(rows, d / f"ntsl_nfs_iccw_{base}.xlsx", "6013")
    write_master(rows, d / f"master_table_nfs_iccw_{base}.csv", "ICCW")
    print(f"  ZIP2: ISSRPIDF={n_iss} UPI_SWITCH={n_upi} CBS={n_cbs} GL={n_gl} NTSL+master")
    return d, base

def make_zip(src, name):
    z = DOWNLOADS / name
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in src.iterdir():
            zf.write(f, arcname=f.name)
    return z

def run_orchestrator(num_txns, role, date_yyyymmdd, seed=42, mcc="6011",
                       skip_iccw=True, out_dir=None, deliver_zip=True,
                       participant_id="IDF"):
    """Programmatic entry point. Returns (output_dir, zip_path | None)."""
    global SEED, DATE, REV_DATE, N, ROLE_LABEL, OUT_ROOT
    SEED = seed
    DATE = date_yyyymmdd
    REV_DATE = (datetime.strptime(date_yyyymmdd, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    N = num_txns
    ROLE_LABEL = role
    OUT_ROOT = Path(out_dir) if out_dir else Path(f"/tmp/nfs_orch_{role}_{num_txns}")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    rows = build_rows(N, mcc, CASES_STD)
    ddmmyy = f"{DATE[6:8]}{DATE[4:6]}{DATE[2:4]}"
    pid = participant_id
    d = OUT_ROOT

    # NPCI-spec named files
    iss_path = d / f"250ISSuerIDF{ddmmyy}.mIDF" if role == "issuer" else None
    acq_path = d / f"250ACQuirerIDF{ddmmyy}.mIDF" if role == "acquirer" else None
    write_issrpidf(rows, iss_path or acq_path, mcc, role=role)

    # Companion bank-side files (same RRN/amount/date as ISSRPIDF — shared rows)
    n_tlf = write_tlf(rows, d / "tlf_nfs.txt", mcc)
    n_cbs = write_cbs(rows, d / "cbs_nfs.txt", mcc)
    n_gl  = write_fssglout(rows, d / "fss_gl_out_nfs.txt", mcc)
    write_ntsl(rows, d / "ntsl_nfs.xlsx", mcc)
    write_master(rows, d / f"master_table_{pid}{ddmmyy}.csv", "STD")

    # VerifReversal xls for cases 2,3,5,6,8,9 (CBS=1 + NPCI != Success)
    # Reuse issrpidf_generator.write_verifreversal which expects master rows
    # in its own shape — translate ours
    vr_master = [
        {"case_num": r["case"], "rrn": r["nfs_rrn"], "pan": r["pan"],
         "amount_paise": r["amount_paise"], "date": DATE, "mcc": r["mcc"],
         "atm_id": r["atm_id"]}
        for r in rows
    ]
    write_verifreversal(vr_master, DATE, pid, str(d))

    # Inner NPCI deliverable zip per NFS-OSG Table 6
    inner = d / f"NFSRawdata{pid}{ddmmyy}.zip"
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in [iss_path, acq_path]:
            if p and p.exists():
                zf.write(p, arcname=p.name)

    print(f"  ISSRPIDF={role}({iss_path or acq_path}) | TLF={n_tlf}lines | CBS={n_cbs} | GL={n_gl}")

    if not deliver_zip:
        return d, None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    role_tag = "ISS" if role == "issuer" else "ACQ"
    z = DOWNLOADS / f"paygen_NFS_{role_tag}_ATM_{num_txns}txn_{date_yyyymmdd}_{ts}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in d.iterdir():
            if f.is_file():
                zf.write(f, arcname=f.name)
    print(f"\n📦 → {z}")
    return d, z


def main(argv=None):
    p = argparse.ArgumentParser(description="NFS recon orchestrator — 18-case matrix with cross-file consistency")
    p.add_argument("--num-txns", type=int, default=1000)
    p.add_argument("--role", choices=["issuer","acquirer"], default="issuer")
    p.add_argument("--date", default="20260401", help="YYYYMMDD")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--mcc", default="6011")
    p.add_argument("--participant-id", default="IDF")
    p.add_argument("--output-dir", default=None)
    p.add_argument("--no-zip", action="store_true")
    args = p.parse_args(argv)
    run_orchestrator(args.num_txns, args.role, args.date,
                      seed=args.seed, mcc=args.mcc,
                      participant_id=args.participant_id,
                      out_dir=args.output_dir,
                      deliver_zip=not args.no_zip)


if __name__ == "__main__":
    main()
