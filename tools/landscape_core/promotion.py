"""Human-gated promotion of a validated catalog proposal into the canonical catalog.

The review text carries a digest; a human approval is bound to the proposal digest, the review digest
and the exact set of added and changed assertions; the single writer re-checks all of it.
"""

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

from .catalog_proposals import (
    canonicalize_catalog_proposal, diff_catalog, empty_catalog, render_catalog_review, validate_catalog_proposal,
)
from .contracts import CATALOG_COLLECTION_TYPES, _exact_fields, _is_datetime, _is_non_empty_string, validate_landscape_catalog

CATALOG_FILE = "landscape-catalog.json"
APPROVAL_FIELDS = {"schemaVersion", "proposalSha256", "reviewSha256", "approvedBy", "approvedAt", "accepted"}
ACCEPTED_FIELDS = {"added", "changed", "openQuestionsAdded"}


def digest(document):
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def serialize(document):
    """Contract serialization: UTF-8, sorted keys, two-space indentation, one trailing newline."""
    return json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def review_digest(review):
    """Return the digest a review states about itself, recomputed from the text above its footer."""
    body, _, _ = review.rpartition("\nReview digest (sha256 of the text above): ")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _accepted(diff):
    return {"added": diff["added"], "changed": diff["changed"], "openQuestionsAdded": diff["openQuestionsAdded"]}


def _load_current(path):
    if path is None or not Path(path).exists():
        return empty_catalog()
    current = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = validate_landscape_catalog(current)
    if errors:
        raise ValueError("current catalog is invalid: " + "; ".join(errors))
    return current


def merge_catalog(proposal, current):
    """Apply a proposal to a catalog: entities are added or replaced by id, nothing is removed."""
    result = copy.deepcopy(current)
    for collection in list(CATALOG_COLLECTION_TYPES) + ["relationships"]:
        by_id = {item["id"]: item for item in result[collection]}
        by_id.update({item["id"]: copy.deepcopy(item) for item in proposal[collection]})
        result[collection] = list(by_id.values())
    result["openQuestions"] = sorted(set(result["openQuestions"]) | set(proposal["openQuestions"]))
    return canonicalize_catalog_proposal(result)


def build_approval(proposal, current, draft, reviewed, approved_by, approved_at):
    """Record a human approval of exactly the review whose digest the approver supplies."""
    if not _is_non_empty_string(approved_by):
        raise ValueError("approver must be a non-empty name")
    if not _is_datetime(approved_at):
        raise ValueError("approval time must be a timezone-aware timestamp")
    review = render_catalog_review(proposal, current, draft)
    actual = review_digest(review)
    if reviewed != actual:
        raise ValueError("reviewed digest does not match the current review; read the current review before approving")
    return {"schemaVersion": 1, "proposalSha256": digest(proposal), "reviewSha256": actual,
            "approvedBy": approved_by, "approvedAt": approved_at, "accepted": _accepted(diff_catalog(proposal, current))}


def _verify_approval(approval, proposal, current, draft):
    if not _exact_fields(approval, APPROVAL_FIELDS) or approval["schemaVersion"] != 1:
        raise ValueError("approval has invalid fields")
    if not _is_non_empty_string(approval["approvedBy"]) or not _is_datetime(approval["approvedAt"]):
        raise ValueError("approval needs an approver and a timezone-aware time")
    if approval["proposalSha256"] != digest(proposal):
        raise ValueError("approval does not match this proposal")
    if approval["reviewSha256"] != review_digest(render_catalog_review(proposal, current, draft)):
        raise ValueError("approval does not match the review of this proposal against the current catalog")
    if not _exact_fields(approval["accepted"], ACCEPTED_FIELDS) or approval["accepted"] != _accepted(diff_catalog(proposal, current)):
        raise ValueError("approval does not cover exactly the added and changed assertions")


def _stamp(value):
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


ANSWERS_DIR = "answers"


def _interview_evidence(document):
    for collection in list(CATALOG_COLLECTION_TYPES) + ["relationships"]:
        for item in document[collection]:
            for field in ("evidence", "counterevidence"):
                for evidence in item["assessment"][field]:
                    if evidence["kind"] == "curated" and evidence["sourceType"] == "interview":
                        yield evidence


def used_answers(proposal, draft):
    """The answered questions that the proposal's interview evidence cites, sorted by question id."""
    cited = {evidence["sourceId"] for evidence in _interview_evidence(proposal)}
    return [copy.deepcopy(item) for item in sorted(draft["questions"], key=lambda item: item["id"])
            if item["id"] in cited and item["answerStatus"] == "answered"]


def with_answer_locators(proposal, stem):
    """Point each interview evidence at its preserved answer; the approved proposal itself is not changed."""
    result = copy.deepcopy(proposal)
    for evidence in _interview_evidence(result):
        evidence["locator"] = "{}/{}.json#{}".format(ANSWERS_DIR, stem, evidence["sourceId"])
    return canonicalize_catalog_proposal(result)


def validate_answer_provenance(catalog, promotions_dir):
    """Every interview evidence that names a preserved answer must resolve to a recorded, matching answer."""
    errors, loaded = [], {}
    for collection in list(CATALOG_COLLECTION_TYPES) + ["relationships"]:
        for item in catalog[collection]:
            for field in ("evidence", "counterevidence"):
                for index, evidence in enumerate(item["assessment"][field]):
                    if evidence["kind"] != "curated" or evidence["sourceType"] != "interview" or "locator" not in evidence:
                        continue
                    prefix = "{}[{}].{}[{}]".format(collection, item["id"], field, index)
                    name, _, question_id = evidence["locator"].partition("#")
                    directory, _, base = name.partition("/")
                    if (directory != ANSWERS_DIR or not base.endswith(".json") or "/" in base or "\\" in base
                            or base.startswith(".") or question_id != evidence["sourceId"]):
                        errors.append("{} has an invalid answer locator".format(prefix))
                        continue
                    if name not in loaded:
                        path = Path(promotions_dir) / name
                        try:
                            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            loaded[name] = None
                    document = loaded[name]
                    questions = document.get("questions") if isinstance(document, dict) else None
                    if not isinstance(questions, list):
                        errors.append("{} cites answers file {} that is missing or unreadable".format(prefix, name))
                        continue
                    answer = next((q.get("answer") for q in questions if isinstance(q, dict) and q.get("id") == question_id
                                   and q.get("answerStatus") == "answered" and isinstance(q.get("answer"), dict)), None)
                    if answer is None:
                        errors.append("{} cites question {} that {} does not hold as answered".format(prefix, question_id, name))
                    elif evidence["suppliedBy"] != answer.get("answerer") or evidence["recordedAt"] != answer.get("recordedAt"):
                        errors.append("{} does not match the preserved answerer or time".format(prefix))
    return errors


def promote(proposal, draft, bundles, approval, catalog_path, promotions_dir, dry_run=False):
    """Validate, verify the approval, and write the merged catalog and a promotion record."""
    errors = validate_catalog_proposal(proposal, draft, bundles)
    if errors:
        raise ValueError("proposal is invalid: " + "; ".join(errors[:5]))
    current = _load_current(catalog_path)
    _verify_approval(approval, proposal, current, draft)
    diff = diff_catalog(proposal, current)
    accepted = _accepted(diff)
    if not any(accepted.values()):
        return {"promoted": False, "reason": "the catalog already contains this proposal", "accepted": accepted}
    record_path = Path(promotions_dir) / "{}-{}.json".format(_stamp(approval["approvedAt"]), approval["proposalSha256"][:8])
    answers_path = record_path.parent / ANSWERS_DIR / record_path.name
    answers = used_answers(proposal, draft)
    merged = merge_catalog(with_answer_locators(proposal, record_path.stem), current)
    errors = validate_landscape_catalog(merged)
    if errors:
        raise ValueError("merged catalog is invalid: " + "; ".join(errors[:5]))
    answers_document = {"schemaVersion": 1, "questionnaireDraftId": draft["id"], "questions": answers}
    if not dry_run:
        # Validate the references against the file about to be written, before anything is written.
        with tempfile.TemporaryDirectory() as scratch:
            (Path(scratch) / ANSWERS_DIR).mkdir()
            (Path(scratch) / ANSWERS_DIR / answers_path.name).write_text(serialize(answers_document), encoding="utf-8")
            errors = validate_answer_provenance(merged, scratch)
        if errors:
            raise ValueError("answer provenance is invalid: " + "; ".join(errors[:5]))
    record = {"schemaVersion": 1, "proposalSha256": approval["proposalSha256"], "reviewSha256": approval["reviewSha256"],
              "approvedBy": approval["approvedBy"], "approvedAt": approval["approvedAt"], "accepted": accepted,
              "retained": {"removed": diff["removed"], "openQuestionsRemoved": diff["openQuestionsRemoved"]},
              "catalogSha256Before": digest(current), "catalogSha256After": digest(merged),
              "questionnaireDraftId": draft["id"], "evidenceBundleIds": sorted(bundle["id"] for bundle in bundles),
              "answersFile": "{}/{}".format(ANSWERS_DIR, answers_path.name), "answerIds": [item["id"] for item in answers]}
    result = {"promoted": not dry_run, "accepted": accepted, "retained": record["retained"],
              "catalog": str(catalog_path), "record": str(record_path), "answers": str(answers_path)}
    if dry_run:
        return result
    if record_path.exists() or answers_path.exists():
        raise ValueError("a promotion record already exists at " + str(record_path))
    answers_path.parent.mkdir(parents=True, exist_ok=True)
    Path(catalog_path).parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(catalog_path) + ".tmp")
    temporary.write_text(serialize(merged), encoding="utf-8")
    answers_path.write_text(serialize(answers_document), encoding="utf-8")
    record_path.write_text(serialize(record), encoding="utf-8")
    temporary.replace(catalog_path)
    return result
