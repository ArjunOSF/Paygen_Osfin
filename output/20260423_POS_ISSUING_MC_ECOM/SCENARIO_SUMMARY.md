# Recon Test Scenario Summary

**Generated:** 2026-04-23 12:11:13  
**Business Date:** 20260423  
**Channel:** POS  
**Role:** ISSUING  
**Network:** MC  

## Layer Configuration

```
Switch=PASS | CBS=PASS | Network=PASS
```

## Transaction Counts

| Layer | Transactions | Notes |
|-------|-------------|-------|
| Total generated | 5 | |
| Switch (TLF/PTLF) | 5 | present |
| CBS (ATM_C) | 5 | present |
| Network (MC) | 5 | present |

## Files Generated

| File | Path | Records |
|------|------|---------|
| PTLF (Switch POS) | `ptlf.txt` | 5 |
| ATM_C (CBS) | `atm_c.txt` | 5 |
| MC T112 | `t112.txt` | 5 |
| MC T140 | `t140.txt` | 5 |

## Join Key Reference

```
PRIMARY KEY (links Switch → CBS → EJ):
  TLF/PTLF.SequenceNumber  =  ATM_C.SEQ_NO  =  EJ.TRXN_NO
  Sample: 390981255226

SECONDARY KEY (links CBS → Network):
  ATM_C.RR_NO  =  MC_T112.DE37  =  Visa_EPIN.ARN[embedded]  =  RuPay.nRRN
  Sample: 763058376020

AMOUNT (must match across all files, in paise):
  Sample: 344100 paise
```

## Recon Expected Outcome

**RECONCILED** — All layers match. No breaks expected.
