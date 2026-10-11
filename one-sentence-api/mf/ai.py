from .nodes import Node


class VAD(Node):
    pass


class STT(Node):
    def __init__(self, provider=None):
        self.provider = provider


class TTS(Node):
    def __init__(self, provider=None, voice=None):
        self.provider, self.voice = provider, voice


class LLM(Node):
    def __init__(
        self, system_prompt="", tools=(), greeting=None, provider=None, model=None
    ):
        self.system_prompt, self.tools, self.greeting = (
            system_prompt,
            list(tools),
            greeting,
        )
        self.provider, self.model = provider, model


def tool(fn):
    """Make an async function callable by the model. The framework passes ctx first."""
    fn._is_tool = True
    return fn
