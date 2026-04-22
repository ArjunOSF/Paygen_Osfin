# Recon Test Scenario Summary

**Generated:** 2026-04-22 17:07:43  
**Business Date:** 20260422  
**Channel:** POS  
**Role:** ISSUING  
**Network:** RUPAY  

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
| Network (RUPAY) | 5 | present |

## Files Generated

| File | Path | Records |
|------|------|---------|
| PTLF (Switch POS) | `ptlf.txt` | 5 |
| ATM_C (CBS) | `atm_c.txt` | 5 |
| RuPay XML (ISS) | `rupay_iss.xml` | 5 |

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
