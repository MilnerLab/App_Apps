from __future__ import annotations

from base_core.framework.events.event_bus import EventBus
from base_core.framework.shm.writer_worker_handle import WriterWorkerHandle
from base_core.ipc.message import OKReply
from oscilloscope.config import ScopeConfig
from oscilloscope.messages import SetScopeConfig

from app_apps.io.oscilloscope.buffer import ScopeBuffer, ScopeMemorySpec
from app_apps.io.oscilloscope.events import TraceAvailable, TraceAck

class OscilloscopeWorkerHandle(WriterWorkerHandle[ScopeBuffer, TraceAvailable, TraceAck]):
    """Main-process handle to the OscilloscopeWorker.

    Owns ScopeBuffer shared memory and the SlotCoordinator.
    """

    WORKER_ID = "oscilloscope"

    def __init__(self, bus: EventBus, spec: ScopeMemorySpec) -> None:
        super().__init__(
            worker_id=self.WORKER_ID,
            bus=bus,
            buffer_cls=ScopeBuffer,
            spec=spec,
            make_available=lambda slot, item_id, ts: TraceAvailable(
                slot=slot, item_id=item_id, timestamp_ns=ts
            ),
            ack_type=TraceAck,
        )

    def set_config(self, config: ScopeConfig) -> None:
        self._request(SetScopeConfig(config=config), self._on_reply)

    def _on_reply(self, reply: OKReply) -> None:
        pass
