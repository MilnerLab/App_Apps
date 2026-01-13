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
        raise RuntimeError("Keine Kamera gefunden. (USB/Spinnaker/Permissions prüfen)")

    cam = cam_list.GetByIndex(0)

    try:
        cam.Init()
        nodemap = cam.GetNodeMap()

        # --- Exposure: Auto aus, dann manuell setzen (Einheit: µs) ---
        if cam.ExposureAuto.GetAccessMode() == PySpin.RW:
            cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)

        # Manche Kameras brauchen kurz, bis Auto wirklich aus ist
        time.sleep(0.05)

        exp_min = float(cam.ExposureTime.GetMin())
        exp_max = float(cam.ExposureTime.GetMax())
        exp_set = clamp(float(exposure_us), exp_min, exp_max)

        if cam.ExposureTime.GetAccessMode() != PySpin.RW:
            raise RuntimeError("ExposureTime Node ist nicht beschreibbar (AccessMode != RW).")

        cam.ExposureTime.SetValue(exp_set)
        print(f"ExposureTime gesetzt auf {exp_set:.1f} µs (min={exp_min:.1f}, max={exp_max:.1f})")

        # --- Acquisition Mode: Single Frame ---
        if cam.AcquisitionMode.GetAccessMode() == PySpin.RW:
            cam.AcquisitionMode.SetValue(PySpin.AcquisitionMode_SingleFrame)

        # Optional: PixelFormat auf Mono8 erzwingen (robust für Anzeige)
        if cam.PixelFormat.GetAccessMode() == PySpin.RW:
            # Wenn du eine Farbkamera hast und Farbe willst, nimm später BGR8 Konvertierung
            cam.PixelFormat.SetValue(PySpin.PixelFormat_Mono8)

        cam.BeginAcquisition()

        # Timeout in ms
        image = cam.GetNextImage(2000)

        if image.IsIncomplete():
            status = image.GetImageStatus()
            image.Release()
            raise RuntimeError(f"Incomplete image (status={status}).")

        # Bild nach NumPy
        frame = image.GetNDArray()  # bei Mono8: HxW uint8
        image.Release()
        cam.EndAcquisition()

        # --- Anzeige ---
        win = "Blackfly S (press any key)"
        cv2.imshow(win, frame)
        if save_path:
            cv2.imwrite(save_path, frame)
            print(f"Gespeichert: {save_path}")
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
