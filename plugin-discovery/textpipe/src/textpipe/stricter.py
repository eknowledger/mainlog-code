"""Registry with three changes:

1. before importing, check the textpipe requirement the plugin's package declares;
2. compare versions with `packaging`, and refuse other major versions;
3. remember why a plugin was skipped, and say so when someone asks for it.
"""

from packaging.markers import UndefinedComparison, UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from textpipe.errors import PluginIncompatibleError, PluginNotFoundError
from textpipe.registry import GROUP, Registry


def declared_host_requirement(ep):
    """The textpipe version range the plugin's package declares, or None.

    Raises InvalidRequirement, UndefinedComparison or UndefinedEnvironmentName if one of
    the package's requirements can't be parsed or its marker can't be evaluated.
    """
    for line in getattr(ep.dist, "requires", None) or []:
        req = Requirement(line)
        if canonicalize_name(req.name) != "textpipe":
            continue
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue                                  # e.g. only with an optional extra
        return req.specifier
    return None


def check_compatible_strict(cls, host_version):
    """Same major version as the host, and not newer than it."""
    declared, host = Version(cls.api_version), Version(host_version)
    if declared.major != host.major or declared > host:
        raise PluginIncompatibleError(
            f"{cls.__module__}.{cls.__name__} targets textpipe API {cls.api_version}, "
            f"but textpipe {host_version} is installed"
        )


UNREADABLE = (InvalidRequirement, UndefinedComparison, UndefinedEnvironmentName)


class StricterRegistry(Registry):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.skipped = {}                             # (group, name) -> reason

    def _admit(self, group, ep):
        try:
            spec = declared_host_requirement(ep)      # textpipe's range, from ep.dist.requires
        except UNREADABLE as exc:                     # an unparseable line or marker
            self._skip(group, ep.name, f"unreadable requirement ({exc}); not imported")
            return False
        if spec is not None and not spec.contains(self._host_version, prereleases=True):
            self._skip(group, ep.name, f"its package requires textpipe{spec}, and "
                                       f"textpipe {self._host_version} is installed; not imported")
            return False
        return True

    def _check_version(self, cls):
        check_compatible_strict(cls, self._host_version)

    def _skip(self, group, name, reason, verb="skipping plugin"):
        super()._skip(group, name, reason, verb)
        self.skipped[(group, name)] = reason

    def get(self, name, group=GROUP):
        usable = self.available(group)
        if name not in usable and (group, name) in self.skipped:
            raise PluginNotFoundError(
                f"{name!r} is installed but not usable: {self.skipped[(group, name)]}."
            )
        return super().get(name, group)
