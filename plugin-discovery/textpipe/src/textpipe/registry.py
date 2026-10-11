"""Plugin discovery: find, import, check the version, check the name."""

import logging
from importlib.metadata import entry_points

import textpipe
from textpipe.errors import (
    AmbiguousPluginError,
    PluginCollisionError,
    PluginIncompatibleError,
    PluginNotFoundError,
)
from textpipe.versions import parse_version_prefix

log = logging.getLogger("textpipe.plugins")

GROUP = "textpipe.filters"


def check_compatible(cls, host_version):
    """Refuse a plugin that targets a newer API than this host provides."""
    if parse_version_prefix(cls.api_version) > parse_version_prefix(host_version):
        raise PluginIncompatibleError(
            f"{cls.__module__}.{cls.__name__} targets textpipe API {cls.api_version}, "
            f"but textpipe {host_version} is installed"
        )


def collision_message(group, name, first, second):
    (pkg_a, cls_a), (pkg_b, cls_b) = sorted([first, second], key=lambda claim: claim[0])
    return (
        f"Two packages register {name!r} in {group!r}: "
        f"{pkg_a} ({cls_a.__module__}:{cls_a.__name__}) and "
        f"{pkg_b} ({cls_b.__module__}:{cls_b.__name__}). "
        f"Rename the entry point in one of them, or uninstall one."
    )


class Registry:
    """Finds the plugins installed for a group, and hands them out by name or kind.

    `loader` is where entry points come from: importlib.metadata by default, stand-ins in tests.
    """

    def __init__(self, loader=entry_points, host_version=textpipe.__version__):
        self._loader = loader
        self._host_version = host_version
        self._cache = {}                              # group -> {name: class}

    def available(self, group=GROUP):
        """{plugin name: class} for every usable plugin in the group."""
        if group not in self._cache:
            self._cache[group] = self._discover(group)  # if this raises, nothing is cached
        return self._cache[group]

    def _discover(self, group):
        claims = {}                                   # name -> (package, class)
        for ep in self._loader(group=group):          # 1. find: read installed metadata
            if not self._admit(group, ep):
                continue
            try:
                cls = ep.load()                       # 2. import: runs the plugin's module
                self._check_version(cls)              # 3. version
            except PluginIncompatibleError as exc:
                self._skip(group, ep.name, str(exc))
                continue
            except Exception as exc:
                self._skip(group, ep.name, repr(exc), verb="could not import plugin")
                continue
            package = getattr(ep.dist, "name", None)  # 4. name: which package claims it
            earlier = claims.get(ep.name)
            if earlier and earlier[0] and package and earlier[0] != package:
                raise PluginCollisionError(
                    collision_message(group, ep.name, earlier, (package, cls)))
            claims[ep.name] = (package, cls)
        return {name: cls for name, (_package, cls) in claims.items()}

    def _admit(self, group, ep):
        """A check that needs only metadata, before anything is imported. None here."""
        return True

    def _check_version(self, cls):
        check_compatible(cls, self._host_version)

    def _skip(self, group, name, reason, verb="skipping plugin"):
        log.warning("%s %r: %s", verb, name, reason)

    def get(self, name, group=GROUP):
        """The plugin registered under this name."""
        usable = self.available(group)
        if name not in usable:
            raise PluginNotFoundError(
                f"No usable plugin named {name!r} in {group!r} "
                f"(usable: {', '.join(sorted(usable)) or 'none'})."
            )
        return usable[name]

    def select(self, kind, *, pin=None, group=GROUP):
        """The one usable plugin of this kind, or the one the application pinned."""
        candidates = {n: c for n, c in self.available(group).items() if issubclass(c, kind)}
        if pin is not None:
            if pin in candidates:
                return candidates[pin]
            raise PluginNotFoundError(
                f"No usable {kind.__name__} named {pin!r} "
                f"(candidates: {', '.join(sorted(candidates)) or 'none'})."
            )
        if len(candidates) == 1:
            return next(iter(candidates.values()))
        if not candidates:
            raise PluginNotFoundError(f"No usable plugin is a {kind.__name__}.")
        raise AmbiguousPluginError(
            f"{len(candidates)} plugins are a {kind.__name__}: {', '.join(sorted(candidates))}. "
            f"Pin one: select({kind.__name__}, pin=...)."
        )
