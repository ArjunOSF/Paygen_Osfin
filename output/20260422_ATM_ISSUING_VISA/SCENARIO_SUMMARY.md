# Recon Test Scenario Summary

**Generated:** 2026-04-22 17:18:04  
**Business Date:** 20260422  
**Channel:** ATM  
**Role:** ISSUING  
**Network:** VISA  

## Layer Configuration

```
Switch=PASS | CBS=PASS | Network=PASS
```

## Transaction Counts

| Layer | Transactions | Notes |
|-------|-------------|-------|
| Total generated | 20 | |
| Switch (TLF/PTLF) | 20 | present |
| CBS (ATM_C) | 20 | present |
| Network (VISA) | 20 | present |

## Files Generated

| File | Path | Records |
|------|------|---------|
| TLF (Switch ATM) | `tlf.txt` | 20 |
| ATM_C (CBS) | `atm_c.txt` | 20 |
| Visa EPIN | `visa_epin.txt` | 20 |

## Join Key Reference

```
PRIMARY KEY (links Switch → CBS → EJ):
  TLF/PTLF.SequenceNumber  =  ATM_C.SEQ_NO  =  EJ.TRXN_NO
  Sample: 803315460795

SECONDARY KEY (links CBS → Network):
  ATM_C.RR_NO  =  MC_T112.DE37  =  Visa_EPIN.ARN[embedded]  =  RuPay.nRRN
  Sample: 689374874230

AMOUNT (must match across all files, in paise):
  Sample: 312900 paise
```

## Recon Expected Outcome

**RECONCILED** — All layers match. No breaks expected.
