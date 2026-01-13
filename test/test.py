#!/usr/bin/env python3
import sys
import time

import numpy as np
import cv2
import PySpin


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def main(exposure_us: float = 5000.0, save_path: str | None = None) -> int:
    system = PySpin.System.GetInstance()
    cam_list = system.GetCameras()

    if cam_list.GetSize() == 0:
        cam_list.Clear()
        system.ReleaseInstance()
        raise RuntimeError("No camera detected. (Check USB / Spinnaker install / permissions.)")

    cam = cam_list.GetByIndex(0)

    try:
        cam.Init()

        # --- Exposure: turn auto off, then set manual exposure (unit: microseconds) ---
        if cam.ExposureAuto.GetAccessMode() == PySpin.RW:
            cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)

        # Some cameras need a short moment until auto is really disabled
        time.sleep(0.05)

        exp_min = float(cam.ExposureTime.GetMin())
        exp_max = float(cam.ExposureTime.GetMax())
        exp_set = clamp(float(exposure_us), exp_min, exp_max)

        if cam.ExposureTime.GetAccessMode() != PySpin.RW:
            raise RuntimeError("ExposureTime node is not writable (AccessMode != RW).")

        cam.ExposureTime.SetValue(exp_set)
        print(f"ExposureTime set to {exp_set:.1f} µs (min={exp_min:.1f}, max={exp_max:.1f})")

        # --- Acquisition Mode: Single Frame ---
        if cam.AcquisitionMode.GetAccessMode() == PySpin.RW:
            cam.AcquisitionMode.SetValue(PySpin.AcquisitionMode_SingleFrame)

        # Optional: force PixelFormat to Mono8 (most robust for display)
        if cam.PixelFormat.GetAccessMode() == PySpin.RW:
            cam.PixelFormat.SetValue(PySpin.PixelFormat_Mono8)

        cam.BeginAcquisition()

        # Timeout in ms
        image = cam.GetNextImage(2000)

        if image.IsIncomplete():
            status = image.GetImageStatus()
            image.Release()
            raise RuntimeError(f"Incomplete image (status={status}).")

        # Convert to NumPy array (Mono8 -> HxW uint8)
        frame = image.GetNDArray()
        image.Release()

        cam.EndAcquisition()

        # --- Display ---
        window_title = "Blackfly S (press any key)"
        cv2.imshow(window_title, frame)

        if save_path:
            cv2.imwrite(save_path, frame)
            print(f"Saved: {save_path}")

        cv2.waitKey(0)
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
    # Usage:
    #   python blackfly_one_frame.py 8000
    #   python blackfly_one_frame.py 8000 out.png
    exposure = float(sys.argv[1]) if len(sys.argv) >= 2 else 5000.0
    out = sys.argv[2] if len(sys.argv) >= 3 else None
    raise SystemExit(main(exposure_us=exposure, save_path=out))
