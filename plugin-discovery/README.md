# textpipe: what plugin discovery actually checks

From the post [What plugin discovery actually checks](https://mainlog.dev/posts/plugin-discovery/).

A tiny plugin host and five one-file plugins that reproduce every discovery
outcome in the article except the source-checkout case: a plugin that works, one whose import fails, one written
for a newer host (with import-time code), two packages that claim the same name,
and two plugins that both qualify for one request.

```text
textpipe/                     the host
  src/textpipe/registry.py    discovery: find, import, version, name; get(); select()
  src/textpipe/versions.py    the version comparison (leading numbers, as a tuple)
  src/textpipe/stricter.py    the same loop with the three changes from the article
  src/textpipe/base.py        Filter and Casing, the classes plugins subclass
  src/textpipe/errors.py
plugins/
  textpipe-shout/             shout: works
  textpipe-caps/              caps: works, and is also a Casing filter
  textpipe-loud/              registers the name shout as well
  textpipe-future/            summarize: targets API 2.0, prints on import
  textpipe-broken/            translate: imports a module that isn't installed
  textpipe-shout-0.2/         a second copy of textpipe-shout, for the PYTHONPATH case
demo/trace.py                 runs discovery and prints what happened
demo/three_checks.py          three checks to run against a registry, with stand-in entry points
export_runs.py                runs every scenario and writes runs.json, which the post's lab replays
tests/test_discovery.py       each outcome, with stand-in entry points
run_scenarios.sh              builds everything and runs each scenario in a fresh venv
real_hosts/run.sh             the same failures in pytest, Flask and stevedore (pinned versions)
```

Run every scenario, then the tests (Python 3.12 was used for the article):

```sh
sh run_scenarios.sh /tmp/textpipe-work
python -m venv .venv && . .venv/bin/activate
pip install packaging pytest /tmp/textpipe-work/dist/textpipe-1.4-py3-none-any.whl
pytest tests
```

Installing the built wheel, rather than `pip install ./textpipe`, keeps pip from
building inside the source tree, which would leave `build/` and `*.egg-info` behind:
the stale-metadata trap from the article.

The tests don't install any plugins: they hand the registry stand-in entry
points, so you can check a loader's decisions without touching installed metadata.

The pytest / Flask / stevedore table in the article comes from:

```sh
sh real_hosts/run.sh /tmp/real-hosts-work
```

It builds its own small plugins and pins pytest 9.1.1, pluggy 1.6.0, Flask 3.1.3,
click 8.5.0 and stevedore 5.9.1.
