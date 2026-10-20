# Every latency number answers one question

From the post [Every latency number answers one question](https://mainlog.dev/posts/measuring-latency/), in the
series *The unwritten rules*.

| File | What it does |
|---|---|
| `latency_questions.py` | A seeded model of a pricing service on four hosts, one of which stalls 2% of its requests by 300 to 600 ms. Draws 100,000 requests and asks each statistic its question: the mean (and Little's law), the percentiles, the share under 100 ms. Then rolls them up across hosts, and makes pages that wait for 1, 5, 20 or 50 calls at once. `--busy` moves traffic onto the stalling host; `--csv fanout` and `--csv sweep` print the data behind the post's two charts |
| `omission.py` | A real TCP server on localhost, in its own process, that freezes for 200 ms every second, load-tested at 200 requests a second and timed two ways: from when each request was sent, and from when it was due. The gap between the two is coordinated omission |
| `make_tool_samples.py` | Cuts the samples in the site's *Percentiles across hosts* tool from seeded runs of the same model: the run's 100,000 requests counted into Prometheus histogram buckets per host, and a few raw latencies per host; `tool-samples.txt` is its output |
| `tests/` | The claims the post makes, checked on the model: shares sum exactly, means only when weighted, and the average of the hosts' p99s is wrong in both directions |
| `demo-output.txt`, `demo-output-busy.txt`, `omission-output.txt`, `fanout.csv`, `sweep.csv` | The runs the post quotes |

Standard library only, Python 3.12 or later. The model draws its random numbers from mulberry32 and Box-Muller
rather than Python's `random`, so the post's lab, which runs the same model in JavaScript, shows the same numbers.

## Run it

```sh
python3 latency_questions.py              # the run the post quotes
python3 latency_questions.py --busy 0.7   # 70% of the traffic on the stalling host
python3 latency_questions.py --csv sweep  # averaged and true p99 as traffic moves onto it
python3 omission.py                       # about 20 seconds
python3 -m unittest discover -s tests
```

`latency_questions.py` prints, among the rest:

```text
mean 23.4 ms, p50 20.0, p99 49.0, p99.9 552.7
Little's law: 2000 req/s x 23.4 ms = 46.8 requests in flight on average
average of host p99s 149.5 ms vs true p99 49.0 ms: 3.05x, too high
under 100 ms: counts [24896, 25196, 24954, 24471] sum to 99,517 of 100,000 = 99.517%; pooled 99.517%
20 calls: page p50  38.8 ms, page p99 581.5, pages over 100 ms  9.1% (1 - (1 - q)^N predicts  9.2%)
```

`omission.py` depends on the machine. One run on a laptop:

```text
from send time       n= 2001  p50    0.18  p90    0.26  p99    1.02  max  205.25  mean   1.21 ms  over 10 ms: 10
from scheduled time  n= 2001  p50    0.85  p90  106.45  p99  193.84  max  204.79  mean  21.91 ms  over 10 ms: 392
```

Across seven runs, the scheduled-time p99 stayed between 192 and 196 ms, and the requests over 10 ms between 10 and 12
timed from send against 392 to 404 timed from the schedule. The send-time p99 is not stable (0.7 to 5.7 ms): ten
freezes make about ten slow requests in 2,001, under the 1% a p99 sees, so it is one of the dozen or so slowest ordinary
localhost round trips, which is the machine's own jitter. So CI checks the stable three: the scheduled p99 above 150 ms,
at most 25 requests over 10 ms from send time, and at least 300 from the schedule.

The service, its hosts and its traffic are a model, not a measurement of a real system. The server in
`omission.py` is real, but its freeze is put there on purpose.
