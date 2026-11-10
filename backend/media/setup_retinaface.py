"""Register a locally exported RetinaFace ResNet50 raw-output ONNX, without inference."""
import argparse
import hashlib
import json
from pathlib import Path

from backend.media.retinaface import MODEL, model_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--sha256', required=True, help='Expected SHA256 of the reviewed local export')
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    source, target = args.source.resolve(), model_path()
    if not source.is_file() or source.suffix.lower() != '.onnx' or not 0 < source.stat().st_size <= 200*1024*1024:
        parser.error('Use a standalone ONNX file, at most 200 MiB')
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != args.sha256.lower():
        parser.error('Model checksum mismatch')
    manifest = target.with_suffix('.manifest.json')
    if not args.replace and (target.exists() or manifest.exists()):
        parser.error('Model already registered; use --replace to replace intentionally')
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_suffix('.install')
    try:
        staged.write_bytes(content)
        staged.replace(target)
        manifest.write_text(json.dumps({
            'model': MODEL, 'sha256': digest,
            'source': 'https://github.com/biubug6/Pytorch_Retinaface',
            'original_filename': source.name,
            'contract': 'cfg_re50, test phase, static 1x3x640x640, raw loc/conf/landmarks',
            'integrity_note': 'Local checksum; does not authenticate publisher or backbone',
        }, indent=2) + '\n', encoding='utf-8')
    finally:
        staged.unlink(missing_ok=True)
    print('RetinaFace registered. Set PPE_MEDIA_FACE_VERIFIER=retinaface on the GPU worker after validation.')


if __name__ == '__main__':
    main()
