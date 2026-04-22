# Recon Test Scenario Summary

**Generated:** 2026-04-22 17:07:43  
**Business Date:** 20260422  
**Channel:** ATM  
**Role:** ACQUIRING  
**Network:** VISA  

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
| Network (VISA) | 5 | present |
| EJ | 5 | 5 success, 0 fail |

## Files Generated

| File | Path | Records |
|------|------|---------|
| TLF (Switch ATM) | `tlf.txt` | 5 |
| ATM_C (CBS) | `atm_c.txt` | 5 |
| EJ (Hyosung) | `ej.csv` | 5 |
| Visa EPIN | `visa_epin.txt` | 5 |

## Join Key Reference

```
PRIMARY KEY (links Switch → CBS → EJ):
  TLF/PTLF.SequenceNumber  =  ATM_C.SEQ_NO  =  EJ.TRXN_NO
  Sample: 495719689509

SECONDARY KEY (links CBS → Network):
  ATM_C.RR_NO  =  MC_T112.DE37  =  Visa_EPIN.ARN[embedded]  =  RuPay.nRRN
  Sample: 629667197569

AMOUNT (must match across all files, in paise):
  Sample: 174700 paise
```

## Recon Expected Outcome

**RECONCILED** — All layers match. No breaks expected.
