# rooftop-watch

I built this to test two checks that a pipeline carrying recordings from dealership service bays to one platform would need: a rooftop's uploads going quiet, and a repair order with no usable recording. Everything in it is synthetic.

## Why

Bayworks's posting (https://www.ycombinator.com/companies/bayworks/jobs/d8F4B1K-data-infrastructure-engineer) lists "monitoring and data quality checks that tell us something broke before a dealer does" and says the platform has to grow from 80+ dealerships to 1000 rooftops. I do not know how their system works. I assumed a program on each dealership's machine uploads a recording for every closed repair order, and that two things go wrong: the uploader goes quiet, and a repair order ends up with no good recording. This repository is independent of Bayworks and has not been reviewed by them.

## What it does

- `quiet` learns each rooftop's uploads per hour of the week from the four weeks before its last upload. A rooftop is quiet when the uploads expected since its last one, added up over that profile, reach k (default 20). A closed Sunday or a closed night adds almost nothing, so it is not reported; a missed busy morning adds the most expected uploads per hour. A rooftop with fewer than 50 uploads in the profile window is not judged.
- `reconcile` reads closed repair orders and recordings as of a given minute and reports six things: a repair order with no recording, a recording with no repair order, one file attached to more than one repair order (the first upload is kept, the others are reported; a retry for the same order is not), an empty file, a file under half the rooftop's median bytes per second, and a transcript under a tenth of the rooftop's median words per minute (recordings of at least 60 seconds). A rooftop with fewer than 20 recordings is judged against the fleet's median instead of its own. A repair order or recording younger than a six-hour grace window is not reported as missing or orphaned.
- `rooftop_watch/gen.py` makes the fleet. Each rooftop gets a UTC offset and opening hours (Monday to Friday 7 to 18, Saturday 8 to 14, local time, Sunday closed). Volume averages about 48 repairs on a weekday (the posting's 4,000 cars a day over 80+ dealerships is about 50). Uploads are 60 to 300 minutes late 5% of the time, the repair order reaches the platform up to 240 minutes after it closes, and a rooftop skips 1 in 60 of the days it would normally open. Outages and seven kinds of injected fault are added, with a manifest of the rows each fault changed.

## Run it

Python 3.10 or newer, standard library only; `pytest` for the tests.

```
python -m pytest
python -m rooftop_watch demo --out demo
python -m rooftop_watch quiet demo/fleet.db
python -m rooftop_watch reconcile demo/fleet.db
python -m rooftop_watch evaluate     # writes results/, five seeds
python -m rooftop_watch tune         # how k and the transcript share were chosen; writes results/tune.txt
python -m rooftop_watch scale        # times both checks at 80 and 1000 rooftops
```

`quiet` and `reconcile` exit 1 when they report anything, so either can gate a job. Time is a number of minutes since a Monday 00:00 UTC.

## Results

Copied from `results/summary.md` and `results/scale.md`, which the commands above write. Fleets of 80 rooftops, five seeds (1 to 5), counts pooled over the seeds unless a cell says otherwise.

### Quiet feeds

28 clean days to learn each profile, then 28 days with outages of 4, 8, 24 or 48 hours (0, 1 or 2 per rooftop); each rule is checked every 15 minutes. An outage is detected when, between the first upload that did not arrive and the end of the outage, the rule fires with the outage and would not fire without it; the delay runs from that first missed upload. A false alert is an alert that starts when the same fleet is replayed without its outages, split by whether the rooftop was open as scheduled or had skipped a day it normally opens. Outages with nothing to miss (the rooftop was closed for all of it) are left out. Each rule is scored over 1600 rooftop-weeks (80 rooftops, 4 test weeks, 5 seeds), so 0.1 in the scheduled-days column is 1 alert and 98.8 is 1581.

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

A gap rule alerts when a rooftop has uploaded nothing for that many hours. Outages judged: 29 to 45 per seed. Detected out of judged, by outage length:

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

At the default k=20, most of the 72 outages it misses last 4 or 8 hours (59 of them). Its lead over the 24-hour gap rule (107 against 96) comes from the outages shorter than a day, 18 of 77 against 1 of 77; on outages of a day or longer it caught 89 of 102 and the gap rule 95.

### Reconcile

28 days of repairs with faults injected, judged at day 31, three days after those 28 days end. 'Missed' is an injected fault that no finding of that code reports; 'extra' is a finding for something that was not injected.

| code | injected | caught | missed | extra |
| --- | --- | --- | --- | --- |
| MISSING_RECORDING | 12807 | 12807 | 0 | 0 |
| ORPHAN_RECORDING | 8585 | 8585 | 0 | 0 |
| DUPLICATE_CONTENT | 4248 | 4248 | 0 | 0 |
| EMPTY_FILE | 2114 | 2114 | 0 | 0 |
| TRUNCATED_FILE | 6135 | 6135 | 0 | 0 |
| THIN_TRANSCRIPT | 8444 | 8421 | 23 | 76 |
| HIDDEN_WRONG_AUDIO | 4310 | not checked | not checked | 0 reported by another check |

The thin-transcript share (0.10 of the rooftop's median words per minute) is the default. Swept on the same fleets:

| share of median | injected | caught | extra |
| --- | --- | --- | --- |
| 0.05 | 8444 | 5622 | 1 |
| 0.1 | 8444 | 8421 | 76 |
| 0.15 | 8444 | 8444 | 786 |
| 0.25 | 8444 | 8444 | 5960 |
| 0.35 | 8444 | 8444 | 14283 |

One Monday-morning snapshot (day 14, 16:00 UTC) of each fleet, judged with and without the grace window. Every finding here is in flight, not defective; cells are the median and range over seeds.

| grace (minutes) | missing recordings reported | orphan recordings reported |
| --- | --- | --- |
| 360 | 0 (0-0) | 0 (0-0) |
| 0 | 15 (9-24) | 590 (561-714) |

### Scale

One check of each kind over 28 days of data, 7 days for the reconcile window; median over 5 runs with the range in brackets. Python's sqlite3 on one laptop, loading not included.

| rooftops | recordings | quiet check (s) | reconcile (s) |
| --- | --- | --- | --- |
| 80 | 92490 | 0.1 (0.095-0.128) | 0.311 (0.285-0.442) |
| 1000 | 1040238 | 0.88 (0.844-1.083) | 10.069 (9.548-10.336) |

## What this does not show

- The data is invented. I chose the opening hours, volumes, delays, fault rates and the transcript model, and I wrote both the faults and the checks, so a high score shows that the checks do what they say. It says nothing about how often a real feed fails or what real recordings look like.
- k=20 and the 0.10 transcript share were chosen on seeds 101 to 103, which the reported runs do not use. The rules were the smallest k with at most 2 false alerts per 100 rooftop-weeks on scheduled days, pooled over those seeds (k=12 gave 2.40 there, k=20 gave 0.10), and the share with the best F1 (0.9949 at 0.10). The k rule was not the first one I tried: no k met my first version, because alerts on skipped days put a floor under the false-alert count, so I changed it to count only scheduled days before choosing k, and I kept it when I later corrected the replay. `tune` prints the table and writes it to `results/tune.txt`. Both depend on my fault model. k=12 would detect 78% of the outages instead of 60% for 1.9 false alerts per 100 rooftop-weeks instead of 0.1; `quiet --k 12` runs it.
- The gap rules fire on ordinary closed nights, so on an outage that starts at night they score a short delay; the false-alert column shows what that costs.
- Alerts on days a rooftop skipped stay between 6.1 and 9.2 per 100 rooftop-weeks (pooled) for every rule; only a holiday calendar would remove them.
- I did not break the missed outages down by time of day.
- One kind of fault, a recording of a different repair that is intact in every way a check can see, is injected and never reported.
- It runs on SQLite. I have not run it against PostgreSQL, an object store, a real repair-order feed or real audio.
- The profile for a live check is the four weeks before a rooftop's last upload, so an earlier outage inside that window lowers it. The replay learns from clean history, so it does not show that effect.
- Reconcile took 10.1 seconds at 1000 rooftops and 0.31 at 80: about 32 times as long for about 11 times the recordings. I would not extrapolate it to larger fleets.
- The timings come from a laptop that was also running other jobs, so read them as an order of magnitude.
