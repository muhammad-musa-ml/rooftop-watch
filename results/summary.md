# Results, seeds 1, 2, 3, 4, 5

Fleets of 80 rooftops, synthetic data. Counts are pooled over the 5 seeds unless a cell says median and range over seeds.

## Quiet feeds

28 clean days to learn each profile, then 28 days with outages of 4, 8, 24 or 48 hours (0, 1 or 2 per rooftop); each rule is checked every 15 minutes. An outage is detected when, between the first upload that did not arrive and the end of the outage, the rule fires with the outage and would not fire without it; the delay runs from that first missed upload. A false alert is an alert that starts on the same fleet with no outage, split by whether the rooftop was open as scheduled or had skipped a day it normally opens. Outages with nothing to miss (the rooftop was closed for all of it) are left out.

| rule | outages detected | detected, by seed: median (range) | median delay (h) | 90th percentile delay (h) | false alerts per 100 rooftop-weeks, scheduled days | false alerts per 100 rooftop-weeks, skipped days |
| --- | --- | --- | --- | --- | --- | --- |
| quiet k=3 | 169 of 179 (94%) | 97 (90-97)% | 0.4 | 1.2 | 883.5 | 8.5 |
| quiet k=5 | 160 of 179 (89%) | 90 (85-94)% | 0.8 | 2.5 | 189.8 | 8.8 |
| quiet k=8 | 149 of 179 (83%) | 87 (68-90)% | 1.4 | 5.2 | 21.7 | 9.2 |
| quiet k=12 | 139 of 179 (78%) | 82 (58-87)% | 2.3 | 11.7 | 1.9 | 8.9 |
| quiet k=20 | 107 of 179 (60%) | 61 (42-74)% | 4.2 | 17.6 | 0.1 | 7.7 |
| gap 6h | 153 of 179 (85%) | 84 (80-97)% | 0.2 | 5.8 | 585.4 | 6.1 |
| gap 12h | 130 of 179 (73%) | 71 (64-90)% | 0.2 | 11.7 | 455.5 | 7.7 |
| gap 24h | 96 of 179 (54%) | 55 (48-66)% | 11.1 | 23.6 | 98.8 | 7.8 |

Outages judged: 29 to 45 per seed.

### Detected by outage length

Outages detected out of outages judged, pooled over the seeds.

| rule | 4 h | 8 h | 24 h | 48 h |
| --- | --- | --- | --- | --- |
| quiet k=3 | 34 of 38 | 33 of 39 | 44 of 44 | 58 of 58 |
| quiet k=5 | 28 of 38 | 30 of 39 | 44 of 44 | 58 of 58 |
| quiet k=8 | 23 of 38 | 25 of 39 | 43 of 44 | 58 of 58 |
| quiet k=12 | 17 of 38 | 23 of 39 | 41 of 44 | 58 of 58 |
| quiet k=20 | 4 of 38 | 14 of 39 | 34 of 44 | 55 of 58 |
| gap 6h | 14 of 38 | 37 of 39 | 44 of 44 | 58 of 58 |
| gap 12h | 12 of 38 | 16 of 39 | 44 of 44 | 58 of 58 |
| gap 24h | 0 of 38 | 1 of 39 | 37 of 44 | 58 of 58 |

## Reconcile

Judged at day 31, three days after the 28-day span. Totals over the 5 seeds. 'Missed' is an injected fault no finding with that code names; 'extra' is a finding for something that was not injected.

| code | injected | caught | missed | extra |
| --- | --- | --- | --- | --- |
| MISSING_RECORDING | 12807 | 12807 | 0 | 0 |
| ORPHAN_RECORDING | 8585 | 8585 | 0 | 0 |
| DUPLICATE_CONTENT | 4248 | 4248 | 0 | 0 |
| EMPTY_FILE | 2114 | 2114 | 0 | 0 |
| TRUNCATED_FILE | 6135 | 6135 | 0 | 0 |
| THIN_TRANSCRIPT | 8444 | 8421 | 23 | 76 |
| HIDDEN_WRONG_AUDIO | 4310 | not checked | not checked | 0 reported by another check |

### Thin-transcript threshold

Words a minute below this share of the rooftop's median counts as thin. The default is the value picked on the tuning seeds.

| share of median | injected | caught | extra |
| --- | --- | --- | --- |
| 0.05 | 8444 | 5622 | 1 |
| 0.1 | 8444 | 8421 | 76 |
| 0.15 | 8444 | 8444 | 786 |
| 0.25 | 8444 | 8444 | 5960 |
| 0.35 | 8444 | 8444 | 14283 |

### Grace window

One Monday-morning snapshot (day 14, 16:00 UTC) of each fleet, judged with and without a grace window. Every finding here is in flight, not defective; cells are the median and range over seeds.

| grace (minutes) | missing recordings reported | orphan recordings reported |
| --- | --- | --- |
| 360 | 0 (0-0) | 0 (0-0) |
| 0 | 15 (9-24) | 590 (561-714) |
