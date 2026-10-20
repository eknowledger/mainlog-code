# agent-event-bus: facts, not instructions, between domain agents

The demo behind [The agent event bus](https://mainlog.dev/posts/agent-event-bus/). Three domain agents from the dinner-booking world,
Bookings, Calendar and Messaging, and two ways of wiring them: Bookings calling the others, and Bookings publishing
on a bus they subscribe to. The restaurant cancels R1, its webhook carries a note, and Bookings has to tell the
other two.

Standard library only, Python 3.9 or later. No language model: the two "models" are scripted, so every difference
between runs comes from the wiring. Messaging's model is still sent a real request, a system prompt, one tool and a
user turn, so what it reads in each run can be printed.

```sh
python -m bus_demo.demo --list        # every scenario
python -m bus_demo.demo call bus      # the opening: the same cancellation, by call and by bus
python -m bus_demo.demo --all         # everything; demo-output.txt is a saved run
python -m bus_demo.prompts            # what Messaging's model is sent in the four runs of the 2 x 2
python -m bus_demo.prompts --tools    # the two tools Bookings' model can reach Messaging through
python -m pip install pytest==8.3.5 && python -m pytest -q tests
```

## What's here

- `bus_demo/agents.py`: the agents, the scripted models, and the bus (a durable list of events with an offset per
  subscriber, and a schema of the fields each event type may carry).
- `bus_demo/scenarios.py`: every run the post shows.
- `bus_demo/demo.py`: prints a run's log and how it ended.
- `bus_demo/prompts.py`: prints the requests Messaging built for its model in a run.
- `tests/`: the claims the post makes, one test each.

## Scenarios by section

| Section of the post | Scenarios |
|---|---|
| Two wirings, one cancellation | `call`, `bus` |
| The caller writes the next model's task | `python -m bus_demo.prompts call call-typed`, `python -m bus_demo.prompts --tools` |
| The bus didn't stop the link; the payload did | `call-typed`, `bus-note` |
| A command is still a command | `bus-command`, `bus-note-strict` |
| What the bus provides: failure stays local | `call-down`, `call-down-messaging-first`, `bus-down` |
| The log is also a record | `replay` |
| What the bus costs | `duplicate`, `duplicate-no-dedupe` |

## What it shows, and what it doesn't

- **The models are scripted.** Bookings' model folds the restaurant's note into the instruction it writes, the way a
  summary would, and Messaging's model treats any "text the group: X" as a request. That shows the *channel* an
  instruction can travel through. It says nothing about how often a real model follows such text.
- **One process, no network.** The bus is a Python list. Delivery, offsets and redelivery are modelled, not
  measured, so there are no latency or throughput numbers here, on purpose.
- **Admission is assumed.** Bookings logs "signature checked" and moves on; how a webhook is checked, stored and
  deduplicated is the subject of the [turn-without-the-user](../turn-without-the-user) runtime.
- **The schema narrows the channel; it doesn't close it.** A typed `slot` string could still carry text. A
  subscriber has to treat every field as data.
