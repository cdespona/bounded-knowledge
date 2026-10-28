"""Tests for validating and reviewing model-proposed catalogs."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from landscape_core.catalog_proposals import (  # noqa: E402
    diff_catalog, empty_catalog, extract_catalog_proposal, render_catalog_review, validate_catalog_proposal,
)
from landscape_core.contracts import artifact_id  # noqa: E402

CONTRACTS = ROOT / "examples" / "contracts"
ANSWERED_AT = "2026-10-28T08:00:00Z"


def load(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def answered_draft():
    draft = load("questionnaire-draft.valid.json")
    question = next(item for item in draft["questions"] if item["id"] == "citation-audit")
    question["reviewType"] = "human-only"
    question["answerStatus"] = "answered"
    question["answer"] = {"text": "Owned by the platform squad.", "answerer": "Technical lead", "recordedAt": ANSWERED_AT}
    draft.pop("id")
    draft["id"] = artifact_id(draft)
    return draft


def repository_evidence(bundle, **overrides):
    entry = bundle["selectedEvidence"][0]
    value = {"kind": "repository", "repositoryId": bundle["repository"], "commit": bundle["commit"],
             "path": entry["path"], "lines": entry["lines"], "observationId": entry["observationIds"][0]}
    value.update(overrides)
    return value


def interview(**overrides):
    value = {"kind": "curated", "sourceId": "citation-audit", "sourceType": "interview",
             "suppliedBy": "Technical lead", "recordedAt": ANSWERED_AT}
    value.update(overrides)
    return value


def proposal(bundle, status="inferred", evidence=None):
    document = empty_catalog()
    document["applications"] = [{
        "id": "synthetic-app", "name": "Synthetic app", "purpose": "Exercise the proposal checks.",
        "assessment": {"status": status, "confidence": "medium", "analyzedAt": ANSWERED_AT,
                       "evidence": evidence if evidence is not None else [interview(), repository_evidence(bundle)],
                       "counterevidence": []},
    }]
    return document


class CatalogProposalTests(unittest.TestCase):
    def setUp(self):
        self.bundle = load("evidence-bundle.valid.json")
        self.draft = answered_draft()

    def errors(self, document, draft=None):
        return validate_catalog_proposal(document, draft or self.draft, [self.bundle])

    def test_accepts_inferred_assertion_with_repository_and_interview_evidence(self):
        self.assertEqual(self.errors(proposal(self.bundle)), [])

    def test_rejects_confirmed_assertion_supported_only_by_testimony(self):
        errors = self.errors(proposal(self.bundle, "confirmed", [interview()]))
        self.assertTrue(any("testimony alone" in error for error in errors))

    def test_allows_confirmed_assertion_with_repository_evidence(self):
        self.assertEqual(self.errors(proposal(self.bundle, "confirmed", [repository_evidence(self.bundle)])), [])

    def test_rejects_unpinned_commit_unselected_path_and_foreign_lines(self):
        for override, text in (({"commit": "b" * 40}, "pins"), ({"path": "other.xml"}, "did not select"),
                               ({"lines": "9-12"}, "outside the selected range"),
                               ({"observationId": "d" * 64}, "observation")):
            with self.subTest(override=override):
                errors = self.errors(proposal(self.bundle, evidence=[repository_evidence(self.bundle, **override)]))
                self.assertTrue(any(text in error for error in errors), errors)

    def test_rejects_interview_evidence_that_does_not_match_an_answer(self):
        for override in ({"sourceId": "missing-question"}, {"suppliedBy": "Someone else"},
                         {"recordedAt": "2026-10-29T08:00:00Z"}, {"sourceId": "runtime-binding"}):
            with self.subTest(override=override):
                errors = self.errors(proposal(self.bundle, evidence=[interview(**override), repository_evidence(self.bundle)]))
                self.assertTrue(errors)

    def test_structural_catalog_errors_are_reported_first(self):
        document = proposal(self.bundle)
        document["applications"][0]["extra"] = True
        self.assertEqual(self.errors(document), ["applications[0] has invalid fields"])

    def test_diff_and_review_are_stable_and_marked_as_draft(self):
        document = proposal(self.bundle)
        current = copy.deepcopy(document)
        current["applications"][0]["purpose"] = "Old purpose."
        current["openQuestions"] = ["Who owns it?"]
        self.assertEqual(diff_catalog(document), {
            "added": ["applications/synthetic-app"], "removed": [], "changed": [],
            "openQuestionsAdded": [], "openQuestionsRemoved": []})
        self.assertEqual(diff_catalog(document, current)["changed"], ["applications/synthetic-app"])
        review = render_catalog_review(document, current)
        self.assertIn("Nothing here is canonical", review)
        self.assertEqual(review, render_catalog_review(document, current))

    def test_extract_requires_exactly_one_object(self):
        self.assertEqual(extract_catalog_proposal('Here: {"a": 1}'), {"a": 1})
        with self.assertRaises(ValueError):
            extract_catalog_proposal('{"a": 1} {"b": 2}')

    def test_extract_canonicalizes_order_only(self):
        document = proposal(self.bundle)
        second = copy.deepcopy(document["applications"][0])
        second["id"] = "another-app"
        document["applications"].insert(0, second)
        document["applications"][1]["assessment"]["evidence"].reverse()
        document["openQuestions"] = ["b", "a", "b"]
        extracted = extract_catalog_proposal(json.dumps(document))
        self.assertEqual([item["id"] for item in extracted["applications"]], ["another-app", "synthetic-app"])
        self.assertEqual(extracted["openQuestions"], ["a", "b"])
        self.assertEqual(self.errors(extracted), [])
        self.assertEqual(extracted["applications"][1]["purpose"], document["applications"][1]["purpose"])

    def test_cli_validates_and_reviews_a_proposal(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            files = {"proposal.json": proposal(self.bundle), "draft.json": self.draft, "bundle.json": self.bundle}
            for name, value in files.items():
                (base / name).write_text(json.dumps(value), encoding="utf-8")
            command = [str(ROOT / "tools" / "landscape"), "catalog"]
            valid = subprocess.run(command + ["propose-validate", str(base / "proposal.json"), "--draft",
                                              str(base / "draft.json"), "--evidence", str(base / "bundle.json")],
                                   capture_output=True, text=True)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            review = subprocess.run(command + ["review", str(base / "proposal.json"), "--output", str(base / "review.md")],
                                    capture_output=True, text=True)
            self.assertEqual(review.returncode, 0, review.stderr)
            self.assertIn("applications/synthetic-app", (base / "review.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
