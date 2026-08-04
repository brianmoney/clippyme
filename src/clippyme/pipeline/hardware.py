"""Hardware detection: compute device + auto-selected Whisper model size.

Extracted from ``pipeline.main`` so the shared ``DEVICE`` / ``CUDA_AVAILABLE`` /
``WHISPER_MODEL`` state lives in one place that both the transcription and
reframe modules can import without a circular dependency on ``main``. The
detection (including the CUDA-usability probe) runs once at import, exactly as
it did at the top of ``main``.
"""
import os

import torch


def _torch_cuda_usable() -> bool:
    """Whether torch can actually launch kernels on the visible GPU.

    torch.cuda.is_available() is True even for GPUs the installed wheels have no
    kernels for (e.g. Pascal sm_61 vs. a build supporting only sm_75+) — those
    fail at runtime with cudaErrorNoKernelImageForDevice. Compare the device
    capability against the arch list baked into the wheel.
    """
    if not torch.cuda.is_available():
        return False
    arch_list = torch.cuda.get_arch_list()
    if not arch_list:
        return False
    try:
        major, minor = torch.cuda.get_device_capability(0)
    except Exception:
        return False
    return f"sm_{major}{minor}" in arch_list


# Device for torch-backed work (reframe/YOLO). Kept separate from the whisper
# probe below: ctranslate2 ships its own CUDA kernels, so transcription can use
# a GPU torch cannot (e.g. Pascal with a modern wheel).
DEVICE = "cuda" if _torch_cuda_usable() else "cpu"


def _pick_whisper_compute_type() -> str:
    """Whisper compute type for CUDA, defaulting to float16.

    Pascal and older (CC < 7.0) lack efficient FP16 units, which ctranslate2
    rejects outright — fall back to int8 there. Override explicitly via
    WHISPER_COMPUTE_TYPE (e.g. "float32" for a CPU-hostile-quantized source).
    """
    override = os.getenv("WHISPER_COMPUTE_TYPE", "").strip()
    if override:
        return override
    try:
        cc = torch.cuda.get_device_capability(0)  # (major, minor)
        if cc[0] < 7:
            return "int8"
    except Exception:
        pass
    return "float16"


WHISPER_COMPUTE_TYPE = _pick_whisper_compute_type()

# Test if CUDA actually works for faster-whisper (needs libcublas via ctranslate2).
# Creating the model is not enough — libcublas only loads during actual encoding.
CUDA_AVAILABLE = False
GPU_VRAM_GB = 0
if torch.cuda.is_available():
    try:
        from faster_whisper import WhisperModel as _WM
        import numpy as _np
        _m = _WM("tiny", device="cuda", compute_type=WHISPER_COMPUTE_TYPE)
        _dummy = _np.zeros(16000, dtype=_np.float32)
        _m.transcribe(_dummy)
        del _m, _dummy
        CUDA_AVAILABLE = True
        GPU_VRAM_GB = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 1)
        print(f"✅ CUDA runtime verified — GPU {torch.cuda.get_device_name(0)} ({GPU_VRAM_GB}GB VRAM)")
    except Exception as e:
        CUDA_AVAILABLE = False
        print(f"⚠️  CUDA not usable for Whisper: {type(e).__name__} — using CPU")
else:
    print("ℹ️  No CUDA detected — using CPU")

# Auto-select Whisper model based on available hardware
# Models: tiny (39M) < base (74M) < small (244M) < medium (769M) < large-v3 (1.55B)
import psutil as _psutil_check
_total_ram_gb = round(_psutil_check.virtual_memory().total / (1024**3), 1)

if CUDA_AVAILABLE:
    if GPU_VRAM_GB >= 6:
        WHISPER_MODEL = "large-v3"
    elif GPU_VRAM_GB >= 3:
        WHISPER_MODEL = "medium"
    else:
        WHISPER_MODEL = "small"
else:
    if _total_ram_gb >= 16:
        WHISPER_MODEL = "medium"
    elif _total_ram_gb >= 8:
        WHISPER_MODEL = "small"
    else:
        WHISPER_MODEL = "base"

# Allow override via env var
WHISPER_MODEL = os.getenv("WHISPER_MODEL", WHISPER_MODEL)
print(f"🎙️  Whisper model: {WHISPER_MODEL} (auto-selected for {'GPU ' + str(GPU_VRAM_GB) + 'GB' if CUDA_AVAILABLE else 'CPU ' + str(_total_ram_gb) + 'GB RAM'})")
