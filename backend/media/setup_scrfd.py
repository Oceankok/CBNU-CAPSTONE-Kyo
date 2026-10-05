"""Explicitly register a locally downloaded official SCRFD 10G ONNX model.

No network requests, inference, dependency installation or DB changes.
The manifest records local integrity, not independent publisher authentication.
"""

import argparse
import hashlib
import json
from pathlib import Path

from backend.media.paths import scrfd_model_path

SOURCE = "https://github.com/deepinsight/insightface/tree/master/detection/scrfd#pretrained-models"
LICENSE_URL = "https://github.com/deepinsight/insightface#license"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Extracted official SCRFD_10G or SCRFD_10G_KPS ONNX")
    parser.add_argument("--noncommercial-research", action="store_true", required=True,
                        help="Acknowledge pretrained weights are for non-commercial research only")
    parser.add_argument("--replace", action="store_true", help="Replace an existing locally registered model")
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file() or source.suffix.lower() != ".onnx" or not 0 < source.stat().st_size <= 50 * 1024 * 1024:
        parser.error("Use the extracted SCRFD 10G ONNX file (maximum 50 MiB), not an archive")
    target = scrfd_model_path()
    manifest_path = target.with_suffix(".manifest.json")
    if not args.replace and (target.exists() or manifest_path.exists()):
        parser.error("A model is already registered; use --replace only to intentionally replace it")
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    manifest = {
        "model": "scrfd-10g", "sha256": digest,
        "source": SOURCE, "original_filename": source.name,
        "license_url": LICENSE_URL,
        "weight_usage": "non-commercial research only",
        "integrity_note": "Locally recorded checksum; source authenticity must be checked when downloading",
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".install")
    try:
        temporary.write_bytes(content)
        temporary.replace(target)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        target.with_suffix(".LICENSE.txt").write_text(
            "InsightFace source code: MIT. Pretrained weights: non-commercial research only.\n"
            "This file summarizes the upstream policy; it does not grant additional rights.\n"
            f"Official policy: {LICENSE_URL}\nOfficial SCRFD source: {SOURCE}\n",
            encoding="utf-8",
        )
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Registered SCRFD 10G: {target}\nSHA-256: {digest}\nNo inference was run.")


if __name__ == "__main__":
    main()
