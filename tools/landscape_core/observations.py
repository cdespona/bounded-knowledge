"""Stable observation construction."""

import hashlib
import json
from typing import Any, Dict, Optional


def observation(
    *,
    kind: str,
    repository: str,
    commit: str,
    detector: str,
    detector_version: int,
    value: Any,
    source_path: Optional[str] = None
) -> Dict[str, Any]:
    identity = {
        "kind": kind,
        "repository": repository,
        "commit": commit,
        "detector": detector,
        "detectorVersion": detector_version,
        "sourcePath": source_path,
        "value": value,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result = {
        "id": digest,
        "kind": kind,
        "repository": repository,
        "commit": commit,
        "detector": {"name": detector, "version": detector_version},
        "value": value,
    }
    if source_path is not None:
        result["source"] = {"path": source_path}
    return result

