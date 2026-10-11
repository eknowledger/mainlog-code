"""textpipe: a tiny host that runs text filters supplied by plugins."""

from importlib.metadata import version

__version__ = version("textpipe")

from textpipe.base import Casing, Filter  # noqa: E402

__all__ = ["Casing", "Filter", "__version__"]
