"""The classes plugins subclass."""


class Filter:
    """A text filter. Plugins subclass this and register it as an entry point."""

    api_version = "1.0"  # the textpipe API version this filter was written for

    def apply(self, text: str) -> str:
        raise NotImplementedError


class Casing(Filter):
    """A filter that changes letter case. Used to ask for "any casing filter"."""
