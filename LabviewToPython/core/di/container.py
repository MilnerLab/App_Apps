# labapp/core/service_container.py
from __future__ import annotations
from typing import Any, Optional, Type, TypeVar, cast
from threading import RLock

T = TypeVar("T")

class Container:
    _services: dict[type, Any] = {}
    _lock = RLock()

    @classmethod
    def register(cls, key: type[T], instance: T) -> None:
        with cls._lock:
            cls._services[key] = instance

    @classmethod
    def get(cls, key: type[T]) -> T:
        with cls._lock:
            return cast(T, cls._services[key])

    @classmethod
    def try_get(cls, key: type[T]) -> Optional[T]:
        with cls._lock:
            return cast(Optional[T], cls._services.get(key))
