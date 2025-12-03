from __future__ import annotations
from typing import Callable, Any, DefaultDict, List
from collections import defaultdict
from threading import RLock

from LabviewToPython.core.interfaces.i_eventbus import IEventBus

class EventBus(IEventBus):
    def __init__(self) -> None:
        self._subs: DefaultDict[str, List[Callable[[Any], None]]] = defaultdict(list)
        self._lock = RLock()

    def subscribe(self, topic: str, handler: Callable[[Any], None]) -> None:
        with self._lock:
            self._subs[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Callable[[Any], None]) -> None:
        with self._lock:
            if handler in self._subs.get(topic, []):
                self._subs[topic].remove(handler)

    def publish(self, topic: str, payload: Any| None = None) -> None:
        with self._lock:
            handlers = list(self._subs.get(topic, []))
        for h in handlers:
            try:
                h(payload)
            except Exception:
                pass
