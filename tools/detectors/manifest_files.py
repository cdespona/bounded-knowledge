"""Shared, read-only traversal for build-manifest detectors."""

import os
from pathlib import Path

from landscape_core.safety import directory_exclusion_reason, exclusion_reason


def manifest_files(root: Path, names):
    """Yield safe, regular manifest files in stable path order."""
    found = []
    for current, directory_names, file_names in os.walk(root, topdown=True):
        current_path = Path(current)
        directory_names[:] = [
            name
            for name in sorted(directory_names)
            if not (current_path / name).is_symlink()
            and directory_exclusion_reason((current_path / name).relative_to(root)) is None
        ]
        for file_name in sorted(file_names):
            if file_name not in names:
                continue
            path = current_path / file_name
            relative = path.relative_to(root)
            if path.is_symlink():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if exclusion_reason(relative, size) is None:
                found.append(path)
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())
