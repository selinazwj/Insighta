"""Compatibility exports for modules whose implementation has been split.

New code imports the owning feature module directly.  These aliases keep older
scripts and ``unittest.mock.patch`` targets working without a shared execution
namespace, source loading, or duplicated implementations.
"""

from types import ModuleType
import sys


_MISSING = object()


class _CompatibilityModule(ModuleType):
    """Forward legacy attributes to their single implementation owner."""

    def __getattribute__(self, name):
        namespace = ModuleType.__getattribute__(self, "__dict__")
        if name == "__dict__":
            # Inspectors such as unittest.mock read __dict__ before getattr.
            # A canonical module may have changed since this facade was installed;
            # expose its current values so a nested legacy patch restores them.
            for exported_name, (owner, attribute) in namespace.get("_compat_bindings", {}).items():
                try:
                    namespace[exported_name] = getattr(owner, attribute)
                except AttributeError:
                    namespace.pop(exported_name, None)
            return namespace
        binding = namespace.get("_compat_bindings", {}).get(name)
        if binding is not None:
            owner, attribute = binding
            return getattr(owner, attribute)
        return ModuleType.__getattribute__(self, name)

    def __setattr__(self, name, value):
        namespace = ModuleType.__getattribute__(self, "__dict__")
        binding = namespace.get("_compat_bindings", {}).get(name)
        if binding is not None:
            owner, attribute = binding
            previous = getattr(owner, attribute, _MISSING)
            setattr(owner, attribute, value)
            for consumer in namespace.get("_compat_alias_consumers", {}).get(name, ()):
                if consumer.__dict__.get(name, _MISSING) is previous:
                    setattr(consumer, name, value)
        ModuleType.__setattr__(self, name, value)

    def __delattr__(self, name):
        namespace = ModuleType.__getattribute__(self, "__dict__")
        binding = namespace.get("_compat_bindings", {}).get(name)
        if binding is not None:
            owner, attribute = binding
            previous = getattr(owner, attribute, _MISSING)
            delattr(owner, attribute)
            for consumer in namespace.get("_compat_alias_consumers", {}).get(name, ()):
                if consumer.__dict__.get(name, _MISSING) is previous:
                    delattr(consumer, name)
        ModuleType.__delattr__(self, name)


def install_compatibility_exports(module_name, bindings, consumers=()):
    """Expose ``{legacy_name: (owner_module, owner_attribute)}`` bindings.

    ``consumers`` contains implementation modules with direct imported aliases;
    only aliases that still reference the same value are updated by a patch.
    Function globals remain independent, normal Python module namespaces.
    """
    module = sys.modules[module_name]
    namespace = module.__dict__
    namespace["_compat_bindings"] = dict(bindings)
    namespace["_compat_consumers"] = tuple(consumers)
    namespace["_compat_alias_consumers"] = {
        name: tuple(
            consumer for consumer in namespace["_compat_consumers"]
            if consumer is not owner
            and consumer.__dict__.get(name, _MISSING) is getattr(owner, attribute)
        )
        for name, (owner, attribute) in bindings.items()
    }
    for name, (owner, attribute) in bindings.items():
        namespace[name] = getattr(owner, attribute)
    namespace["__all__"] = [name for name in bindings if not name.startswith("_")]
    module.__class__ = _CompatibilityModule
