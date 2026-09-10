"""Operational validation for model-produced candidate envelopes."""

from .contracts import validate_candidate_envelope, validate_evidence_bundle


def validate_candidate(candidate, evidence_bundle):
    """Validate a candidate and its binding to a structurally valid evidence bundle."""
    evidence_errors = validate_evidence_bundle(evidence_bundle)
    if evidence_errors:
        return ["evidence bundle: {}".format(error) for error in evidence_errors]
    return validate_candidate_envelope(candidate, evidence_bundle)
