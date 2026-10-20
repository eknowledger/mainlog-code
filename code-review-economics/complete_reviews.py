"""Fill in every review for the snapshot's PRs that were fetched with only their first 10.

    python3 complete_reviews.py data/*.jsonl

The first fetch asked for `reviews(first:10)`. A PR with ten reviews may have had its first review by someone other
than its author after those ten (most often after a run of bot reviews), so it would have been wrongly left out. This
re-fetches every review of those PRs, and only those, and rewrites the file in place. Run once on 2026-10-11; the
committed snapshot is the result, so CI doesn't run it and doesn't need GitHub.
"""

import json
import sys
from pathlib import Path

from fetch_prs import all_reviews


def main() -> None:
    for path in map(Path, sys.argv[1:]):
        owner, name = path.stem.split("_", 1)
        prs = [json.loads(line) for line in path.open()]
        filled = 0
        for p in prs:
            if len(p["reviews"]["nodes"]) >= 10:
                p["reviews"]["nodes"] = all_reviews(owner, name, p["number"])
                filled += 1
        path.write_text("".join(json.dumps(p) + "\n" for p in prs))
        print(f"{owner}/{name}: completed the reviews of {filled} PRs", file=sys.stderr)


if __name__ == "__main__":
    main()
