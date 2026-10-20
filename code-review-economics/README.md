# What splitting a pull request costs in waiting

From the post [Splitting a pull request costs waiting. Here's how much.](https://mainlog.dev/posts/code-review-economics/),
in the series *The unwritten rules*.

| File | What it does |
|---|---|
| `fetch_prs.py` | Fetches a repository's most recently created merged PRs through the GitHub CLI's GraphQL access: size, author, the last "ready for review" event, review threads and every review |
| `complete_reviews.py` | Run once on the snapshot: it was first fetched with only each PR's first 10 reviews, and this filled in all of them for the 100 PRs that had 10 |
| `split_cost.py` | For each PR, its size and its pickup wait (ready for review to the first review by someone other than its author). Says what it left out and why, bins by size, fits the elasticity e of wait to size with a bootstrap range and without the PRs floored at 3 minutes, and prices splitting a change into 2, 4 or 8 PRs reviewed one after another, side by side, or in one sitting: once as if size drove the pickup as the fit says, once as if it had no effect. `--csv` prints the size,wait pairs |
| `data/` | The snapshot the post's numbers come from: the 1,000 most recently created merged PRs of astral-sh/ruff, grafana/grafana and pola-rs/polars, fetched on 2026-10-11, with their reviews completed the same day. Public data from GitHub's API, committed so the numbers can be checked without a token and CI never calls GitHub |
| `tests/` | The filter on hand-made PRs, the fit on data with a known elasticity, and the split rule against summing the pieces |
| `demo-output.txt` | The run the post quotes |

Standard library only, Python 3.12 or later. `fetch_prs.py` also needs the [GitHub CLI](https://cli.github.com/).

## Run it

```sh
python3 split_cost.py data/*.jsonl                 # the post's tables, about 2 seconds
python3 split_cost.py --csv data/grafana_grafana.jsonl > grafana.csv
python3 -m unittest discover -s tests

# your own repository: fetch, then the same
python3 fetch_prs.py your-org/your-repo 1000 > data/your-org_your-repo.jsonl
python3 split_cost.py data/your-org_your-repo.jsonl
```

The CSV pastes straight into the site's *PR split cost* tool.

For grafana/grafana, `split_cost.py` prints, among the rest:

```text
fit: e = 0.25 (95% bootstrap 0.17 to 0.34), r = 0.19, n = 857; doubling a PR multiplies its expected wait by 1.19
     without the 96 PRs reviewed within 3 minutes of being ready: e = 0.16, n = 761
split into k PRs, expected waiting against one PR's, if size drives the pickup as the fit says | if size has no effect:
   k        one after another   side by side  in one sitting |  after   side  sitting
   2     x1.68 (1.58 to 1.78)          x1.54           x0.84 |     x2  x1.83    x1.00
   4     x2.83 (2.50 to 3.17)          x2.26           x0.71 |     x4  x3.17    x1.00
   8     x4.76 (3.96 to 5.65)          x3.10           x0.59 |     x8  x5.12    x1.00
on medians instead of means, k = 8: one after another x53.21, side by side x31.93, in one sitting x0.59
with a bot review: 15% of PRs of 11-50 lines, 54% of 501-2000; one 800-line PR on the fit: median pickup 2.1 h, mean 34.2 h
```

How it filters, exactly: a PR is left out if its author is a bot (GitHub's Bot type, a login ending in `bot` or
`[bot]`; every such login in the snapshot was checked by hand), or if nobody but its author and bots reviewed it.
A wait under 3 minutes counts as 3 minutes so a log can be taken; most of those are drafts reviewed before their
author marked them ready, and the report shows e without them.

## What it does not show

- **Cause.** The fit is a correlation, and a weak one (r = 0.12 to 0.27 on these three). The report prices the split
  both as if size drove the wait and as if it had no effect; the truth is somewhere between. Small PRs are often small
  fixes by maintainers, picked up fast for that reason. It describes these repositories in this window.
- **PRs nobody reviewed.** A PR merged without a review by someone other than its author is left out: 170 on ruff,
  where they are mostly small (median 44 lines), 3 on grafana, and 376 on polars, where they are larger than the ones
  kept (median 110 lines against 62).
- **A company team.** These are busy open-source projects reviewed mostly by their maintainers. The direction matches
  published company data; the exponents may not transfer.
