"""Safety filters shared by deterministic detectors."""

from pathlib import Path
from typing import Optional


EXCLUDED_DIRECTORIES = {
    ".git",
    ".gradle",
    ".idea",
    ".terraform",
    "build",
    "dist",
    "generated",
    "node_modules",
    "out",
    "target",
    "vendor",
}

SENSITIVE_EXACT_NAMES = {
    ".env",
    "kubeconfig",
    "credentials",
    "credentials.json",
}

SENSITIVE_SUFFIXES = (
    ".jks",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".tfstate",
)

MAX_FILE_BYTES = 2 * 1024 * 1024


def exclusion_reason(relative_path: Path, size: Optional[int] = None) -> Optional[str]:
    parts = relative_path.parts
    if any(part in EXCLUDED_DIRECTORIES for part in parts[:-1]):
        return "excluded-directory"

    name = relative_path.name.lower()
    if name in SENSITIVE_EXACT_NAMES or name.startswith(".env."):
        return "sensitive-file"
    if name.endswith(SENSITIVE_SUFFIXES) or ".tfstate." in name:
        return "sensitive-file"
    if (name.startswith("secret") or name.endswith("secret.yaml") or name.endswith("secret.yml")):
        return "potential-secret-file"
    if size is not None and size > MAX_FILE_BYTES:
        return "file-too-large"
    return None


def copilot_configuration_files(root: Path):
    candidates = [
        root / "AGENTS.md",
        root / "CLAUDE.md",
        root / ".github" / "copilot-instructions.md",
    ]
    for directory in (root / ".github" / "agents", root / ".github" / "skills"):
        if directory.is_dir():
            candidates.extend(path for path in directory.rglob("*") if path.is_file())
    return sorted(path.relative_to(root).as_posix() for path in candidates if path.is_file())

