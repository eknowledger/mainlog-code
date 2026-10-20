"""python -m memory_demo                     the table: every policy, 89 reads over 90 days
python -m memory_demo inputs              every input, with who sent it
python -m memory_demo writes provenance   what the store did with each input under one policy
python -m memory_demo day keep            what memory answers on day 81 under one policy
python -m memory_demo day keep 30         ...on another session day
python -m memory_demo prompt keep         what the planner is sent on day 81 under one policy
python -m memory_demo prompt keep 30      ...on another session day
"""
import sys

from .sim import POLICIES, day, inputs, prompt, table, writes

args = sys.argv[1:]
if not args:
    print(table())
elif args == ["inputs"]:
    print(inputs())
elif len(args) == 2 and args[0] == "writes" and args[1] in POLICIES:
    print(writes(args[1]))
elif args[0] in ("day", "prompt") and len(args) >= 2 and args[1] in POLICIES:
    show = day if args[0] == "day" else prompt
    print(show(args[1], int(args[2]) if len(args) > 2 else 81))
else:
    print(__doc__)
    print("policies:", ", ".join(POLICIES))
    sys.exit(2)
