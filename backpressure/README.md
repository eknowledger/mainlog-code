# Someone has to hear no: backpressure, load shedding and who pays for an overload

From the post [Someone has to hear no](https://mainlog.dev/posts/backpressure/), in the collection *Down to the wire*.

| File | What it does |
|---|---|
| `shed_sim.py` | A model, not a measurement: a seeded discrete-event simulation of a shop's checkout service (8 workers, 80 ms per request on average, 100 requests a second at most) through a one-minute sale at 150 a second, under six policies for the request that does not fit. Prints the post's tables; `--timeline`, `--csv` and `--policy` for the charts and for your own numbers |
| `export_runs.py` | Writes `runs.json`, the 162 recorded runs the post's lab replays: every policy, 0, 1 or 3 retries, a 120, 150 or 200 a second sale, a 1, 2 or 4 second timeout, each marked stuck or not. The site copies the file as it is |
| `plot_figures.py` | Draws the post's two charts from `shed_sim.py`. Needs matplotlib |
| `test_shed_sim.py` | The post's numbers, and the shape of the model: every person ends in exactly one outcome, a short bound never times anyone out, and retries keep the unbounded queue down |
| `requirements.txt` | matplotlib, for the charts only; everything else is the standard library |

## Run it

```sh
python3 shed_sim.py                       # the post's tables, about 6 s
python3 shed_sim.py --timeline            # answered per 10 s window, every policy, 0, 1 and 3 retries
python3 shed_sim.py --policy bounded --retries 2 --queue-bound 1024    # one run, with your own numbers
python3 export_runs.py > runs.json        # the lab's data, about 30 s
python3 -m unittest test_shed_sim.py
```

The first table, no retries:

```text
  policy                                 answered  fast no timed out      p50     p99    orders tries  wasted  recovered
  unbounded queue (FIFO)                     8.4%     0.0%     91.6%   1125ms  1982ms      8.4%  1.00   14309  90 s
  skip the abandoned                        19.5%     0.0%     80.5%   1961ms  2000ms     18.6%  1.00    4466  0 s
  bounded queue, reject when full           67.1%    32.9%      0.0%    550ms   894ms     68.4%  1.00       0  0 s
  bounded, orders first                     67.1%    32.9%      0.0%    549ms   892ms    100.0%  1.00       0  0 s
  queue timeout (CoDel-style)               64.4%    35.6%      0.0%     98ms   402ms     66.8%  1.00       0  0 s
  newest first (adaptive LIFO + CoDel)      64.3%    35.7%      0.0%     81ms   389ms     65.3%  1.00       0  0 s
```

## The model, in one place

- **Arrivals**: Poisson, 70 a second, then 150 a second from 60 s to 120 s, then 70 again to 300 s. One in five
  places an order; the rest are price previews.
- **Service**: exponential, mean 80 ms, on 8 workers. Work is never cancelled: a request whose client has given up is
  still served when its turn comes, unless the policy checks.
- **Clients**: each attempt waits 2 s, then gives up. With retries, the next attempt goes 0.5 to 1.5 s later, after a
  timeout or a refusal. A person gives up altogether 10 s after their first try.
- **Answered** means a successful response within those 10 s. That is the definition that still means something with
  retries: an attempt that succeeds after the person has gone is counted as late, which is a failure.
- **Recovered** is the first 10 s window after the sale from which every later window answers 90% or more.
- **The queue timeout** is Fail at Scale's CoDel variant (Maurer, ACM Queue, 2015) with its published values: as a
  request arrives, if the queue has not been empty in the last 100 ms, it may wait 5 ms; otherwise 100 ms. That limit is
  set on arrival, as the article's `onNewRequest` does, and enforced as a worker picks the request up. **Newest first** is the same, plus taking the newest request while the queue is standing.

## Why it is built this way

- **Its own random generator.** mulberry32, the same function the site's tools use, so the *Overload and queue* tool,
  which runs this model in TypeScript, prints these numbers to the last digit. Python's own generator would make that
  impossible.
- **Seeded.** Every machine and every Python version prints the same tables. Another seed moves the shares by a point
  or so, which is the model's noise.
- **Exponential service has a light tail.** Real services have heavier ones, which makes every policy worse. The
  comparison between policies is the point, not the absolute shares.
