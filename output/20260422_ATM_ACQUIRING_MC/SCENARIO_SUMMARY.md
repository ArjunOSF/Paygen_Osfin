# Recon Test Scenario Summary

**Generated:** 2026-04-22 18:35:23  
**Business Date:** 20260422  
**Channel:** ATM  
**Role:** ACQUIRING  
**Network:** MC  

## Layer Configuration

```
Switch=PASS | CBS=PASS | Network=PASS | EJ=PASS
```

## Transaction Counts

| Layer | Transactions | Notes |
|-------|-------------|-------|
| Total generated | 10 | |
| Switch (TLF/PTLF) | 10 | present |
| CBS (ATM_C) | 10 | present |
| Network (MC) | 10 | present |
| EJ | 10 | 10 success, 0 fail |

## Files Generated

| File | Path | Records |
|------|------|---------|
| TLF (Switch ATM) | `tlf.txt` | 10 |
| ATM_C (CBS) | `atm_c.txt` | 10 |
| EJ (Hyosung) | `ej.csv` | 10 |
| MC T112 | `t112.txt` | 10 |
| MC T140 | `t140.txt` | 10 |
| MC T464 | `t464.t464` | 10 |

## Join Key Reference

```
PRIMARY KEY (links Switch → CBS → EJ):
  TLF/PTLF.SequenceNumber  =  ATM_C.SEQ_NO  =  EJ.TRXN_NO
  Sample: 572685998206

SECONDARY KEY (links CBS → Network):
  ATM_C.RR_NO  =  MC_T112.DE37  =  Visa_EPIN.ARN[embedded]  =  RuPay.nRRN
  Sample: 273236868049

AMOUNT (must match across all files, in paise):
  Sample: 143400 paise
```

## Recon Expected Outcome

**RECONCILED** — All layers match. No breaks expected.
