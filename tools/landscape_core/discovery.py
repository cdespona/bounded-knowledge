"""Deterministic repository discovery orchestration."""

from pathlib import Path

from detectors import built_in_detectors
from .git_repository import commit_sha, dirty_paths, require_repository_root
from .safety import copilot_configuration_files


def preflight(path: Path):
    root = require_repository_root(path)
    dirty = dirty_paths(root)
    return {
        "root": str(root),
        "commit": commit_sha(root),
        "clean": not dirty,
        "dirtyPaths": dirty,
        "copilotConfigurationFiles": copilot_configuration_files(root),
    }


def discover(path: Path, repository: str):
    check = preflight(path)
    if not check["clean"]:
        raise ValueError("Repository must have a clean working tree before discovery")

    root = Path(check["root"])
    commit = check["commit"]
    observations = []
    excluded = []
    detectors = built_in_detectors()
    for detector in detectors:
        found, skipped = detector.detect(root, repository, commit)
        observations.extend(found)
        excluded.extend(skipped)

    return {
        "schemaVersion": 1,
        "repository": repository,
        "commit": commit,
        "detectors": [
            {"name": detector.name, "version": detector.version} for detector in detectors
        ],
        "observations": sorted(observations, key=lambda item: item["id"]),
        "excluded": sorted(excluded, key=lambda item: (item["path"], item["reason"])),
    }

