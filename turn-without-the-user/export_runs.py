"""Write every scenario's log to runs.json, for the posts' replay labs.

The labs replay these recorded runs rather than reimplementing the runtime in the browser, so what the reader sees
is this runtime's real output. Re-run after changing the runtime, and copy runs.json to the site.
"""
import json
import subprocess
import sys

names = subprocess.run([sys.executable, "-m", "agent_runtime.demo", "--list"], capture_output=True, text=True,
                       check=True).stdout.split()
# The runs whose point is in the model's history get a second entry with it.
WITH_HISTORY = ["offer-hostile-model-question", "offer-as-user-origin", "revise-after-text"]
runs = {}
for name in names + [f"{n} --history" for n in WITH_HISTORY]:
    out = subprocess.run([sys.executable, "-m", "agent_runtime.demo", *name.split()], capture_output=True, text=True,
                         check=True).stdout
    runs[name] = out.split("\n", 1)[1].rstrip("\n") if out.startswith("== ") else out.rstrip("\n")
with open("runs.json", "w") as f:
    json.dump(runs, f, indent=1, ensure_ascii=False)
    f.write("\n")
print(len(runs), "runs")
