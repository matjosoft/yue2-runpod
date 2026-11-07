"""Operation registry. ``/v1/capabilities`` and job submission are built from it, so a
new operation needs no change to routes or core (W-7)."""

from collections.abc import Iterator
from contextlib import contextmanager

from studio_worker.operations.base import Operation

_REGISTRY: dict[str, Operation] = {}


def register_operation[T: Operation](cls: type[T]) -> type[T]:
    """Class decorator: instantiate the operation and register it under ``cls.name``."""
    if cls.name in _REGISTRY:
        raise ValueError(f"operation {cls.name!r} is already registered")
    _REGISTRY[cls.name] = cls()
    return cls


def unregister_operation(name: str) -> None:
    _REGISTRY.pop(name, None)


def get_operation(name: str) -> Operation | None:
    return _REGISTRY.get(name)


def all_operations() -> list[Operation]:
    return sorted(_REGISTRY.values(), key=lambda op: op.name)


@contextmanager
def registered[T: Operation](cls: type[T]) -> Iterator[type[T]]:
    """Register ``cls`` for the duration of a ``with`` block. For tests (``test.echo``)."""
    register_operation(cls)
    try:
        yield cls
    finally:
        unregister_operation(cls.name)
