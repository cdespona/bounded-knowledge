"""Safety filters shared by deterministic detectors."""

import os
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

COPILOT_CONFIGURATION_DIRECTORIES = (
    ".github/agents",
    ".github/instructions",
    ".github/skills",
    ".claude/agents",
    ".claude/skills",
    ".agents/skills",
)

COPILOT_INSTRUCTION_NAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
}


def directory_exclusion_reason(relative_path: Path) -> Optional[str]:
    """Return an exclusion reason for a directory path, accounting for source packages."""
    parts = relative_path.parts
    for index, part in enumerate(parts):
        if part not in EXCLUDED_DIRECTORIES:
            continue
        if part == "out" and any(
            parts[position : position + 2] in (("src", "main"), ("src", "test"))
            for position in range(index)
        ):
            continue
        return "excluded-directory"
    return None


def exclusion_reason(relative_path: Path, size: Optional[int] = None) -> Optional[str]:
    directory_reason = directory_exclusion_reason(relative_path.parent)
    if directory_reason is not None:
        return directory_reason

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
    candidates = {root / ".github" / "copilot-instructions.md"}
    for current, directory_names, file_names in os.walk(root, topdown=True):
        current_path = Path(current)
        directory_names[:] = [
            name
            for name in sorted(directory_names)
            if directory_exclusion_reason(
                (current_path / name).relative_to(root)
            ) is None
        ]
        candidates.update(
            current_path / name
            for name in file_names
            if name in COPILOT_INSTRUCTION_NAMES
        )
    for relative in COPILOT_CONFIGURATION_DIRECTORIES:
        directory = root / relative
        if directory.is_dir():
            candidates.update(path for path in directory.rglob("*") if path.is_file())
    return sorted(path.relative_to(root).as_posix() for path in candidates if path.is_file())
