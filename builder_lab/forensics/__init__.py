"""Private, opt-in generation forensics primitives.

Exports are deliberately lazy: the shared app configuration must not import
builder-worker-only dependencies such as Pillow.
"""

from importlib import import_module

__all__ = [
    "ForensicBlob",
    "ForensicEntry",
    "ForensicManifest",
    "ForensicWriteResult",
    "GenerationForensicStorage",
    "GenerationForensicsConfig",
]

_EXPORTS = {
    "ForensicBlob": ("builder_lab.forensics.models", "ForensicBlob"),
    "ForensicEntry": ("builder_lab.forensics.models", "ForensicEntry"),
    "ForensicManifest": ("builder_lab.forensics.models", "ForensicManifest"),
    "ForensicWriteResult": (
        "builder_lab.forensics.models",
        "ForensicWriteResult",
    ),
    "GenerationForensicStorage": (
        "builder_lab.forensics.storage",
        "GenerationForensicStorage",
    ),
    "GenerationForensicsConfig": (
        "builder_lab.forensics.config",
        "GenerationForensicsConfig",
    ),
}


def __getattr__(name: str) -> object:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(name) from error
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
