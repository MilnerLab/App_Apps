from __future__ import annotations

from app_apps.io.spectrometer.events import SpectrumAvailable
from base_core.framework.subprocess.shared_memory.base_protocol import ItemAvailable
from base_core.framework.subprocess.shared_memory_device_service import SharedMemoryDeviceService


class SpectrometerService(SharedMemoryDeviceService):
    """
    Specialises SharedMemoryDeviceService for the SPM-002 spectrometer.

    Subscribes to ItemAvailable events for the "ui" consumer and re-publishes
    them as SpectrumAvailable domain events (slot reference only, no copy).

    Consumers:
      - Subscribe to SpectrumAvailable on the EventBus.
      - Read wavelengths/intensities via buffer.read_spectrum_view(msg.slot).
      - Call svc.ack_slot(msg.slot, msg.item_id, "ui") when done.
    """

    def start(self) -> None:
        super().start()
        self._cleanup.add(
            self._bus.subscribe(ItemAvailable, self._on_item_available)
        )

    def _on_item_available(self, msg: ItemAvailable) -> None:
        if msg.consumer_id != "ui" or msg.buffer_id != self._buffer_id:
            return
        self._bus.publish(SpectrumAvailable(
            slot=msg.slot,
            item_id=msg.item_id,
            timestamp_ns=msg.timestamp_ns,
        ))
