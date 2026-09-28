# Third-party notices

## libavoid-js

- Version: 0.4.5, pinned in `package-lock.json`.
- Upstream: https://github.com/Aksem/libavoid-js
- Package metadata declares: LGPL-2.1-or-later.
- The upstream license text is at
  https://github.com/Aksem/libavoid-js/blob/master/LICENSE
- The upstream README contains source/build instructions.

The npm-installed library retains its own license. Its generated JavaScript and
WebAssembly are not included in this source archive. `router.mjs` is the adapter
that invokes it.

## Other runtime requirements

Pillow is installed separately from `requirements.txt`. Node.js, Python,
`rsvg-convert`, and fonts are supplied by the user's environment and retain their
respective terms. No font files or system executables are distributed here.

## Public example

The RQ diagram is newly authored from public documentation and code:
https://python-rq.org/docs/ and the `rq/rq` repository revision
`90a67a159ef9fa055c6bde12ee1bb3ebd0440b84`.
It is a diagram input, not copied RQ implementation code.
