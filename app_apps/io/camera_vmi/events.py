from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VmiCameraConfigChanged:
    """Published when the user applies new camera_vmi settings."""


@dataclass(frozen=True)
class VmiCameraWorkerStateChanged:
    """Published whenever the camera_vmi handle's WorkerStatus changes.
    Subscribers read handle.state to get the current WorkerStatus."""


@dataclass(frozen=True)
class VmiFrameAvailable:
    """
    Published by CameraService (for camera_vmi) when a new frame slot is ready.

    Broadcast to all registered consumers — each reads the same slot from
    shared memory and acks with their own consumer_id.
    """
    slot: int
    item_id: int
    timestamp_ns: int


@dataclass(frozen=True)
class VmiFrameAck:
    """Published by a consumer after it finishes reading a camera_vmi frame slot."""
    slot: int
    item_id: int
    consumer_id: str
