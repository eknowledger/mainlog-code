"""Errors raised by plugin discovery and selection."""


class PluginError(Exception):
    """Base class for every plugin error."""


class PluginIncompatibleError(PluginError):
    """A plugin targets a textpipe API this host doesn't provide."""


class PluginCollisionError(PluginError):
    """Two packages register the same plugin name in the same group."""


class AmbiguousPluginError(PluginError):
    """Several plugins satisfy a request and the application didn't pin one."""


class PluginNotFoundError(PluginError, KeyError):
    """No usable plugin matches the request. Also a KeyError, for dict-style callers."""

    def __str__(self) -> str:
        # KeyError would print the message in quotes; show it as written.
        return self.args[0] if self.args else ""
