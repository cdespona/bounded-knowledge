"""Evidence audit of one candidate: the auditor's findings, the reviser's decisions and the deterministic downgrade.

Decision: issue #11, implemented by #30. The pipeline per application is
``cartographer -> check -> audit -> check -> reviser -> check -> downgrade``. Models only propose; every rule below
is enforced here: the auditor's findings must point at real claims and bundle lines, the reviser may touch only
claims a finding names, and a finding that was declined or left unaddressed lowers its claim's status one step and
leaves a caveat that resolves into ``audit.json``. Findings never fail a run.
"""

import copy

from .candidates import extract_candidate_response, validate_candidate
from .contracts import CAVEAT_KINDS, REPOSITORY_ID, SHA256, _exact_fields, _is_non_empty_string, _line_bounds, \
    _valid_lines

SCHEMA_VERSION = 1
DECISIONS = {"applied", "declined"}
STATUS_ORDER = ["unknown", "inferred", "confirmed"]  # weakest first
CONFIDENCE_CAP = {"inferred": "medium", "unknown": "low"}
CONFIDENCE_ORDER = ["low", "medium", "high"]


def _claims(candidate):
    return {claim["id"]: claim for claim in candidate["proposedProfile"]["claims"]}


def _selected(bundle):
    selected = {}
    for item in bundle.get("selectedEvidence", []):
        if isinstance(item, dict) and isinstance(item.get("path"), str) and _valid_lines(item.get("lines")) \
                and isinstance(item.get("observationIds"), list):
            selected.setdefault(item["path"], []).append((item["lines"], set(item["observationIds"])))
    return selected


def _reference_errors(items, bundle, prefix):
    """References must have the evidence shape and lie inside a selected bundle entry."""
    if not isinstance(items, list) or not items:
        return ["{} must be a non-empty array".format(prefix)]
    errors, selected = [], _selected(bundle)
    for index, item in enumerate(items):
        where = "{}[{}]".format(prefix, index)
        if not _exact_fields(item, {"repository", "commit", "path", "lines", "observationId"}):
            errors.append("{} must have exactly repository, commit, path, lines and observationId".format(where))
            continue
        if item["repository"] != bundle["repository"] or item["commit"] != bundle["commit"]:
            errors.append("{} does not match the bundle repository and commit".format(where))
        inside = False
        if _valid_lines(item["lines"]) and isinstance(item["path"], str) and isinstance(item["observationId"], str):
            start, end = _line_bounds(item["lines"])
            for lines, observation_ids in selected.get(item["path"], []):
                low, high = _line_bounds(lines)
                if low <= start <= end <= high and item["observationId"] in observation_ids:
                    inside = True
                    break
        if not inside:
            errors.append("{} is outside the bundle's selected evidence".format(where))
    return errors


def validate_audit(audit, candidate, bundle):
    """Check an auditor document against the candidate it audits and the bundle; returns error strings."""
    if not _exact_fields(audit, {"schemaVersion", "evidenceBundleId", "findings"}):
        return ["audit must have exactly schemaVersion, evidenceBundleId and findings"]
    errors = []
    if audit["schemaVersion"] != SCHEMA_VERSION:
        errors.append("audit schemaVersion must be {}".format(SCHEMA_VERSION))
    if audit["evidenceBundleId"] != bundle["id"] or candidate.get("evidenceBundleId") != bundle["id"]:
        errors.append("audit evidenceBundleId must match the candidate and the bundle")
    findings = audit["findings"]
    if not isinstance(findings, list):
        return errors + ["audit findings must be an array"]
    claims, seen = _claims(candidate), set()
    for index, finding in enumerate(findings):
        prefix = "findings[{}]".format(index)
        if not _exact_fields(finding, {"id", "kind", "explanation"}, {"claimId", "citations", "contradicts"}):
            errors.append("{} has invalid fields".format(prefix))
            continue
        if not isinstance(finding["id"], str) or not REPOSITORY_ID.fullmatch(finding["id"]):
            errors.append("{}.id must be lowercase kebab-case".format(prefix))
        elif finding["id"] in seen:
            errors.append("{}.id is duplicated".format(prefix))
        seen.add(finding["id"])
        if finding["kind"] not in CAVEAT_KINDS:
            errors.append("{}.kind must be one of {}".format(prefix, ", ".join(sorted(CAVEAT_KINDS))))
        if not _is_non_empty_string(finding["explanation"]):
            errors.append("{}.explanation must be non-empty".format(prefix))
        claim_id = finding.get("claimId")
        if "claimId" in finding and claim_id not in claims:
            errors.append("{}.claimId names no claim of the candidate".format(prefix))
        if "citations" in finding:
            errors.extend(_reference_errors(finding["citations"], bundle, prefix + ".citations"))
        if "contradicts" in finding:
            errors.extend(_reference_errors(finding["contradicts"], bundle, prefix + ".contradicts"))
            if "claimId" not in finding:
                errors.append("{}.contradicts needs a claimId to attach the counterevidence to".format(prefix))
        if finding["kind"] in {"overreach", "mislabelled-status"} and "claimId" not in finding:
            errors.append("{} {} findings must name a claim".format(prefix, finding["kind"]))
        if finding["kind"] == "missed-evidence" and "citations" not in finding:
            errors.append("{} missed-evidence findings must cite the bundle".format(prefix))
        if "claimId" not in finding and "citations" not in finding:
            errors.append("{} must point at a claim id or a bundle path".format(prefix))
    return errors


def extract_audit(response, candidate, bundle):
    document = extract_candidate_response(response)
    return document, validate_audit(document, candidate, bundle)


def finding_kinds(audit):
    return {finding["id"]: finding["kind"] for finding in audit["findings"]}


def _key(reference):
    return (reference["path"], reference["observationId"])


def validate_revision(revision, original, audit, bundle):
    """Check the reviser's output: decisions for every finding and a candidate that touches only named claims."""
    if not _exact_fields(revision, {"schemaVersion", "evidenceBundleId", "decisions", "candidate"}):
        return ["revision must have exactly schemaVersion, evidenceBundleId, decisions and candidate"]
    errors = []
    if revision["schemaVersion"] != SCHEMA_VERSION:
        errors.append("revision schemaVersion must be {}".format(SCHEMA_VERSION))
    if revision["evidenceBundleId"] != bundle["id"]:
        errors.append("revision evidenceBundleId must match the bundle")
    findings = {finding["id"]: finding for finding in audit["findings"]}
    decisions = revision["decisions"]
    decided = {}
    if not isinstance(decisions, list):
        return errors + ["revision decisions must be an array"]
    for index, decision in enumerate(decisions):
        prefix = "decisions[{}]".format(index)
        if not _exact_fields(decision, {"findingId", "decision", "reason"}):
            errors.append("{} must have exactly findingId, decision and reason".format(prefix))
            continue
        if decision["findingId"] not in findings:
            errors.append("{}.findingId names no audit finding".format(prefix))
        elif decision["findingId"] in decided:
            errors.append("{}.findingId is decided twice".format(prefix))
        if decision["decision"] not in DECISIONS:
            errors.append("{}.decision must be applied or declined".format(prefix))
        if not _is_non_empty_string(decision["reason"]):
            errors.append("{}.reason must be non-empty".format(prefix))
        decided[decision["findingId"]] = decision
    for finding_id in sorted(set(findings) - set(decided)):
        errors.append("finding {} has no decision; every finding is applied or declined".format(finding_id))
    candidate = revision["candidate"]
    if not isinstance(candidate, dict):
        return errors + ["revision candidate must be an object"]
    errors.extend("revised candidate: " + error for error in validate_candidate(candidate, bundle))
    if errors:
        return errors
    for claim in candidate["proposedProfile"]["claims"]:
        if "caveats" in claim:
            errors.append("claim {} carries caveats; only the downgrade writes them".format(claim["id"]))
    for field in ("schemaVersion", "repository", "kind", "analyzedCommit", "evidenceBundleId"):
        if candidate[field] != original[field]:
            errors.append("revised candidate changes {}".format(field))
    old_profile, new_profile = original["proposedProfile"], candidate["proposedProfile"]
    if new_profile["openQuestions"] != old_profile["openQuestions"]:
        errors.append("revised candidate changes openQuestions; a revision may touch only claims")
    applied = {key: decision for key, decision in decided.items()
               if decision["decision"] == "applied" and key in findings}
    named = {findings[key]["claimId"] for key in applied if "claimId" in findings[key]}
    old_claims, new_claims = _claims(original), _claims(candidate)
    for claim_id, old in sorted(old_claims.items()):
        new = new_claims.get(claim_id)
        if new == old:
            continue
        if claim_id not in named:
            errors.append("claim {} was {} but no applied finding names it".format(
                claim_id, "dropped" if new is None else "changed"))
        elif new is not None and STATUS_ORDER.index(new["status"]) > STATUS_ORDER.index(old["status"]):
            errors.append("claim {} was upgraded from {} to {}; a revision may only keep or lower a status"
                          .format(claim_id, old["status"], new["status"]))
    cited = {_key(reference) for key in applied if findings[key]["kind"] == "missed-evidence"
             for reference in findings[key].get("citations", [])}
    for claim_id, new in sorted(new_claims.items()):
        if claim_id not in old_claims and not any(_key(item) in cited for item in new["evidence"]):
            errors.append("claim {} was added without an applied missed-evidence finding that cites its "
                          "evidence".format(claim_id))
    return errors


def extract_revision(response, original, audit, bundle):
    document = extract_candidate_response(response)
    return document, validate_revision(document, original, audit, bundle)


def _addressed(finding, decision, original_claims, revised_claims):
    """An applied finding counts only if the claim it names changed (or was dropped), or its citation was used."""
    if decision["decision"] != "applied":
        return False
    claim_id = finding.get("claimId")
    if claim_id is not None:
        return revised_claims.get(claim_id) != original_claims.get(claim_id)
    cited = {_key(reference) for reference in finding.get("citations", [])}
    return any(claim_id_ not in original_claims and any(_key(item) in cited for item in claim["evidence"])
               for claim_id_, claim in revised_claims.items())


def downgrade_candidate(original, audit, revision):
    """Return (final candidate, summary).

    Every finding that was declined or not addressed lowers the claim it names one step (confirmed -> inferred ->
    unknown) and adds a caveat. A finding that cites contradicting locations adds them to the claim's
    counterevidence whatever the decision. Nothing here consults a model.
    """
    final = copy.deepcopy(revision["candidate"])
    decisions = {item["findingId"]: item for item in revision["decisions"]}
    original_claims, revised_claims = _claims(original), _claims(revision["candidate"])
    claims = _claims(final)
    summary = {"findings": len(audit["findings"]), "applied": 0, "declined": 0, "unaddressed": 0, "downgrades": 0,
               "caveats": 0, "unattached": []}
    for finding in audit["findings"]:
        decision = decisions[finding["id"]]
        summary[decision["decision"]] += 1
        addressed = _addressed(finding, decision, original_claims, revised_claims)
        if decision["decision"] == "applied" and not addressed:
            summary["unaddressed"] += 1
        claim = claims.get(finding.get("claimId"))
        for reference in finding.get("contradicts", []):
            if claim is not None and reference not in claim["counterevidence"]:
                claim["counterevidence"].append(reference)
        if addressed:
            continue
        if claim is None:
            if "claimId" not in finding:
                summary["unattached"].append(finding["id"])
            continue
        if decision["decision"] == "declined":
            reason = "declined by the reviser: " + decision["reason"]
        else:
            reason = "applied by the reviser without changing the claim: " + decision["reason"]
        position = STATUS_ORDER.index(claim["status"])
        if position > 0:
            claim["status"] = STATUS_ORDER[position - 1]
            summary["downgrades"] += 1
            cap = CONFIDENCE_CAP[claim["status"]]
            if CONFIDENCE_ORDER.index(claim["confidence"]) > CONFIDENCE_ORDER.index(cap):
                claim["confidence"] = cap
        if claim["status"] == "unknown":
            note = "audit finding {} is unresolved: {}".format(finding["id"], finding["explanation"])
            claim["missingEvidence"] = sorted(set(claim.get("missingEvidence", [])) | {note})
        claim.setdefault("caveats", []).append(
            {"findingId": finding["id"], "kind": finding["kind"],
             "explanation": "{} ({})".format(finding["explanation"], reason)})
        summary["caveats"] += 1
    return final, summary
