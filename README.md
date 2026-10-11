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
| [What's inside one voice packet, and why it rides UDP](https://mainlog.dev/posts/voice-packet/) | [`voice-packet/`](voice-packet/) | Python, PyAV |
| [Late is lost: jitter buffers, concealment and forward error correction](https://mainlog.dev/posts/late-is-lost/) | [`late-is-lost/`](late-is-lost/) | Python, PyAV, NumPy |
| [How two computers find a path: NAT, STUN, TURN and ICE](https://mainlog.dev/posts/find-a-path/) | [`find-a-path/`](find-a-path/) | Python, aioice |
| [Keys, then audio: DTLS, SRTP and one call from cold start](https://mainlog.dev/posts/keys-then-audio/) | [`keys-then-audio/`](keys-then-audio/) | Python, aiortc, pylibsrtp |
| [The receiver talks back: RTCP, round-trip time and bandwidth estimation](https://mainlog.dev/posts/receiver-talks-back/) | [`receiver-talks-back/`](receiver-talks-back/) | Python, aiortc |
| [Where the server sits: mesh, SFU, MCU and the server that is the other end](https://mainlog.dev/posts/where-the-server-sits/) | [`where-the-server-sits/`](where-the-server-sits/) | Python, PyAV, pylibsrtp |
| [SFU, MCU, and the participant that has to decode](https://mainlog.dev/posts/participant-that-decodes/) | [`participant-that-decodes/`](participant-that-decodes/) | Python |
| [When a turn doesn't start with the user](https://mainlog.dev/posts/turn-without-the-user/) and [When the user changes their mind mid-task](https://mainlog.dev/posts/user-changes-their-mind/) | [`turn-without-the-user/`](turn-without-the-user/) | Python |
| [How many concepts does a framework need?](https://mainlog.dev/posts/concept-count/) | [`concept-count/`](concept-count/) | Python, Django, pytest, FastAPI |
| [If you can't explain it in one sentence, it doesn't belong in your API](https://mainlog.dev/posts/one-sentence-api/) | [`one-sentence-api/`](one-sentence-api/) | Python |
| [What plugin discovery actually checks](https://mainlog.dev/posts/plugin-discovery/) | [`plugin-discovery/`](plugin-discovery/) | Python, pip, pytest, Flask, stevedore |

A post links its folder at a specific commit, so the code you read in the post is the code you find here, even
after a folder changes. A demo that grows into a project of its own moves to its own repository.

MIT licensed; see [LICENSE](LICENSE). Third-party dependencies keep their own licences, noted in a folder where it
matters.
