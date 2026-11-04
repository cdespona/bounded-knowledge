"""Tests for preparing, running and checking model-backed stages."""

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from landscape_core.stages import COPILOT_FLAGS, check_stage, copilot_command, prepare_stage, render, run_stage  # noqa: E402

CONTRACTS = ROOT / "examples" / "contracts"


def write(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


class StageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.bundle = json.loads((CONTRACTS / "evidence-bundle.valid.json").read_text(encoding="utf-8"))
        self.candidate = json.loads((CONTRACTS / "candidate-envelope.valid.json").read_text(encoding="utf-8"))
        self.evidence = write(self.base / "evidence.json", self.bundle)
        self.candidate_path = write(self.base / "candidate.json", self.candidate)

    def tearDown(self):
        self.directory.cleanup()

    def prepare(self, name="carto"):
        return prepare_stage("cartographer", self.base / name, [self.evidence], analyzed_at="2026-10-29T10:00:00Z")

    def test_prepare_stages_inputs_prompt_agent_and_digests(self):
        outcome = self.prepare()
        stage = Path(outcome["stageDir"])
        repository = self.bundle["repository"]
        self.assertEqual(outcome["inputs"], [repository + ".evidence.json"])
        self.assertIn("inputs/{}.evidence.json".format(repository), (stage / "prompt.txt").read_text(encoding="utf-8"))
        self.assertIn("2026-10-29T10:00:00Z", (stage / "prompt.txt").read_text(encoding="utf-8"))
        self.assertTrue((stage / ".github" / "agents" / "repository-cartographer.agent.md").is_file())
        self.assertIn(repository + ".evidence.json", (stage / "inputs.sha256").read_text(encoding="utf-8"))

    def test_prepare_never_reuses_a_directory(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.prepare()

    def test_prepare_refuses_an_unfilled_or_incomplete_request(self):
        with self.assertRaises(ValueError):
            prepare_stage("cartographer", self.base / "x", [self.evidence])
        with self.assertRaises(ValueError):
            prepare_stage("synthesis", self.base / "y", [self.evidence], [self.candidate_path], analyzed_at="2026-10-29T10:00:00Z")
        with self.assertRaises(ValueError):
            render("cartographer", {})

    def test_curator_prompt_carries_the_scope_of_every_bundle(self):
        outcome = prepare_stage("curator-business", self.base / "cur", [self.evidence], [self.candidate_path])
        prompt = Path(outcome["promptFile"]).read_text(encoding="utf-8")
        self.assertIn(self.bundle["id"], prompt)
        self.assertIn(self.bundle["commit"], prompt)
        self.assertIn("inputs/{}.candidate.json".format(self.bundle["repository"]), prompt)

    def test_copilot_command_is_read_only_and_bounded_to_the_stage_directory(self):
        stage = Path(self.prepare()["stageDir"])
        command = copilot_command(stage)
        self.assertEqual(command[:5], ["copilot", "-C", str(stage.resolve()), "--agent", "repository-cartographer"])
        self.assertEqual(tuple(command[5:5 + len(COPILOT_FLAGS)]), COPILOT_FLAGS)
        self.assertFalse([item for item in command[:-1] if item.startswith("--allow") or item == "--yolo"])
        with self.assertRaises(ValueError):
            copilot_command(self.base)

    def test_run_captures_response_and_stderr_and_reports_failure(self):
        stage = self.prepare()["stageDir"]
        fake = self.base / "fake-copilot"
        fake.write_text("#!/bin/sh\necho '{\"ok\": true}'\necho oops >&2\nexit 3\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        outcome = run_stage(stage, self.base / "out" / "response.txt", 10, executable=str(fake))
        self.assertEqual(outcome["exitCode"], 3)
        self.assertEqual((self.base / "out" / "response.txt").read_text(encoding="utf-8").strip(), '{"ok": true}')
        self.assertEqual((self.base / "out" / "response.stderr.txt").read_text(encoding="utf-8").strip(), "oops")

    def test_check_accepts_a_valid_candidate_and_rejects_prose_and_foreign_bundles(self):
        document, errors = check_stage("cartographer", "Here you go:\n" + json.dumps(self.candidate), [self.evidence])
        self.assertEqual(errors, [])
        self.assertEqual(document["evidenceBundleId"], self.bundle["id"])
        _, errors = check_stage("cartographer", "I will read the bundle now.", [self.evidence])
        self.assertTrue(errors)
        other = dict(self.candidate, evidenceBundleId="0" * 64)
        document, errors = check_stage("cartographer", json.dumps(other), [self.evidence])
        self.assertIsNone(document)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
