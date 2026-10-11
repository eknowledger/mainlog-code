import asyncio
from types import SimpleNamespace

from .ai import LLM
from .endpoint import Endpoint


class Session:
    """Runs a pipeline. This stand-in prints the graph and fakes one model turn."""

    def __init__(self, pipeline):
        self.pipeline = pipeline

    def run(self):
        print(
            "[session] graph:",
            " >> ".join(
                [repr(self.pipeline.edges[0][0])]
                + [repr(b) for _, b in self.pipeline.edges]
            ),
        )
        llm = next(b for _, b in self.pipeline.edges if isinstance(b, LLM))
        ep = next(
            n.owner for e in self.pipeline.edges for n in e if hasattr(n, "owner")
        )
        if llm.greeting:
            print(f"[agent] says: {llm.greeting}")
        ctx = SimpleNamespace(endpoint=ep, session=self)
        result = asyncio.run(
            llm.tools[0](ctx, destination="+15550100")
        )  # the model calls a tool
        print(f"[agent] tool result: {result}")
