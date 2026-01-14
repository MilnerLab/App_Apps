import cv2
import numpy as np
from dataclasses import dataclass
from typing import List, Tuple, Optional


@dataclass
class Hit:
    cx: int
    cy: int
    area: int
    roundness: float


@dataclass
class ContourConfig:
    adaptive: bool = False
    threshold_value: int = 1            # used if adaptive == False
    adaptive_blocksize: int = 3            # must be odd >= 3
    adaptive_shift: float = 3.0             # C in OpenCV (C is subtracted). C++ uses -shift.
    contour_min_size: int = 1              # minimum number of points in contour
    contour_min_area: int = 0              # inclusive lower area cut
    contour_max_area: int = 3      # inclusive upper area cut


def threshold_and_extract_hits(
    frame: np.ndarray,
    config: ContourConfig = ContourConfig(),
    composit: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, List[Hit]]:
    """
    Python version of your C++ logic:

      - thresholding: either fixed threshold or adaptive mean threshold
      - findContours: RETR_EXTERNAL, CHAIN_APPROX_SIMPLE
      - for each contour:
          * skip if contour has too few points
          * compute centroid via moments
          * compute area and perimeter
          * apply area bounds + bounds check
          * increment composit[cy, cx] by 1 (uint8 wrap-around like C++)
          * store Hit(cx, cy, area, roundness)

    Parameters
    ----------
    frame : np.ndarray
        Grayscale (H,W) or BGR (H,W,3).
    config : ContourConfig
        Parameters matching the C++ code.
    composit : np.ndarray | None
        Optional uint8 accumulator image of shape (H,W). If None, a new one is created.

    Returns
    -------
    frame_threshold : np.ndarray
        Thresholded binary image (uint8, 0/255).
    composit : np.ndarray
        uint8 accumulator image where composit[cy,cx] += 1 for each accepted contour.
    hits : List[Hit]
        List of extracted hit entries.
    """
    if frame is None or not isinstance(frame, np.ndarray):
        raise TypeError("frame must be a numpy array")

    # --- to grayscale ---
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    elif frame.ndim == 2:
        gray = frame
    else:
        raise ValueError("frame must be HxW (grayscale) or HxWx3 (BGR)")

    # Ensure uint8 (OpenCV thresholding expects 8-bit single channel)
    if gray.dtype != np.uint8:
        g = gray.astype(np.float32)
        g = np.nan_to_num(g)
        gmin, gmax = float(np.min(g)), float(np.max(g))
        if gmax > gmin:
            g = (g - gmin) * (255.0 / (gmax - gmin))
        gray_u8 = np.clip(g, 0, 255).astype(np.uint8)
    else:
        gray_u8 = gray

    height, width = gray_u8.shape[:2]

    # --- composit init ---
    if composit is None:
        composit = np.zeros((height, width), dtype=np.uint8)
    else:
        if composit.shape != (height, width) or composit.dtype != np.uint8:
            raise ValueError("composit must be uint8 with shape (H,W) matching frame")

    # --- Thresholding ---
    if not config.adaptive:
        _, frame_threshold = cv2.threshold(
            gray_u8, config.threshold_value, 255, cv2.THRESH_BINARY
        )
    else:
        block = int(config.adaptive_blocksize)
        if block < 3:
            block = 3
        if block % 2 == 0:
            block += 1

        # C++: adaptiveThreshold(..., MEAN_C, BINARY, blocksize, -shift)
        # Python/OpenCV: threshold = mean - C  (C is subtracted)
        # So C = -shift
        C = -float(config.adaptive_shift)

        frame_threshold = cv2.adaptiveThreshold(
            gray_u8, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, block, C
        )

    # --- Find contours ---
    res = cv2.findContours(frame_threshold,  cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if len(res) == 2:
        contours, _hier = res
    else:
        _img, contours, _hier = res

    hits: List[Hit] = []

    # --- Process contours ---
    for contour in contours:
        if int(contour.shape[0]) < int(config.contour_min_size):
            continue

        m = cv2.moments(contour)
        m00 = m.get("m00", 0.0)
        if m00 == 0.0:
            continue

        cx = int(m["m10"] / m00)
        cy = int(m["m01"] / m00)

        area = float(cv2.contourArea(contour))
        perimeter = float(cv2.arcLength(contour, True))

        # Area cuts (C++: if (area < min || area > max) continue;)
        if area < float(config.contour_min_area) or area > float(config.contour_max_area):
            continue

        # Bounds check
        if cx < 0 or cy < 0 or cx >= width or cy >= height:
            continue

        # composit[cy,cx] += 1 with uint8 wrap-around like C++
        composit[cy, cx] = np.uint8(int(composit[cy, cx]) + 1)

        # C++: roundness = (perimeter^2)/area - 4*pi
        # (Your code uses ~3141.59265359 which looks like 1000*pi; assume you intended pi.)
        # We'll use pi here.
        if area > 0:
            roundness = (perimeter * perimeter) / area - 4.0 * np.pi
        else:
            roundness = float("inf")

        hits.append(Hit(cx=cx, cy=cy, area=int(area), roundness=float(roundness)))

    return frame_threshold, composit, hits
