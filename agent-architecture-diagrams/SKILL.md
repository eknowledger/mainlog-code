---
name: architecture-diagram
description: >
  Explain a system with a diagram grounded in supplied sources. Use Mermaid for
  behavior and simple flows; use the bundled engine when grouping, layer order,
  and attachment placement carry architectural meaning.
---

# Architecture Diagram

Create a diagram that answers the user's question. Read the sources before
choosing components or relationships. Render the result and inspect it before
calling it ready.

## Resolve the inputs

Obtain the question, source files or documents, output directory, and intended
display width from the request. If width is unspecified, start at 1200 pixels and
say so. Use the host's output conventions when supplied; otherwise use the
requested output directory. Resolve output paths before writing. Keep source
JSON outside the directory owned by a build.

## Choose the representation

| Question | Representation |
|---|---|
| What happens, in order, during one scenario? | Mermaid `sequenceDiagram` |
| What event moves the system between states? | Mermaid `stateDiagram-v2` |
| What is the interface or type surface? | Mermaid `classDiagram` |
| What is a short pipeline or decision flow? | Mermaid flowchart |
| How do components group into layers, and what crosses between them? | Structural JSON and this engine |
| Which shared actor or store participates across several phases? | Structural JSON with a spanning lane |

For the structural path, decide the grouping before asking the engine to draw:

- Use a `stage` for a horizontal row: a layer, phase, or alternative path.
- Use a `spine` for a vertical lane whose actor or data spans several stages.
- Put connectable components in `nodes`. Containers are not edge endpoints.
- Give every node one owner with `parent`. Use `atStage` to align a spine's
  child with a row without giving it a second owner.
- Add an `orders` constraint only when left-to-right order carries meaning.
- Explain each proposed relationship from the source. Do not invent an edge
  to complete a pleasing picture, or remove one to obtain a cleaner layout.

For example, a background-job diagram can put submission, execution, and outcome
handling in stages, with stored job data in a spine. This follows the question
about phases and shared data; it is not a claim that every job system needs
this arrangement.

## Write the structural model

Use the field contract below and the complete
[RQ example](examples/rq-job-path.json). This is the format the Python engine
reads; do not invent alternative field names. Do not write coordinates.

| Record | Fields |
|---|---|
| Top level | `schemaVersion: 1`, `id`, `title`, optional `subtitle`, `targetWidth`, `stages`, `nodes`, `edges` |
| `stages[]` | `id`, `label`, optional `category`; array order determines row order |
| `spines[]` | `id`, `label`, optional `category`, optional `fromStage` and `toStage` |
| `groups[]` | `id`, `label`, `parent`, optional `direction: row` or `column` |
| `nodes[]` | `id`, `title`, `parent`, optional `lines`, `category`, `atStage` |
| `edges[]` | `id`, `from`, `to`, `label` (string or `null`), `kind` |
| `outcomes[]` | `id`, `label`, `after` (stage ID), `verdict` |
| `orders[]` | Lists of node IDs whose left-to-right order must survive |

Allowed edge kinds are `command`, `read`, `event`, and `data`. Supported color
categories are `safety`, `route`, `speech`, `context`, `model`, and `legacy`.
Use color consistently and explain it when necessary.

The engine has Python consistency checks, not a complete JSON Schema validator.
Unknown or misspelled keys can be ignored. Match this contract rather than
assuming every mistaken field will raise an error.

This complete small model shows the distinction between ownership and alignment:

```json
{
  "schemaVersion": 1,
  "id": "queue-reader",
  "title": "The worker takes an ID from shared storage",
  "targetWidth": 1200,
  "stages": [
    {"id": "run", "label": "Execute"}
  ],
  "spines": [
    {"id": "redis", "label": "Shared data",
     "fromStage": "run", "toStage": "run"}
  ],
  "nodes": [
    {"id": "queue", "title": "Queue IDs", "parent": "redis", "atStage": "run"},
    {"id": "worker", "title": "Worker", "parent": "run"}
  ],
  "edges": [
    {"id": "dequeue", "from": "queue", "to": "worker",
     "label": "take ID", "kind": "read"}
  ]
}
```

Check the model against the sources before building. A read arrow can show data
toward its receiver; explain that convention so it is not mistaken for the
direction of a function call. A geometrically valid model can still be false.

## Build and check

The [README](README.md) describes dependency installation. The commands below
run from this skill directory. Substitute resolved paths for the example paths.
`--out` is required; never put the input JSON or logs in that output directory.

```sh
python3 scripts/doctor.py
python3 scripts/build.py examples/rq-job-path.json --out output/rq --crossings 3 --quick
python3 scripts/audit.py output/rq/rq-job-path.svg --spec examples/rq-job-path.json --crossings 3
python3 scripts/cross_audit.py --strict --max-crossings 3 output/rq/rq-job-path.svg
```

Run preflight first. If it fails, repair the dependency setup rather than
reporting a diagram failure.

Keep the same crossing allowance for the build and both audits. Start at three,
inspect any crossings, and pursue zero only when it serves the explanation.
Use `--quick` first to bound the initial search. No fixed node or edge cap
permits dropping required content.

The model supplies meaning. The build varies placement, spacing, ordering where
allowed, and connection sites. It renders candidate SVGs, audits them, and ranks
them. A candidate is one complete proposed drawing, not a changed architecture.

If a build is rejected:

- Read its findings and inspect the rejected preview.
- Keep required components, connections, ownership, and declared order.
- Try legal placement or attachment changes. `--refine` explores the retained
  search state; a recipe with `--untangle` can search connection choices again.
- Do not hand-edit SVG coordinates or remove a relationship to pass.
- If only the crossing allowance blocks delivery, use the reported feasible
  allowance when the crossings are readable and disclose the count.
- If required checks still fail, retain the rejected output and explain the
  unresolved problem. Do not label it accepted.

Accepted output includes SVG, PNG previews, source, geometry, an audit receipt,
a replay recipe, and search state. A failed result goes under `rejected/`.
Files are replaced individually with the receipt last.

To replay the supplied example recipe:

```sh
python3 scripts/build.py examples/rq-job-path.json \
  --recipe examples/rq-job-path.recipe.json --out output/replay
```

The recipe carries its crossing allowance. Reproduction depends on the same
source, engine, fonts, and dependencies.

## Inspect the picture

Open the PNG at its intended display width. Follow the main path through it.
Check labels, apparent ownership, reading order, and whether the explanation
still agrees with the source. Read both audit reports.

The native audit feeds candidate selection. The independent audit checks the
selected SVG with separate code. Neither establishes semantic truth or replaces
looking at the image.

Deliver the preview with its full-size SVG and retain the model, recipe, and
reports alongside it. State unresolved limitations where they affect use.

## Mermaid path

Use `autonumber` in sequence diagrams with numbered explanations. Give participants
readable aliases. Put message content on arrows and explanatory claims in notes.
Render or parse the diagram with a compatible Mermaid tool, and inspect the result.
If rendering cannot be verified, say so. End the figure with a sentence explaining
what the reader should learn from it.
