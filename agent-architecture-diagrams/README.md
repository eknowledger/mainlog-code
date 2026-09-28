# Architecture Diagram

From the post [Making my agent's architecture diagrams worth reading](https://mainlog.dev/posts/agent-architecture-diagrams/).

Instructions and local drawing tools accompanying the diagram-skill article.
The agent chooses the explanation; Python and libavoid construct and check the
drawing.

This directory is standalone. It does not read a note vault, contact an internal
service, or import an installed agent package. Give an agent access to `SKILL.md`
through its skill mechanism, or ask it to read that file with your diagram request
and source material.

## Setup

Use Python 3.10 or later, Node.js 20 or later, Pillow, `rsvg-convert`, and loadable
Arial regular and bold fonts. Python 3.12 and Node.js 22 were used for the article.
Fonts and system binaries are not bundled.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci --ignore-scripts
.venv/bin/python scripts/doctor.py
```

On platforms without the POSIX virtual-environment layout, use that environment's
Python executable. Install `rsvg-convert` through your system package manager
(for example, `brew install librsvg` on macOS).

The font resolver accepts `DIAGRAM_FONT_REGULAR` and `DIAGRAM_FONT_BOLD`. They
must name loadable Arial faces, and the SVG renderer must resolve the same family.
Do not bundle or substitute fonts without checking their terms and metrics.

## Run the article example

From this directory, after activating the environment or using its Python:

```sh
python3 scripts/build.py examples/rq-job-path.json --out output/rq --crossings 3 --quick
python3 scripts/audit.py output/rq/rq-job-path.svg --spec examples/rq-job-path.json --crossings 3
python3 scripts/cross_audit.py --strict --max-crossings 3 output/rq/rq-job-path.svg
```

Open `output/rq/rq-job-path.png`. The output also retains the SVG, source,
geometry, recipe, search state, and audit receipt. Inputs and logs belong outside
the build's output directory.

The RQ example depicts ordinary asynchronous jobs from the public documentation
and implementation. It omits retries and dependencies. Read arrows show data
toward its receiver. Outcome records group related result and status structures;
these are not separate Redis servers or a claim about process boundaries.

## Find the implementation

| File | Responsibility |
|---|---|
| [SKILL.md](SKILL.md) | Representation choice, model format, tool workflow, visual review |
| [build.py](scripts/build.py) | Candidate construction, ranking, search, delivery |
| [layout.py](scripts/layout.py) | Input checks, measured placement, routing requests |
| [router.mjs](scripts/router.mjs) | JSON interface to libavoid |
| [emit.py](scripts/emit.py) | Edge-label placement and SVG generation |
| [audit.py](scripts/audit.py) | Source fidelity and SVG geometry checks |
| [cross_audit.py](scripts/cross_audit.py) | Separate implementation of geometry checks |
| [ports.py](scripts/ports.py) | Attachment alignment and spacing |
| [simplify.py](scripts/simplify.py) | Proposals for straighter routes |
| [font_config.py](scripts/font_config.py), [typography.py](scripts/typography.py) | Font loading and measurement |

These are local program interfaces, not network services. Python sends routing
requests to a Node process on standard input and receives JSON on standard output.
The router dependency is fetched during `npm ci`; rendering itself needs no network.

## Checks

```sh
python3 -B -m unittest discover -s scripts -p 'test_*.py'
```

The included tests cover layout and port/score regressions using synthetic data.
The article's source bundle was also exercised from a separate directory with the
RQ example and both auditors. No claim is made about untested operating systems.

## Dependencies

`package-lock.json` pins `libavoid-js` 0.4.5. Its JavaScript and WebAssembly are
installed by npm and are excluded from this source bundle. See
[third-party notices](THIRD_PARTY_NOTICES.md) for the upstream project and license.

## Article figures

`examples/rq-first-pass.mmd` is the single first-pass Mermaid source used in the opening comparison (Mermaid11.17.2, Dagre layout, neutral theme). The second figure adds explicit phase grouping and layout search; it is not a renderer benchmark. `examples/article-sequence.mmd` and `examples/skill-call-map.json` supply the process and component views.
