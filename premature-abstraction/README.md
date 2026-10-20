# Do your copies change together?

Companion code for [Don't count the copies, watch them change](https://mainlog.dev/posts/premature-abstraction/).

`cochange.py` finds near-duplicate functions in a Python repository and asks git, for each pair and each group of
copies, how often they changed together and how often only one of them changed. That is the question that decides
whether two copies belong in one function: copies that always move together are one thing written twice, and copies
that change apart only look alike.

## Run it on your repository

Python 3.12 or later, standard library only, and `git`.

```sh
python3 cochange.py path/to/repo src                    # every near-duplicate pair, and the clone groups
python3 cochange.py path/to/repo src --pair A B         # one pair, commit by commit
python3 cochange.py path/to/repo src --json out.json    # everything, for your own analysis
```

`A` and `B` are `FILE:QUALNAME`, for example `httpx/_client.py:AsyncClient._send_single_request`. Options:
`--threshold 0.85` (how alike, names set aside), `--min-lines 6` (shortest body considered), `--sweep 20` (a commit
touching more files than this is set aside as sweeping).

For one pair, and for languages other than Python, the [Copy drift](https://mainlog.dev/tools/copy-drift/) tool does
the same count in a browser from two pasted functions and their `git log -L` output.

## How it counts

1. **Near-duplicates.** Every function and method of six or more lines, its body tokenised with identifiers, strings
   and numbers replaced by placeholders, compared pairwise with `difflib`'s ratio. 85% or more is a pair. A function
   and a function nested inside it are not a pair.
2. **History.** `git log -L` on each function's lines. It follows lines, not functions, so the history stops at the
   first commit, going back, where the traced lines are not mostly inside a function of the same qualified name in that
   commit's version of the file. Without this, httpx's sync `Client._send_single_request` traces back into 2019, through
   months when httpx had no sync client.
3. **Only after both existed.** A pair's commits count from strictly after the younger copy's first commit.
4. **Sweeping commits** (more than `--sweep` files) are counted apart.
5. **Clone groups** are the connected pairs. A group change is consistent when it touches every member that existed
   before it, partial otherwise. Summing pairs instead counts a change to a group of five ten times over.

## Reproduce the post's numbers

```sh
./fetch_httpx.sh /tmp/httpx      # httpx at b5addb6, blobs fetched lazily
python3 cochange.py /tmp/httpx httpx --pair httpx/_client.py:Client._send_single_request httpx/_client.py:AsyncClient._send_single_request
python3 cochange.py /tmp/httpx httpx --pair httpx/_api.py:get httpx/_client.py:Client.get
python3 cochange.py /tmp/httpx httpx --json httpx.json    # a few minutes the first time
python3 sample.py httpx.json
```

| | Result at httpx `b5addb6` |
|---|---|
| The two `_send_single_request` | 99.6% alike; since July 2020, together 15, sync only 1, async only 6; 22 earlier traced commits were other code |
| `httpx.get` and `Client.get` | 87.8% alike; since January 2020, together 3, module only 5, client only 7 |
| Whole repository | 201 functions, 128 pairs in 28 clone groups; 232 consistent group changes, 73 partial, 38 sweeping |
| 20 partial changes, read by hand | 6 out of step, 9 on purpose, 4 cosmetic, 1 miscounted |

The limits section's flask figures come from flask at a pinned commit too:

```sh
./fetch_flask.sh /tmp/flask      # flask at d086db8
python3 cochange.py /tmp/flask src
```

| | Result at flask `d086db8` |
|---|---|
| Whole repository | 160 functions, 22 pairs in 6 clone groups; 11 consistent group changes, 0 partial, 3 sweeping |

`hand_check.json` holds the hand classification, with a note per commit, and `sample.py` checks that a fresh draw
(`random.seed(2547)`) is still the sample that was read.

`tool_reference.py` is the browser tool's arithmetic in Python, using the real `tokenize` and `difflib`, which the
tool's fixtures are checked against.

## Tests

```sh
python3 -m pip install -r requirements.txt
python3 -m pytest -q tests
```

Each counting rule has a test on a small repository built commit by commit, so the expected numbers are known by
hand: the before-both-existed rule, sweeping commits, a history that must stop where the function did not exist,
methods of different classes, and a three-copy group counted once per change.

## Limits

- The count says how often copies moved apart, not why. A one-sided change is either a missed update or a sign the
  copies do different jobs, and only reading it tells which. Of the 20 sampled in httpx, 6 were out of step.
- "Together" means both changed in one commit, not that they changed the same way.
- A function renamed or moved to a new class loses the history from before, by rule 2. That undercounts rather than
  inventing history.
- Python only, because it parses with `ast`.
