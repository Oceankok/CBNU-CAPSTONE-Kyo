"""Explicit, user-invoked setup; importing the app never downloads a model."""

import hashlib
import urllib.request

from backend.media.paths import MODEL_SHA256, model_path


def main():
    path = model_path()
    url = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
    with urllib.request.urlopen(url, timeout=60) as response:
        content = response.read(2_000_000)
    if hashlib.sha256(content).hexdigest() != MODEL_SHA256:
        raise RuntimeError("Model checksum mismatch; no file was installed")
    with urllib.request.urlopen(
        "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/LICENSE", timeout=30
    ) as response:
        license_text = response.read(20_000)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".LICENSE.txt").write_bytes(license_text)
    temporary = path.with_suffix(".download")
    temporary.write_bytes(content)
    temporary.replace(path)
    print(f"Installed verified YuNet model: {path}")


if __name__ == "__main__":
    main()
