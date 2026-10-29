"""Deterministic checks and review diffs for untrusted model-proposed catalogs."""

import hashlib
import json

from .candidates import extract_candidate_response
from .contracts import (
    CATALOG_COLLECTION_TYPES,
    _knowledge_evidence_key,
    validate_evidence_bundle,
    validate_landscape_catalog,
    validate_questionnaire_draft,
)


def _sorted_strings(values):
    return sorted(set(values)) if isinstance(values, list) and all(isinstance(item, str) for item in values) else values


def _canonical_assessment(assessment):
    if not isinstance(assessment, dict):
        return
    for field in ("evidence", "counterevidence"):
        if isinstance(assessment.get(field), list):
            unique = {json.dumps(item, sort_keys=True): item for item in assessment[field]}
            assessment[field] = sorted(unique.values(), key=_knowledge_evidence_key)
    if "missingEvidence" in assessment:
        assessment["missingEvidence"] = _sorted_strings(assessment["missingEvidence"])


def canonicalize_catalog_proposal(proposal):
    """Put a proposal's lists in the contract's order without adding, removing, or rewording anything."""
    def identity(item, *keys):
        return tuple(str(item.get(key, "")) if isinstance(item, dict) else "" for key in keys)

    for collection in list(CATALOG_COLLECTION_TYPES) + ["relationships"]:
        values = proposal.get(collection)
        if not isinstance(values, list):
            continue
        for item in values:
            _canonical_assessment(item.get("assessment") if isinstance(item, dict) else None)
        keys = ("type", "id") if collection == "relationships" else ("id",)
        proposal[collection] = sorted(values, key=lambda item: identity(item, *keys))
    if "openQuestions" in proposal:
        proposal["openQuestions"] = _sorted_strings(proposal["openQuestions"])
    return proposal


def extract_catalog_proposal(response):
    """Extract exactly one JSON object from a model response and canonicalize its list order."""
    return canonicalize_catalog_proposal(extract_candidate_response(response))


def empty_catalog():
    document = {"schemaVersion": 1, "relationships": [], "openQuestions": []}
    document.update({collection: [] for collection in CATALOG_COLLECTION_TYPES})
    return document


def _line_range(value):
    first, _, last = value.partition("-")
    return int(first), int(last or first)


def _assessments(proposal):
    for collection in CATALOG_COLLECTION_TYPES:
        for item in proposal.get(collection, []):
            yield "{}[{}]".format(collection, item.get("id")), item.get("assessment")
    for item in proposal.get("relationships", []):
        yield "relationships[{}]".format(item.get("id")), item.get("assessment")


def _check_repository(evidence, bundles, prefix):
    pinned = [item for item in bundles
              if item["repository"] == evidence["repositoryId"] and item["commit"] == evidence["commit"]]
    if not pinned:
        return ["{} cites a repository or commit that no supplied evidence bundle pins".format(prefix)]
    entries = [entry for bundle in pinned for entry in bundle["selectedEvidence"] if entry["path"] == evidence["path"]]
    if not entries:
        return ["{} cites a path that the evidence bundle did not select".format(prefix)]
    if "lines" in evidence:
        first, last = _line_range(evidence["lines"])
        entries = [entry for entry in entries
                   if _line_range(entry["lines"])[0] <= first and last <= _line_range(entry["lines"])[1]]
        if not entries:
            return ["{} cites lines outside the selected range".format(prefix)]
    if "observationId" in evidence and not any(evidence["observationId"] in entry["observationIds"] for entry in entries):
        return ["{} cites an observation that the selected entry does not carry".format(prefix)]
    return []


def _check_curated(evidence, answers, prefix):
    if evidence["sourceType"] != "interview":
        return []
    answer = answers.get(evidence["sourceId"])
    if answer is None:
        return ["{} cites no answered questionnaire question".format(prefix)]
    if evidence["suppliedBy"] != answer["answerer"] or evidence["recordedAt"] != answer["recordedAt"]:
        return ["{} does not match the recorded answerer or time".format(prefix)]
    return []


def validate_catalog_proposal(proposal, draft, bundles):
    """Validate a proposed catalog and bind its evidence to supplied bundles and recorded answers.

    Testimony is attributed draft evidence: it can support an inferred or unknown assertion but
    never a confirmed one by itself, and an unknown or deferred answer supports nothing.
    """
    errors = ["evidence bundle: {}".format(error) for bundle in bundles for error in validate_evidence_bundle(bundle)]
    errors += ["questionnaire draft: {}".format(error) for error in validate_questionnaire_draft(draft)]
    if errors:
        return errors
    errors = validate_landscape_catalog(proposal)
    if errors:
        return errors
    answers = {item["id"]: item["answer"] for item in draft["questions"] if item["answerStatus"] == "answered"}
    for name, assessment in _assessments(proposal):
        for field in ("evidence", "counterevidence"):
            for index, evidence in enumerate(assessment[field]):
                prefix = "{}.{}[{}]".format(name, field, index)
                if evidence["kind"] == "repository":
                    errors += _check_repository(evidence, bundles, prefix)
                else:
                    errors += _check_curated(evidence, answers, prefix)
        if assessment["status"] == "confirmed" and not any(item["kind"] == "repository" for item in assessment["evidence"]):
            errors.append("{} is confirmed without repository evidence; testimony alone cannot confirm".format(name))
    return errors


def _entities(document):
    result = {}
    for collection in list(CATALOG_COLLECTION_TYPES) + ["relationships"]:
        for item in document[collection]:
            result[(collection, item["id"])] = item
    return result


def diff_catalog(proposal, current=None):
    """Return a stable added, removed, and changed summary of a proposal against a catalog."""
    before, after = _entities(current or empty_catalog()), _entities(proposal)
    old_questions = set((current or empty_catalog())["openQuestions"])
    new_questions = set(proposal["openQuestions"])
    return {
        "added": sorted("{}/{}".format(*key) for key in after if key not in before),
        "removed": sorted("{}/{}".format(*key) for key in before if key not in after),
        "changed": sorted("{}/{}".format(*key) for key in after if key in before and before[key] != after[key]),
        "openQuestionsAdded": sorted(new_questions - old_questions),
        "openQuestionsRemoved": sorted(old_questions - new_questions),
    }


_CONTENT_FIELDS = ("kind", "purpose", "description", "definition", "actor", "outcome", "type", "statement")


def _describe_evidence(evidence, answers):
    if evidence["kind"] == "repository":
        lines = ":{}".format(evidence["lines"]) if "lines" in evidence else ""
        return "repository `{}@{}` `{}{}`".format(evidence["repositoryId"], evidence["commit"][:8], evidence["path"], lines)
    text = "{} `{}`".format(evidence["sourceType"], evidence["sourceId"])
    if "suppliedBy" in evidence:
        text += " by {}".format(evidence["suppliedBy"])
    answer = answers.get(evidence["sourceId"])
    if answer:
        text += ': "{}"'.format(answer["text"].strip().replace("\n", " "))
    return text


def _render_entity(collection, item, answers):
    heading = item.get("name") or item["id"]
    lines = ["### {} (`{}`)".format(heading, item["id"]), ""]
    if collection == "relationships":
        lines.append("- {} -> {}".format(*("{} `{}`".format(item[end]["entityType"], item[end]["entityId"])
                                             for end in ("source", "target"))))
    for field in _CONTENT_FIELDS:
        if field in item:
            lines.append("- {}: {}".format(field, item[field]))
    assessment = item["assessment"]
    lines.append("- assessment: **{}**, {} confidence".format(assessment["status"], assessment["confidence"]))
    for field in ("evidence", "counterevidence"):
        lines.extend("  - {}: {}".format(field, _describe_evidence(entry, answers)) for entry in assessment[field])
    lines.extend("  - missing: {}".format(entry) for entry in assessment.get("missingEvidence", []))
    return lines + [""]


def render_catalog_review(proposal, current=None, draft=None):
    """Render a Markdown review of a proposal: the diff, then every assertion with its content and evidence."""
    diff = diff_catalog(proposal, current)
    answers = {item["id"]: item["answer"] for item in (draft or {}).get("questions", [])
               if item.get("answerStatus") == "answered"}
    lines = ["# Catalog proposal review", "",
             "Draft proposal only. Nothing here is canonical until a human approves it.", "",
             "## Diff against the current catalog", ""]
    for title, key in (("Added", "added"), ("Kept, not in this proposal", "removed"), ("Changed", "changed"),
                       ("Open questions added", "openQuestionsAdded"), ("Open questions kept, not in this proposal", "openQuestionsRemoved")):
        lines.append("- {}: {}".format(title, len(diff[key])))
        lines.extend("  - `{}`".format(item) for item in diff[key])
    for collection in list(CATALOG_COLLECTION_TYPES) + ["relationships"]:
        if proposal.get(collection):
            lines += ["", "## {}".format(collection), ""]
            for item in proposal[collection]:
                lines.extend(_render_entity(collection, item, answers))
    body = "\n".join(lines) + "\n"
    return body + "\nReview digest (sha256 of the text above): `{}`\n".format(hashlib.sha256(body.encode("utf-8")).hexdigest())
