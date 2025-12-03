from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Callable

class IEventBus(ABC):
    @abstractmethod
    def publish(self, topic: str, payload: Any | None = None) -> None: ...
    @abstractmethod
    def subscribe(self, topic: str, handler: Callable[[Any], None]) -> object: ...
    @abstractmethod
    def unsubscribe(self, topic: str, handler: Callable[[Any], None]) -> None: ...