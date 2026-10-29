"""Tests for the human-gated promotion of a catalog proposal."""

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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from landscape_core.catalog_proposals import render_catalog_review  # noqa: E402
from landscape_core.contracts import validate_landscape_catalog  # noqa: E402
from landscape_core.promotion import build_approval, digest, merge_catalog, promote, review_digest, serialize  # noqa: E402
from test_catalog_proposals import ANSWERED_AT, answered_draft, load, proposal  # noqa: E402

APPROVED_AT = "2026-10-29T12:00:00+01:00"


class PromotionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.bundle = load("evidence-bundle.valid.json")
        self.draft = answered_draft()
        self.proposal = proposal(self.bundle)
        self.catalog = self.base / "catalog" / "landscape-catalog.json"
        self.promotions = self.base / "catalog" / "promotions"

    def approval(self, current=None, document=None):
        document = document or self.proposal
        review = render_catalog_review(document, current, self.draft)
        return build_approval(document, current, self.draft, review_digest(review), "Technical lead", APPROVED_AT)

    def promote(self, approval, document=None, dry_run=False):
        return promote(document or self.proposal, self.draft, [self.bundle], approval, self.catalog, self.promotions, dry_run)

    def test_review_states_a_digest_of_its_own_text(self):
        review = render_catalog_review(self.proposal, None, self.draft)
        self.assertIn(review_digest(review), review.splitlines()[-1])
        self.assertNotEqual(review_digest(review), review_digest(review.replace("Synthetic", "Changed")))

    def test_approval_requires_the_digest_of_the_current_review(self):
        with self.assertRaises(ValueError):
            build_approval(self.proposal, None, self.draft, "0" * 64, "Technical lead", APPROVED_AT)
        with self.assertRaises(ValueError):
            build_approval(self.proposal, None, self.draft, review_digest(render_catalog_review(self.proposal, None, self.draft)),
                           "", APPROVED_AT)

    def test_promotion_writes_a_valid_deterministic_catalog_and_a_record(self):
        result = self.promote(self.approval())
        self.assertTrue(result["promoted"])
        written = json.loads(self.catalog.read_text(encoding="utf-8"))
        self.assertEqual(validate_landscape_catalog(written), [])
        self.assertEqual(self.catalog.read_text(encoding="utf-8"), serialize(written))
        self.assertEqual([item["id"] for item in written["applications"]], ["synthetic-app"])
        records = list(self.promotions.glob("*.json"))
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(record["approvedBy"], "Technical lead")
        self.assertEqual(record["accepted"]["added"], ["applications/synthetic-app"])
        self.assertEqual(record["evidenceBundleIds"], [self.bundle["id"]])

    def test_dry_run_writes_nothing(self):
        result = self.promote(self.approval(), dry_run=True)
        self.assertFalse(result["promoted"])
        self.assertFalse(self.catalog.exists())
        self.assertFalse(self.promotions.exists())

    def test_promoting_the_same_proposal_twice_changes_nothing(self):
        self.promote(self.approval())
        before = self.catalog.read_text(encoding="utf-8")
        current = json.loads(before)
        result = self.promote(self.approval(current))
        self.assertFalse(result["promoted"])
        self.assertEqual(self.catalog.read_text(encoding="utf-8"), before)
        self.assertEqual(len(list(self.promotions.glob("*.json"))), 1)

    def test_a_proposal_edited_after_approval_is_refused(self):
        approval = self.approval()
        edited = copy.deepcopy(self.proposal)
        edited["applications"][0]["purpose"] = "Edited after the review."
        with self.assertRaises(ValueError):
            self.promote(approval, edited)
        self.assertFalse(self.catalog.exists())

    def test_an_approval_for_another_catalog_state_is_refused(self):
        approval = self.approval()
        self.promote(approval)
        changed = copy.deepcopy(self.proposal)
        changed["applications"][0]["purpose"] = "A different purpose."
        stale = self.approval(None, changed)
        with self.assertRaises(ValueError):
            self.promote(stale, changed)

    def test_invalid_proposals_are_refused_before_any_write(self):
        broken = copy.deepcopy(self.proposal)
        broken["applications"][0]["assessment"]["evidence"][1]["commit"] = "b" * 40
        approval = self.approval(None, self.proposal)
        approval["proposalSha256"] = digest(broken)
        with self.assertRaises(ValueError):
            self.promote(approval, broken)
        self.assertFalse(self.catalog.exists())

    def test_merge_keeps_entities_the_proposal_does_not_mention(self):
        self.promote(self.approval())
        current = json.loads(self.catalog.read_text(encoding="utf-8"))
        other = copy.deepcopy(self.proposal)
        other["applications"][0]["id"] = "second-app"
        other["openQuestions"] = ["Who owns it?"]
        merged = merge_catalog(other, current)
        self.assertEqual([item["id"] for item in merged["applications"]], ["second-app", "synthetic-app"])
        self.assertEqual(merged["openQuestions"], ["Who owns it?"])
        review = render_catalog_review(other, current, self.draft)
        self.assertIn("Kept, not in this proposal: 1", review)

    def test_cli_approves_and_promotes(self):
        files = {"proposal.json": self.proposal, "draft.json": self.draft, "bundle.json": self.bundle}
        for name, value in files.items():
            (self.base / name).write_text(json.dumps(value), encoding="utf-8")
        tool = [str(ROOT / "tools" / "landscape"), "catalog"]
        review = self.base / "review.md"
        subprocess.run(tool + ["review", str(self.base / "proposal.json"), "--draft", str(self.base / "draft.json"),
                               "--output", str(review)], check=True)
        digest = review_digest(review.read_text(encoding="utf-8"))
        approve = subprocess.run(tool + ["approve", str(self.base / "proposal.json"), "--draft", str(self.base / "draft.json"),
                                         "--current", str(self.catalog), "--reviewed", digest, "--by", "Technical lead",
                                         "--at", APPROVED_AT, "--output", str(self.base / "approval.json")],
                                 capture_output=True, text=True)
        self.assertEqual(approve.returncode, 0, approve.stderr)
        run = subprocess.run(tool + ["promote", str(self.base / "proposal.json"), "--approval", str(self.base / "approval.json"),
                                     "--draft", str(self.base / "draft.json"), "--evidence", str(self.base / "bundle.json"),
                                     "--catalog", str(self.catalog), "--promotions", str(self.promotions)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(self.catalog.exists())
        refused = subprocess.run(tool + ["approve", str(self.base / "proposal.json"), "--draft", str(self.base / "draft.json"),
                                         "--reviewed", "0" * 64, "--by", "x", "--at", APPROVED_AT,
                                         "--output", str(self.base / "other.json")], capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)


if __name__ == "__main__":
    unittest.main()
