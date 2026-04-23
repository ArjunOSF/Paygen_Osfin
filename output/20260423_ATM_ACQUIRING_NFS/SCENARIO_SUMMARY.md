# Recon Test Scenario Summary

**Generated:** 2026-04-23 12:11:06  
**Business Date:** 20260423  
**Channel:** ATM  
**Role:** ACQUIRING  
**Network:** NFS  

## Layer Configuration

```
Switch=PASS | CBS=PASS | Network=PASS | EJ=PASS
```

## Transaction Counts

| Layer | Transactions | Notes |
|-------|-------------|-------|
| Total generated | 5 | |
| Switch (TLF/PTLF) | 5 | present |
| CBS (ATM_C) | 5 | present |
| Network (NFS) | 5 | present |
| EJ | 5 | 5 success, 0 fail |

## Files Generated

| File | Path | Records |
|------|------|---------|
| TLF (Switch ATM) | `tlf.txt` | 5 |
| ATM_C (CBS) | `atm_c.txt` | 5 |
| EJ (Hyosung) | `ej.csv` | 5 |
| NFS ACQ | `nfs_acq.txt` | 5 |

## Join Key Reference

```
PRIMARY KEY (links Switch → CBS → EJ):
  TLF/PTLF.SequenceNumber  =  ATM_C.SEQ_NO  =  EJ.TRXN_NO
  Sample: 372014156125

SECONDARY KEY (links CBS → Network):
  ATM_C.RR_NO  =  MC_T112.DE37  =  Visa_EPIN.ARN[embedded]  =  RuPay.nRRN
  Sample: 499975811595

AMOUNT (must match across all files, in paise):
  Sample: 177200 paise
```

## Recon Expected Outcome

**RECONCILED** — All layers match. No breaks expected.
