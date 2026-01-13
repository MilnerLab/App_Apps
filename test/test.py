#!/usr/bin/env python3
"""
Live stream viewer for a Teledyne FLIR / Point Grey Blackfly S using PySpin + OpenCV.

Requirements:
  pip install numpy opencv-python
  (PySpin must be installed and the Spinnaker SDK must be installed on the system.)

Controls:
  - Press 'q' or ESC to quit
"""

import time
import argparse

import cv2
import numpy as np
import PySpin


def _try_set_enum(nodemap, node_name: str, entry_name: str) -> bool:
    """Try to set an enumeration node to a given entry; return True on success."""
    node = PySpin.CEnumerationPtr(nodemap.GetNode(node_name))
    if not PySpin.IsAvailable(node) or not PySpin.IsWritable(node):
        return False
    entry = node.GetEntryByName(entry_name)
    if not PySpin.IsAvailable(entry) or not PySpin.IsReadable(entry):
        return False
    node.SetIntValue(entry.GetValue())
    return True


def _try_set_float(cam, prop_name: str, value: float) -> bool:
    """Try to set a float node on the camera; return True on success."""
    node = getattr(cam, prop_name, None)
    if node is None or node.GetAccessMode() != PySpin.RW:
        return False
    vmin = float(node.GetMin())
    vmax = float(node.GetMax())
    node.SetValue(max(vmin, min(vmax, float(value))))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exposure-us", type=float, default=5000.0, help="Exposure time in microseconds (default: 5000)")
    ap.add_argument("--auto-exposure", action="store_true", help="Keep auto exposure on (default: off)")
    ap.add_argument("--color", action="store_true", help="Convert to BGR8 for color display (slower)")
    ap.add_argument("--window", default="Blackfly S Stream (q/ESC to quit)")
    args = ap.parse_args()

    system = PySpin.System.GetInstance()
    cam_list = system.GetCameras()

    if cam_list.GetSize() == 0:
        cam_list.Clear()
        system.ReleaseInstance()
        raise RuntimeError("No camera detected. Check USB connection and Spinnaker installation.")

    cam = cam_list.GetByIndex(0)

    processor = PySpin.ImageProcessor()
    processor.SetColorProcessing(PySpin.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR)

    try:
        cam.Init()

        # --- Stream buffer handling (drop old frames, keep newest) ---
        try:
            tl_stream_nodemap = cam.GetTLStreamNodeMap()
            _try_set_enum(tl_stream_nodemap, "StreamBufferHandlingMode", "NewestOnly")
        except Exception:
            pass  # not critical

        # --- Acquisition mode: Continuous ---
        _try_set_enum(cam.GetNodeMap(), "AcquisitionMode", "Continuous")

        # --- Exposure control ---
        if not args.auto_exposure:
            if cam.ExposureAuto.GetAccessMode() == PySpin.RW:
                cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)
            time.sleep(0.05)
            if not _try_set_float(cam, "ExposureTime", args.exposure_us):
                print("Warning: Could not set ExposureTime (node not writable).")
        else:
            if cam.ExposureAuto.GetAccessMode() == PySpin.RW:
                cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Continuous)

        # --- Try to force a convenient pixel format (optional) ---
        # Mono8 is fastest for display. For color, we will convert anyway.
        if cam.PixelFormat.GetAccessMode() == PySpin.RW and not args.color:
            try:
                cam.PixelFormat.SetValue(PySpin.PixelFormat_Mono8)
            except Exception:
                pass

        cam.BeginAcquisition()

        last_t = time.time()
        frames = 0
        fps = 0.0

        while True:
            image = cam.GetNextImage(1000)  # timeout ms

            if image.IsIncomplete():
                image.Release()
                continue

            # Convert to a displayable format
            if args.color:
                img_conv = processor.Convert(image, PySpin.PixelFormat_BGR8)
                frame = img_conv.GetNDArray()  # HxWx3 uint8 (BGR)
            else:
                # Prefer Mono8 for speed; if camera isn't Mono8, convert.
                if image.GetPixelFormat() != PySpin.PixelFormat_Mono8:
                    img_conv = processor.Convert(image, PySpin.PixelFormat_Mono8)
                    frame = img_conv.GetNDArray()  # HxW uint8
                else:
                    frame = image.GetNDArray()

            image.Release()

            # FPS counter
            frames += 1
            now = time.time()
            if now - last_t >= 1.0:
                fps = frames / (now - last_t)
                frames = 0
                last_t = now

            # Overlay FPS
            if frame.ndim == 2:
                disp = frame
                cv2.putText(disp, f"{fps:5.1f} FPS", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, 255, 2)
            else:
                disp = frame
                cv2.putText(disp, f"{fps:5.1f} FPS", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

            cv2.imshow(args.window, disp)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):  # ESC or q
                break

        cam.EndAcquisition()
        cv2.destroyAllWindows()
        return 0

    finally:
        try:
            cam.DeInit()
        except Exception:
            pass
        del cam
        cam_list.Clear()
        system.ReleaseInstance()


if __name__ == "__main__":
    raise SystemExit(main())
