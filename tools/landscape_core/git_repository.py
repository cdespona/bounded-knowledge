"""Read-only Git repository inspection."""

from pathlib import Path
import subprocess
from typing import List


class RepositoryError(ValueError):
    """Raised when a source path is not a reproducible Git repository."""


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Git command failed"
        raise RepositoryError(message)
    return result.stdout.strip()


def require_repository_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise RepositoryError("Source path is not a directory: {}".format(resolved))
    root = Path(_git(resolved, "rev-parse", "--show-toplevel")).resolve()
    if root != resolved:
        raise RepositoryError(
            "Source path must be an independent Git repository root: {}".format(resolved)
        )
    return root


def commit_sha(path: Path) -> str:
    return _git(path, "rev-parse", "HEAD")


def remote_url(path: Path) -> str:
    try:
        return _git(path, "remote", "get-url", "origin")
    except RepositoryError:
        return ""


def dirty_paths(path: Path) -> List[str]:
    output = _git(path, "status", "--porcelain", "--untracked-files=all")
    paths = []
    for line in output.splitlines():
        if not line:
            continue
        paths.append(line[3:] if len(line) > 3 else line)
    return sorted(paths)

