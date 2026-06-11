from typing import Protocol

from base_core.framework.concurrency.interfaces import ITaskRunner


class ISpectrometerTaskRunner(ITaskRunner, Protocol): ...