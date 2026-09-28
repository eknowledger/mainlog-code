# mainlog-code

The runnable code behind posts on [mainLog](https://mainlog.dev). One folder per post, named by the post's
URL slug, and each folder stands on its own: its own README, its own dependencies at pinned versions, and its own
CI job that runs the code and checks it still prints what the post says it prints.

| Post | Folder | Language |
|---|---|---|
| [Keeping optional Python imports optional](https://mainlog.dev/posts/optional-python-imports/) | [`optional-python-imports/`](optional-python-imports/) | Python |
| [Why a higher sample rate cannot restore lost sound](https://mainlog.dev/posts/sample-rate-cannot-restore-sound/) | [`sample-rate-cannot-restore-sound/`](sample-rate-cannot-restore-sound/) | Python, NumPy, SciPy |
| [Green on my laptop, red in CI](https://mainlog.dev/posts/pytest-plugin-ci-only-failures/) | [`pytest-plugin-ci-only-failures/`](pytest-plugin-ci-only-failures/) | Python, pytest |
| [Making my agent's architecture diagrams worth reading](https://mainlog.dev/posts/agent-architecture-diagrams/) | [`agent-architecture-diagrams/`](agent-architecture-diagrams/) | Python, Node |

A post links its folder at a specific commit, so the code you read in the post is the code you find here, even
after a folder changes. A demo that grows into a project of its own moves to its own repository.

MIT licensed; see [LICENSE](LICENSE). Third-party dependencies keep their own licences, noted in a folder where it
matters.
