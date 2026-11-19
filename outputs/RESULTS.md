# Credit Risk Management: Theory and Application

## Scenario repricing of three synthetic bond portfolios - results

All portfolio figures are instantaneous, first-order mark-to-market changes in local currency, as a percentage of initial portfolio market value. Benchmark yield changes are observed; holdings, durations and the two designed stresses are illustrative.

Run on 2026-11-19T15:48:25+00:00 by `credit_risk_scenarios.py`.

## Observed inputs (benchmark yields)

- **USD** `DGS10` (Federal Reserve H.15 via FRED): **ok**, 249 valid obs 2022-01-03 to 2022-12-30
- **CAD** `BD.CDN.10YR.DQ.YLD` (Bank of Canada Valet): **ok**, 248 valid obs 2022-01-04 to 2022-12-30. Note: Replaces V39055 named in the project brief, which Valet returns as 'Series V39055 not found' (HTTP 404). Override with --cad-series.
- **EUR** `YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y` (ECB Data Portal): **ok**, 257 valid obs 2022-01-03 to 2022-12-30

### January month-end to December month-end 2022 benchmark repricing

| Currency | Jan common date | Jan yield % | Dec common date | Dec yield % | Change bp |
|---|---|---:|---|---:|---:|
| USD | 2022-01-31 | 1.7900 | 2022-12-30 | 3.8800 | +209.00 |
| CAD | 2022-01-31 | 1.7800 | 2022-12-30 | 3.3000 | +152.00 |
| EUR | 2022-01-31 | 0.0276 | 2022-12-30 | 2.5553 | +252.78 |

These yield changes are observed. Early-January moves before the January month-end common date are excluded by construction.

Resulting rate-only repricing (illustrative: observed move x synthetic 6-year benchmark duration, spreads held at 0):

| Portfolio | Rate shock bp | Rate contribution | Net |
|---|---:|---:|---:|
| USD | +209.00 | -12.54% | -12.54% |
| CAD | +152.00 | -9.12% | -9.12% |
| EUR | +252.78 | -15.17% | -15.17% |

## Illustrative portfolio results (% of initial MV, local currency)

| Scenario | Portfolio | Rate | Spread | Net |
|---|---|---:|---:|---:|
| January month-end to December month-end 2022 benchmark repricing | USD | -12.54% | +0.00% | -12.54% |
| January month-end to December month-end 2022 benchmark repricing | CAD | -9.12% | +0.00% | -9.12% |
| January month-end to December month-end 2022 benchmark repricing | EUR | -15.17% | +0.00% | -15.17% |
| Common spread widening | USD | +0.00% | -0.90% | -0.90% |
| Common spread widening | CAD | +0.00% | -0.90% | -0.90% |
| Common spread widening | EUR | +0.00% | -3.00% | -3.00% |
| Risk-off with a bond rally | USD | +3.00% | -0.90% | +2.10% |
| Risk-off with a bond rally | CAD | +3.00% | -0.90% | +2.10% |
| Risk-off with a bond rally | EUR | +3.00% | -5.40% | -2.40% |

### Sensitivities

| Test | Scenario | Portfolio | Value | Net |
|---|---|---|---:|---:|
| composition | Common spread widening | USD | 10% | -0.30% |
| composition | Common spread widening | USD | 30% | -0.90% |
| composition | Common spread widening | USD | 60% | -1.80% |
| composition | Common spread widening | CAD | 10% | -0.30% |
| composition | Common spread widening | CAD | 30% | -0.90% |
| composition | Common spread widening | CAD | 60% | -1.80% |
| composition | Common spread widening | EUR | 10% | -3.00% |
| composition | Common spread widening | EUR | 30% | -3.00% |
| composition | Common spread widening | EUR | 60% | -3.00% |
| composition | Risk-off with a bond rally | EUR | 10% | -1.80% |
| composition | Risk-off with a bond rally | EUR | 30% | -2.40% |
| composition | Risk-off with a bond rally | EUR | 60% | -3.30% |
| spread duration | Common spread widening | USD | 4y | -0.60% |
| spread duration | Common spread widening | CAD | 4y | -0.60% |
| spread duration | Common spread widening | EUR | 4y | -2.00% |
| spread duration | Common spread widening | USD | 6y | -0.90% |
| spread duration | Common spread widening | CAD | 6y | -0.90% |
| spread duration | Common spread widening | EUR | 6y | -3.00% |
| spread duration | Common spread widening | USD | 8y | -1.20% |
| spread duration | Common spread widening | CAD | 8y | -1.20% |
| spread duration | Common spread widening | EUR | 8y | -4.00% |

## Validation

| Check | Result | Detail |
|---|---|---|
| weights sum to one per portfolio (base) | PASS | {'CAD': 1.0, 'EUR': 1.0, 'USD': 1.0} |
| weights sum to one per portfolio (composition variants) | PASS |  |
| zero shock produces zero P&L | PASS |  |
| 100 bp equals 0.01 in decimal yield | PASS |  |
| higher benchmark rate lowers value (all sleeves, D_rate>0) | PASS |  |
| higher spread lowers value (sleeves with D_spread>0); zero for government | PASS |  |
| base common widening USD = -0.90% | PASS | -0.900000% |
| base common widening CAD = -0.90% | PASS | -0.900000% |
| base common widening EUR = -3.00% | PASS | -3.000000% |
| base risk-off USD: rate +3.00%, spread -0.90%, net +2.10% | PASS | got +3.000000%, -0.900000%, +2.100000% |
| base risk-off CAD: rate +3.00%, spread -0.90%, net +2.10% | PASS | got +3.000000%, -0.900000%, +2.100000% |
| base risk-off EUR: rate +3.00%, spread -5.40%, net -2.40% | PASS | got +3.000000%, -5.400000%, -2.400000% |
| contributions reconcile before rounding (sleeve and portfolio) | PASS | max abs error 0.00e+00 |
| EUR common stress flat across seniority mix and sleeve swap | PASS | mix 10/30/60%: [-3.0, -3.0, -3.0]; swapped 30/70: -3.000000% |
| historical case: three validated series, 12 monthly common observations | PASS | Jan 2022-01-31 -> Dec 2022-12-30 |
| no cross-currency P&L aggregation | PASS |  |

## Interpretation

- **Common spread widening isolates spread-bearing weights.** Every spread-bearing sleeve gets the same +50 bp at the same 6-year spread duration and benchmarks do not move, so the only thing that differs across portfolios is the share of spread-bearing holdings: 30% in USD and CAD (-0.90%) versus 100% in EUR (-3.00%). That is why the EUR result is unchanged by the SP/SNP mix. It is a property of the synthetic design, not evidence about real portfolios.
- **Risk-off also changes the shocks themselves.** The -50 bp benchmark rally adds +3.00% everywhere, but the assumed spread shocks now differ by sleeve (+50 bp state/local and provincial, +75 bp SP, +125 bp SNP). The EUR loss therefore reflects both its 100% spread-bearing weight and the larger hypothesised bank spread moves; the two effects are not separated by this scenario. Moving SNP weight from 10% to 60% takes EUR from -1.80% to -3.30% net.
- Spread-duration sensitivity scales spread losses linearly: going from 4 to 8 years doubles them.
- The 2022 case is rate-only: it reprices the benchmark sleeves for the observed 10-year moves with no spread move, so it says nothing about 2022 credit-spread behaviour.

## Limitations

- Synthetic weights and durations; no Banque de France holdings or sensitivities.
- First-order duration only: no convexity, carry, roll-down, default losses or FX. Same pre-shock weights.
- Designed stresses carry no historical frequency or empirical calibration.
- Benchmark series differ in instrument and compounding (CMT par yield, benchmark bond yield, AAA spot rate); only within-series changes are used. The EUR AAA curve is a proxy, not an OIS or OAT curve.
- The 10-year move is applied as a parallel shock to a 6-year-duration portfolio.
- No cross-currency aggregation and no ranking of actual portfolios.
