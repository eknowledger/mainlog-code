"""Count non-blank, non-comment lines, the metric used for the line-count bar."""

import sys

for path in sys.argv[1:]:
    n = sum(
        1 for line in open(path) if line.strip() and not line.strip().startswith("#")
    )
    print(f"{n:3d}  {path}")
