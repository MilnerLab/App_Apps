import time
import numpy as np
import matplotlib.pyplot as plt
import PySpin


def main(exposure_us: float = 5000.0, n_frames: int = 200):
    system = PySpin.System.GetInstance()
    cams = system.GetCameras()

    if cams.GetSize() == 0:
        cams.Clear()
        system.ReleaseInstance()
        raise RuntimeError("No camera detected.")

    cam = cams.GetByIndex(0)

    try:
        cam.Init()

        # --- Set manual exposure (µs) ---
        if cam.ExposureAuto.GetAccessMode() == PySpin.RW:
            cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)
        time.sleep(0.05)

        exp_min = float(cam.ExposureTime.GetMin())
        exp_max = float(cam.ExposureTime.GetMax())
        exp_set = max(exp_min, min(exp_max, float(exposure_us)))
        cam.ExposureTime.SetValue(exp_set)
        print(f"ExposureTime set to {exp_set:.1f} µs")

        # Optional but helps for simple plotting (fast grayscale)
        if cam.PixelFormat.GetAccessMode() == PySpin.RW:
            try:
                cam.PixelFormat.SetValue(PySpin.PixelFormat_Mono8)
            except Exception:
                pass

        cam.BeginAcquisition()

        plt.ion()
        fig, ax = plt.subplots()
        im = None

        for i in range(n_frames):
            img = cam.GetNextImage(1000)  # timeout ms

            if img.IsIncomplete():
                img.Release()
                continue

            frame = img.GetNDArray()  # Mono8 -> HxW uint8
            img.Release()

            if im is None:
                im = ax.imshow(frame, cmap="gray", vmin=0, vmax=255)
                ax.set_title("Live (close window to stop)")
                plt.show(block=False)
            else:
                im.set_data(frame)

            fig.canvas.draw_idle()
            plt.pause(0.001)

            # If user closes the window, stop the loop
            if not plt.fignum_exists(fig.number):
                break

        cam.EndAcquisition()
        plt.ioff()
        plt.show()

    finally:
        try:
            cam.DeInit()
        except Exception:
            pass
        del cam
        cams.Clear()
        system.ReleaseInstance()


if __name__ == "__main__":
    # Change these as you like:
    main(exposure_us=5000.0, n_frames=300)
