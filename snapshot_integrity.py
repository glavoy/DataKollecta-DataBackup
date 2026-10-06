"""Integrity manifests for complete snapshots; no network or restore side effects.
The manifest proves bytes, not clinical completeness or recovery. Coverage and
restore reconciliation are separately reviewed in the Word recovery record.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()

def write_manifest(directory, coverage):
    root = Path(directory).resolve()
    files = []
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Snapshot may not contain symbolic links')
        if path.is_file() and path.name != 'manifest.json':
            files.append({'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size, 'sha256': digest(path)})
    if not files:
        raise ValueError('An empty snapshot cannot be completed')
    manifest = {'format_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'coverage': coverage, 'files': files}
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    verify_manifest(root)
    return manifest

def verify_manifest(directory):
    root = Path(directory).resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format_version') != 1 or not manifest.get('files'):
        raise ValueError('Unsupported or empty snapshot manifest')
    seen = set()
    for item in manifest['files']:
        name = item['path']
        candidate = root / name
        if name in seen or Path(name).is_absolute() or '..' in Path(name).parts or candidate.is_symlink():
            raise ValueError('Unsafe or duplicate snapshot path')
        seen.add(name)
        if not candidate.resolve().is_relative_to(root) or not candidate.is_file():
            raise ValueError('Missing or unsafe snapshot file')
        if candidate.stat().st_size != item['bytes'] or digest(candidate) != item['sha256']:
            raise ValueError('Snapshot file failed integrity verification: ' + name)
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name != 'manifest.json'}
    if actual != seen:
        raise ValueError('Snapshot has unlisted files')
    return manifest
