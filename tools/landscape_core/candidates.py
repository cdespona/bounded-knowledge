"""Extraction and validation for untrusted model-produced candidate envelopes."""

import json

from .contracts import validate_candidate_envelope, validate_evidence_bundle


def extract_candidate_response(response):
    """Extract exactly one JSON object from an otherwise untrusted text response."""
    if not isinstance(response, str) or not response:
        raise ValueError("Model response must be non-empty text")
    decoder = json.JSONDecoder()
    objects = []
    position = 0
    while True:
        object_start = response.find("{", position)
        array_start = response.find("[", position)
        starts = [start for start in (object_start, array_start) if start >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            value, end = decoder.raw_decode(response, start)
        except json.JSONDecodeError:
            position = start + 1
            continue
        if not isinstance(value, dict):
            raise ValueError("Model response JSON value must be an object")
        objects.append(value)
        position = end
    if len(objects) != 1:
        raise ValueError("Model response must contain exactly one JSON object")
    return objects[0]


def validate_candidate(candidate, evidence_bundle):
    """Validate a candidate and its binding to a structurally valid evidence bundle."""
    evidence_errors = validate_evidence_bundle(evidence_bundle)
    if evidence_errors:
        return ["evidence bundle: {}".format(error) for error in evidence_errors]
    return validate_candidate_envelope(candidate, evidence_bundle)
