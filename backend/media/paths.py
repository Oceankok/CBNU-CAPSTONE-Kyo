import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORAGE_ROOT = PROJECT_ROOT / "storage"
PROCESSED_ROOT = STORAGE_ROOT / "candidate_events" / "processed"
WORK_ROOT = PROJECT_ROOT / ".private_media"
SOURCE_ROOT = WORK_ROOT / "originals"


def private_source_path(key: str) -> Path:
    """Resolve a private original without accepting absolute or escaping paths."""
    relative = Path(key)
    root = SOURCE_ROOT.resolve()
    target = (root / relative).resolve()
    if not key or relative.is_absolute() or target == root or not target.is_relative_to(root):
        raise ValueError("unsafe_source_path")
    return target
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def model_path() -> Path:
    return Path(os.environ.get("PPE_FACE_MODEL_PATH", str(
        PROJECT_ROOT / "models" / "privacy" / "face_detection_yunet_2023mar.onnx"
    )))


def scrfd_model_path() -> Path:
    return Path(os.environ.get("PPE_SCRFD_MODEL_PATH", str(
        PROJECT_ROOT / "models" / "privacy" / "scrfd_10g_bnkps.onnx"
    )))


def owned_path(storage_path: str) -> Path:
    """Resolve only canonical files below storage, never arbitrary input paths."""
    relative = Path(storage_path)
    if relative.is_absolute() or not relative.parts or relative.parts[0] != "storage":
        raise ValueError("unsafe_storage_path")
    target = (PROJECT_ROOT / relative).resolve()
    if not target.is_relative_to(STORAGE_ROOT.resolve()) or target == STORAGE_ROOT.resolve():
        raise ValueError("unsafe_storage_path")
    return target


def storage_key(path: Path) -> str:
    return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
