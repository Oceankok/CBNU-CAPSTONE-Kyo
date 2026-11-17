"""Copy original source artifacts before acknowledged cleanup; never overwrite."""
import hashlib
import os
from pathlib import Path
import shutil
from uuid import uuid4


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.digest()


def archive_chunk(root, run_id, chunk, *, clock_required, max_bytes):
    root=Path(root)
    if not root.is_absolute():raise ValueError('archive_root_must_be_absolute')
    if type(max_bytes) is not int or max_bytes <= 0:raise ValueError('archive_limit_required')
    destination=root / run_id
    destination.mkdir(parents=True,exist_ok=True)
    originals=[chunk,chunk.with_suffix('.txt')]
    if clock_required:originals.append(chunk.with_suffix('.clock.json'))
    for original in originals:
        if not original.is_file():raise ValueError('archive_source_missing')
        target=destination/original.name
        expected=digest(original)
        if target.exists():
            if not target.is_file() or digest(target)!=expected:raise ValueError('archive_content_mismatch')
            continue
        used=sum(item.stat().st_size for item in root.rglob('*') if item.is_file())
        if used+original.stat().st_size > max_bytes:raise OSError('archive_capacity_exceeded')
        temporary=destination/(original.name+'.'+uuid4().hex+'.tmp')
        try:
            with original.open('rb') as source, temporary.open('xb') as output:
                shutil.copyfileobj(source,output);output.flush();os.fsync(output.fileno())
            if digest(temporary)!=expected:raise ValueError('archive_copy_mismatch')
            # Hard link publishes without replacing a concurrent destination.
            try:
                if os.name=='nt':temporary.rename(target)
                else:os.link(temporary,target)
            except FileExistsError:
                if digest(target)!=expected:raise ValueError('archive_content_mismatch')
        finally:
            temporary.unlink(missing_ok=True)
