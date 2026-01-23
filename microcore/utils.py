from pkgutil import resolve_name
from typing import Any


def file_link(path: str) -> str:
    return str(path)


def resolve_callable(path: Any):
    """Resolve a callable from a string like 'module.sub.func' or return
    the callable if it's already one.
    """
    if callable(path):
        return path
    if isinstance(path, str):
        module_name, func_name = path.rsplit('.', 1)
        module = __import__(module_name, fromlist=[func_name])
        return getattr(module, func_name)
    raise ValueError("Unsupported callable spec")
