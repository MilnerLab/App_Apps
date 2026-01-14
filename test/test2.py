# AcquireAndDisplay_PySide6_PyQtGraph.py
import sys
import time

import numpy as np
import PySpin
import cv2

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

import pyqtgraph as pg


# -----------------------------
# User settings
# -----------------------------
EXPOSURE_US = 5000.0          # manual exposure in microseconds
TIMEOUT_MS = 1000             # GetNextImage timeout
FORCE_MONO8 = True            # simplest/fastest for display


class CameraWorker(QObject):
    frame_ready = Signal(object)     # emits numpy array
    status = Signal(str)
    finished = Signal()

    def __init__(self, exposure_us: float):
        super().__init__()
        self._exposure_us = float(exposure_us)
        self._running = False

        self._system = None
        self._cam_list = None
        self._cam = None

    @Slot()
    def start(self):
        """Runs in worker thread."""
        try:
            self._running = True
            self._open_camera()
            self._acquire_loop()
        except Exception as e:
            self.status.emit(f"Error: {e}")
        finally:
            self._cleanup()
            self.finished.emit()

    @Slot()
    def stop(self):
        self._running = False

    def _open_camera(self):
        self._system = PySpin.System.GetInstance()
        self._cam_list = self._system.GetCameras()

        if self._cam_list.GetSize() == 0:
            raise RuntimeError("No camera detected. Check USB / Spinnaker install / permissions.")

        self._cam = self._cam_list.GetByIndex(0)
        self._cam.Init()

        nodemap = self._cam.GetNodeMap()
        sNodemap = self._cam.GetTLStreamNodeMap()

        # StreamBufferHandlingMode = NewestOnly  (same spirit as your example) :contentReference[oaicite:1]{index=1}
        node_bh = PySpin.CEnumerationPtr(sNodemap.GetNode("StreamBufferHandlingMode"))
        if PySpin.IsReadable(node_bh) and PySpin.IsWritable(node_bh):
            newest = node_bh.GetEntryByName("NewestOnly")
            if PySpin.IsReadable(newest):
                node_bh.SetIntValue(newest.GetValue())

        # AcquisitionMode = Continuous :contentReference[oaicite:2]{index=2}
        node_am = PySpin.CEnumerationPtr(nodemap.GetNode("AcquisitionMode"))
        if PySpin.IsReadable(node_am) and PySpin.IsWritable(node_am):
            cont = node_am.GetEntryByName("Continuous")
            if PySpin.IsReadable(cont):
                node_am.SetIntValue(cont.GetValue())

        # Manual exposure: ExposureAuto Off + ExposureTime set
        if self._cam.ExposureAuto.GetAccessMode() == PySpin.RW:
            self._cam.ExposureAuto.SetValue(PySpin.ExposureAuto_Off)
            time.sleep(0.05)

        if self._cam.ExposureTime.GetAccessMode() == PySpin.RW:
            exp_min = float(self._cam.ExposureTime.GetMin())
            exp_max = float(self._cam.ExposureTime.GetMax())
            exp_set = max(exp_min, min(exp_max, self._exposure_us))
            self._cam.ExposureTime.SetValue(exp_set)
            self.status.emit(f"ExposureTime set to {exp_set:.1f} µs (min={exp_min:.1f}, max={exp_max:.1f})")
        else:
            self.status.emit("Warning: ExposureTime node not writable.")

        # Force Mono8 for simplest display path
        if FORCE_MONO8 and self._cam.PixelFormat.GetAccessMode() == PySpin.RW:
            try:
                self._cam.PixelFormat.SetValue(PySpin.PixelFormat_Mono8)
            except Exception:
                self.status.emit("Warning: Could not force PixelFormat=Mono8 (continuing).")

        self._cam.BeginAcquisition()
        self.status.emit("Acquisition started.")

    def _acquire_loop(self):
        while self._running:
            img = self._cam.GetNextImage(TIMEOUT_MS)

            if img.IsIncomplete():
                img.Release()
                continue

            frame = img.GetNDArray()  # Mono8 -> HxW uint8
            img.Release()

            # Emit a *copy* to decouple from any internal buffers
            self.frame_ready.emit(frame.copy())

    def _cleanup(self):
        # End acquisition + deinit
        try:
            if self._cam is not None:
                try:
                    self._cam.EndAcquisition()
                except Exception:
                    pass
                try:
                    self._cam.DeInit()
                except Exception:
                    pass
        finally:
            # release references (important with PySpin)
            try:
                if self._cam is not None:
                    del self._cam
            except Exception:
                pass

            try:
                if self._cam_list is not None:
                    self._cam_list.Clear()
            except Exception:
                pass

            try:
                if self._system is not None:
                    self._system.ReleaseInstance()
            except Exception:
                pass

            self._cam = None
            self._cam_list = None
            self._system = None


class MainWindow(QMainWindow):
    def __init__(self, exposure_us: float):
        super().__init__()
        self.setWindowTitle("Blackfly S Live (PySide6 + pyqtgraph)")

        pg.setConfigOptions(imageAxisOrder="row-major")

        # Plot widget
        self._glw = pg.GraphicsLayoutWidget()
        self.setCentralWidget(self._glw)

        self._vb = self._glw.addViewBox(row=0, col=0)
        self._vb.setAspectLocked(True)
        self._img_item = pg.ImageItem()
        self._vb.addItem(self._img_item)

        self.statusBar().showMessage("Starting camera...")

        # Worker thread
        self._thread = QThread(self)
        self._worker = CameraWorker(exposure_us=exposure_us)
        self._worker.moveToThread(self._thread)

        self._worker.frame_ready.connect(self.on_frame)
        self._worker.status.connect(self.statusBar().showMessage)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.started.connect(self._worker.start)
        self._thread.start()

    @Slot(object)
    def on_frame(self, frame: np.ndarray):
        # frame is uint8 HxW (Mono8)
        self._img_item.setImage(frame, autoLevels=False)
        # Optional: auto-range on first frame
        if self._vb.autoRangeEnabled()[0]:
            self._vb.autoRange()

    def closeEvent(self, event):
        # Stop worker and wait for thread to finish
        try:
            if self._worker is not None:
                self._worker.stop()
        except Exception:
            pass

        # Give it a moment to exit cleanly
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(2000)

        super().closeEvent(event)


def adaptive_threshold_and_contours(
    frame: np.ndarray,
    block_size: int = 31,
    C: int = 5,
    method: int = cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
    thresh_type: int = cv2.THRESH_BINARY,
    retrieval_mode: int = cv2.RETR_EXTERNAL,
    approx_method: int = cv2.CHAIN_APPROX_SIMPLE,
):
    """
    Takes a frame (np.ndarray), applies adaptiveThreshold, then finds contours.

    Parameters
    ----------
    frame : np.ndarray
        Input image. Can be grayscale (H,W) or color (H,W,3).
    block_size : int
        Neighborhood size for adaptive thresholding. Must be odd and >= 3.
    C : int
        Constant subtracted from the mean/gaussian weighted mean.
    method : int
        cv2.ADAPTIVE_THRESH_MEAN_C or cv2.ADAPTIVE_THRESH_GAUSSIAN_C.
    thresh_type : int
        cv2.THRESH_BINARY or cv2.THRESH_BINARY_INV.
    retrieval_mode : int
        cv2.RETR_EXTERNAL, cv2.RETR_TREE, etc.
    approx_method : int
        cv2.CHAIN_APPROX_SIMPLE, cv2.CHAIN_APPROX_NONE, etc.

    Returns
    -------
    binary : np.ndarray
        Thresholded binary image (uint8, values 0/255).
    contours : list
        Contours as returned by cv2.findContours.
    hierarchy : np.ndarray | None
        Contour hierarchy (depends on retrieval_mode).
    """
    if frame is None or not isinstance(frame, np.ndarray):
        raise TypeError("frame must be a numpy array.")

    # Convert to grayscale if needed
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    elif frame.ndim == 2:
        gray = frame
    else:
        raise ValueError("frame must be HxW (grayscale) or HxWx3 (BGR).")

    # Ensure uint8 for adaptiveThreshold
    if gray.dtype != np.uint8:
        # Scale/clip safely if it's float or higher bit-depth
        g = gray.astype(np.float32)
        g = np.nan_to_num(g)
        g_min, g_max = float(np.min(g)), float(np.max(g))
        if g_max > g_min:
            g = (g - g_min) * (255.0 / (g_max - g_min))
        gray_u8 = np.clip(g, 0, 255).astype(np.uint8)
    else:
        gray_u8 = gray

    # block_size must be odd and >= 3
    if block_size < 3:
        block_size = 3
    if block_size % 2 == 0:
        block_size += 1

    binary = cv2.adaptiveThreshold(
        gray_u8,
        maxValue=255,
        adaptiveMethod=method,
        thresholdType=thresh_type,
        blockSize=block_size,
        C=C,
    )

    # OpenCV version difference: findContours returns 2 or 3 values
    res = cv2.findContours(binary, retrieval_mode, approx_method)
    if len(res) == 2:
        contours, hierarchy = res
    else:
        _, contours, hierarchy = res

    return binary, contours, hierarchy


def main():
    app = QApplication(sys.argv)
    w = MainWindow(exposure_us=EXPOSURE_US)
    w.resize(900, 700)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
