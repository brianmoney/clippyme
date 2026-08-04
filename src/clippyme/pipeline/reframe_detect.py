"""ML-bound detection layer for the reframe pipeline (cv2 + YOLO + MediaPipe).

Owns the lazy model singletons (YOLOv8n, MediaPipe FaceDetection/FaceMesh) and
the per-frame detectors. Split out of ``reframe.py`` so the pure tracking
classes (``reframe_track``) stay host-importable; ``reframe.py`` re-exports
these names for back-compat.
"""
import logging
import os
import shutil
import tempfile
import time
import urllib.request

import cv2
import mediapipe as mp
from ultralytics import YOLO

from clippyme.pipeline.hardware import DEVICE

logger = logging.getLogger(__name__)

_YOLO_MODEL_URL = (
    "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt"
)

_yolo_model = None
_yolo_model_failed = False


def _yolo_weights_path() -> str:
    """Resolve a stable, persistent path for the YOLOv8n weights file.

    Priority: ``CLIPPYME_YOLO_PATH`` env override, else a persistent cache
    under ``data/cache/ultralytics``. Never a bare CWD-relative name, so a
    cold download survives across jobs/processes instead of being re-attempted
    (and re-failed) for every clip.
    """
    override = (os.environ.get("CLIPPYME_YOLO_PATH") or "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.abspath(os.path.join("data", "cache", "ultralytics", "yolov8n.pt"))


def _download_weights(url: str, target: str, retries: int = 5) -> bool:
    """Download ``url`` to ``target`` with retry/backoff, atomically.

    Pure stdlib (urllib) so it works regardless of curl availability. The
    payload is written to a unique temp file in the target directory and
    ``os.replace``d into place only when complete, so a partial/interrupted
    download never poisons the cache and concurrent warmers can't corrupt each
    other. Returns True only when the file is on disk and non-empty.
    """
    target_dir = os.path.dirname(target)
    os.makedirs(target_dir, exist_ok=True)
    for attempt in range(1, retries + 1):
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix=".yolov8n-", suffix=".pt.part", dir=target_dir)
            with os.fdopen(fd, "wb") as out, urllib.request.urlopen(url, timeout=60) as resp:
                shutil.copyfileobj(resp, out)
            if os.path.getsize(tmp_path) > 0:
                os.replace(tmp_path, target)
                return True
        except Exception as exc:  # noqa: BLE001 — any network/IO error is retryable
            logger.warning(
                "YOLOv8n weights download attempt %d/%d failed: %s", attempt, retries, exc
            )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        if attempt < retries:
            time.sleep(min(2 ** attempt, 20))
    return False


def ensure_yolo_weights() -> bool:
    """Ensure the YOLOv8n weights file is on disk (download if missing).

    Returns True when the weights are available for YOLO to load. Used to
    pre-warm the persistent cache ahead of rendering so a cold, flaky download
    can't stall a clip mid-render. Also reuses a weights file that a pre-fix
    version of ClippyMe downloaded into the job CWD.
    """
    target = _yolo_weights_path()
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return True
    legacy_cwd = os.path.join(os.getcwd(), "yolov8n.pt")
    if os.path.exists(legacy_cwd) and os.path.getsize(legacy_cwd) > 0:
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(legacy_cwd, target)
            return True
        except OSError:
            pass
    return _download_weights(_YOLO_MODEL_URL, target)


def _get_yolo_model():
    """Lazy-load YOLOv8n on first body-detection call.

    Degrades gracefully: if the weights can't be fetched (offline, flaky
    download) or the model can't be loaded, this logs once and returns None
    instead of raising — callers fall back to face-only tracking / letterbox
    rather than letting the clip render crash. The failure is cached so we
    don't hammer the network on every frame.
    """
    global _yolo_model, _yolo_model_failed
    if _yolo_model is None and not _yolo_model_failed:
        path = _yolo_weights_path()
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            if not ensure_yolo_weights():
                _yolo_model_failed = True
                logger.warning(
                    "YOLOv8n weights unavailable (%s); person-detection fallback disabled", path
                )
                return None
        try:
            _yolo_model = YOLO(path)
            _yolo_model.to(DEVICE)
        except Exception:  # noqa: BLE001
            _yolo_model_failed = True
            logger.exception(
                "Failed to load YOLOv8n from %s; person-detection fallback disabled", path
            )
            return None
    return _yolo_model

mp_face_detection = mp.solutions.face_detection
mp_face_mesh = mp.solutions.face_mesh

_face_detection = None
_face_mesh = None


def _get_face_detection():
    """Lazy-init MediaPipe FaceDetection on first use (avoids ~300ms TFLite
    load at import time on every subprocess, incl. --reframe-only switches)."""
    global _face_detection
    if _face_detection is None:
        _face_detection = mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
    return _face_detection


def _get_face_mesh():
    """Lazy-init MediaPipe FaceMesh on first use (see _get_face_detection)."""
    global _face_mesh
    if _face_mesh is None:
        _face_mesh = mp_face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
    return _face_mesh

# MediaPipe FaceMesh mouth landmark indices (inner lips + corners). Defined here
# because compute_mouth_aspect_ratio was extracted from main.py during the
# refactor; without these the MAR call raised NameError on every frame, silently
# disabling active-speaker selection (the exception was swallowed by the
# corrupt-frame guard → ~37% duplicated frames).
_MOUTH_TOP = 13
_MOUTH_BOTTOM = 14
_MOUTH_LEFT = 78
_MOUTH_RIGHT = 308


def compute_mouth_aspect_ratio(frame_bgr, face_box) -> float | None:
    """
    Crops the face region and runs FaceMesh to extract the Mouth Aspect Ratio
    (vertical mouth opening / horizontal mouth width).

    Returns a normalized MAR in [0, ~1.5] or None if landmarks couldn't be
    extracted (e.g. profile view, occlusion). The absolute value matters less
    than its *variance over time* — a still mouth has near-zero variance, a
    talking mouth oscillates.
    """
    x, y, w, h = face_box
    H, W = frame_bgr.shape[:2]
    # Pad the crop a bit so FaceMesh has context
    pad = int(max(w, h) * 0.2)
    x1 = max(0, int(x - pad))
    y1 = max(0, int(y - pad))
    x2 = min(W, int(x + w + pad))
    y2 = min(H, int(y + h + pad))
    if x2 - x1 < 30 or y2 - y1 < 30:
        return None
    roi = frame_bgr[y1:y2, x1:x2]
    rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
    rgb.flags.writeable = False
    res = _get_face_mesh().process(rgb)
    if not res.multi_face_landmarks:
        return None
    lm = res.multi_face_landmarks[0].landmark
    rh, rw = roi.shape[:2]
    top = (lm[_MOUTH_TOP].x * rw, lm[_MOUTH_TOP].y * rh)
    bot = (lm[_MOUTH_BOTTOM].x * rw, lm[_MOUTH_BOTTOM].y * rh)
    left = (lm[_MOUTH_LEFT].x * rw, lm[_MOUTH_LEFT].y * rh)
    right = (lm[_MOUTH_RIGHT].x * rw, lm[_MOUTH_RIGHT].y * rh)
    mouth_h = ((top[0] - bot[0]) ** 2 + (top[1] - bot[1]) ** 2) ** 0.5
    mouth_w = ((left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2) ** 0.5
    if mouth_w < 1:
        return None
    return mouth_h / mouth_w

def detect_face_candidates(frame):
    """
    Returns list of all detected faces using lightweight FaceDetection.
    """
    height, width, _ = frame.shape
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = _get_face_detection().process(rgb_frame)
    
    candidates = []
    
    if not results.detections:
        return []
        
    for detection in results.detections:
        bboxC = detection.location_data.relative_bounding_box
        x = int(bboxC.xmin * width)
        y = int(bboxC.ymin * height)
        w = int(bboxC.width * width)
        h = int(bboxC.height * height)
        
        candidates.append({
            'box': [x, y, w, h],
            'score': w * h # Area as score
        })
            
    return candidates

def detect_person_yolo(frame):
    """
    Fallback: Detect largest person using YOLO when face detection fails.
    Returns [x, y, w, h] of the person's 'upper body' approximation.
    """
    model = _get_yolo_model()
    if model is None:
        return None
    results = model(frame, verbose=False, classes=[0])  # class 0 is person
    
    if not results:
        return None
        
    best_box = None
    max_area = 0
    
    for result in results:
        boxes = result.boxes
        for box in boxes:
            x1, y1, x2, y2 = [int(i) for i in box.xyxy[0]]
            w = x2 - x1
            h = y2 - y1
            area = w * h
            
            if area > max_area:
                max_area = area
                # Return the full person bbox. SmoothedCameraman.update_target
                # applies the head-zone offset itself when is_person_box=True
                # (y_center = y + h*0.15). Previously we truncated here AND
                # there, which stacked the offsets and aimed above the head.
                best_box = [x1, y1, w, h]
                
    return best_box
