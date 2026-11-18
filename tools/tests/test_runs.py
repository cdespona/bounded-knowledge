"""Tests for run folders: init, per-selection survey, skip-on-rerun, stop-on-failure and commit drift."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from landscape_core import runs  # noqa: E402
from landscape_core.cli import main  # noqa: E402
from landscape_core.stages import run_stage  # noqa: E402

EXAMPLES = ROOT / "examples"
CREATED_AT = "2026-11-18T10:00:00+01:00"

# A stand-in for Copilot: answers each stage from its staged inputs with a minimal valid document.
FAKE_COPILOT = r'''#!/usr/bin/env python3
import json, pathlib, sys
args = sys.argv[1:]
stage = pathlib.Path(args[args.index("-C") + 1])
agent = args[args.index("--agent") + 1]
inputs = stage / "inputs"
if (stage.parent / "FAIL").exists():
    print("not json"); sys.exit(0)
bundles = [json.loads(p.read_text()) for p in sorted(inputs.glob("*.evidence.json"))]
if agent == "repository-cartographer":
    bundle = bundles[0]
    item = next(entry for entry in bundle["selectedEvidence"] if entry.get("observationIds"))
    print(json.dumps({"schemaVersion": 1, "repository": bundle["repository"], "kind": bundle["kind"],
        "analyzedCommit": bundle["commit"], "evidenceBundleId": bundle["id"],
        "proposedProfile": {"schemaVersion": 1, "repository": bundle["repository"], "kind": bundle["kind"],
            "analyzedCommit": bundle["commit"], "openQuestions": ["What business capability is this?"],
            "claims": [{"id": "selected-file", "statement": "The repository contains " + item["path"] + ".",
                "status": "confirmed", "confidence": "high", "analyzedAt": "2026-11-18T10:00:00+01:00",
                "counterevidence": [], "evidence": [{"repository": bundle["repository"],
                "commit": bundle["commit"], "path": item["path"], "lines": item["lines"],
                "observationId": item["observationIds"][0]}]}]}}))
elif agent == "questionnaire-curator":
    candidates = [json.loads(p.read_text()) for p in sorted(inputs.glob("*.candidate.json"))]
    links = [{"type": "claim", "claimId": "selected-file", "repository": b["repository"], "commit": b["commit"],
              "evidenceBundleId": b["id"]} for b in bundles]
    print(json.dumps({"schemaVersion": 1, "respondent": {"id": "technical-lead", "role": "technical lead"},
        "scope": [{"repository": b["repository"], "commit": b["commit"], "evidenceBundleId": b["id"]} for b in bundles],
        "questions": [{"id": "human-capability", "reviewType": "human-only", "answerStatus": "unanswered",
            "wording": "Which business capability does each service serve?",
            "whyCodeCannotSettle": "Business intent is not in code.", "links": links}]}))
elif agent == "catalog-synthesizer":
    answers = json.loads((inputs / "answers.json").read_text())
    question = next(item for item in answers["questions"] if item["answerStatus"] == "answered")
    catalog = {"schemaVersion": 1, "relationships": [], "openQuestions": [], "applications": [],
               "boundedContexts": [], "deployables": [], "strategicCapabilities": [], "terms": [], "useCases": []}
    for bundle in bundles:
        entry = next(item for item in bundle["selectedEvidence"] if item.get("observationIds"))
        catalog["applications"].append({"id": bundle["repository"], "name": bundle["repository"],
            "purpose": "Serves the capability named by the technical lead.",
            "assessment": {"status": "inferred", "confidence": "medium", "analyzedAt": "2026-11-18T10:00:00+01:00",
                "counterevidence": [], "evidence": [
                {"kind": "curated", "sourceId": question["id"], "sourceType": "interview",
                 "suppliedBy": question["answer"]["answerer"], "recordedAt": question["answer"]["recordedAt"]},
                {"kind": "repository", "repositoryId": bundle["repository"], "commit": bundle["commit"],
                 "path": entry["path"], "lines": entry["lines"], "observationId": entry["observationIds"][0]}]}})
    print(json.dumps(catalog))
else:
    print("unsupported agent"); sys.exit(1)
'''


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


def make_repository(target, fixture):
    shutil.copytree(fixture, target)
    git(target, "init", "-q")
    git(target, "config", "user.email", "fixture@example.invalid")
    git(target, "config", "user.name", "Fixture")
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "fixture")
    return target


def sort_topology(topology):
    topology = dict(topology)
    topology["sourceSelections"] = sorted(topology["sourceSelections"], key=lambda item: item["id"])
    topology["bindings"] = sorted(topology["bindings"],
                                  key=lambda item: (item["sourceSelectionId"], item["target"]["type"], item["target"]["id"]))
    return topology


class RunFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name).resolve()
        self.java = make_repository(self.base / "repos" / "java", EXAMPLES / "synthetic-java-service")
        self.kotlin = make_repository(self.base / "repos" / "kotlin", EXAMPLES / "synthetic-kotlin-service")
        self.k8s = make_repository(self.base / "repos" / "k8s", EXAMPLES / "synthetic-kubernetes-manifests")
        self.sources = self.write("sources.json", {"schemaVersion": 1, "repositories": [
            {"id": "k8s-manifests", "kind": "kubernetes", "enabled": True, "path": str(self.k8s)},
            {"id": "synthetic-java-service", "kind": "application", "enabled": True, "path": str(self.java)},
            {"id": "synthetic-kotlin-service", "kind": "application", "enabled": True, "path": str(self.kotlin)},
            {"id": "unused-service", "kind": "application", "enabled": False, "path": str(self.base / "nowhere")}]})
        manual = {"method": "manual", "detail": "Mapped by the fixture."}
        self.topology = self.write("topology.json", {"schemaVersion": 1, "repositories": [
            {"id": "k8s-manifests", "kind": "kubernetes"},
            {"id": "synthetic-java-service", "kind": "application"},
            {"id": "synthetic-kotlin-service", "kind": "application"}], "sourceSelections": [
            {"id": "order-api-production-kubernetes", "kind": "kubernetes", "repositoryId": "k8s-manifests",
             "subpath": "k8s-capside/pro/mango/orders/order-api"},
            {"id": "order-code", "kind": "application", "repositoryId": "synthetic-java-service", "subpath": "."},
            {"id": "billing-code", "kind": "application", "repositoryId": "synthetic-kotlin-service", "subpath": "."}],
            "bindings": [
            {"sourceSelectionId": "order-code", "status": "declared", "provenance": manual,
             "target": {"type": "application", "id": "order-management"}},
            {"sourceSelectionId": "order-api-production-kubernetes", "status": "declared", "provenance": manual,
             "target": {"type": "application", "id": "order-management"}},
            {"sourceSelectionId": "billing-code", "status": "declared", "provenance": manual,
             "target": {"type": "application", "id": "billing"}}]})
        self.runs_dir = self.base / "runs"
        self.fake = self.base / "fake-copilot"
        self.fake.write_text(FAKE_COPILOT, encoding="utf-8")
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)

    def tearDown(self):
        self.directory.cleanup()

    def write(self, name, document):
        if "sourceSelections" in document:
            document = sort_topology(document)
        path = self.base / name
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return path

    def init(self, applications=("order-management", "billing"), name="pilot"):
        return Path(runs.init_run(name, self.topology, list(applications), self.sources, runs_dir=self.runs_dir,
                                  created_at=CREATED_AT)["run"])

    def cartographer(self, run, application):
        prepared = runs.prepare_run_stage(run, "cartographer", application)
        if prepared["upToDate"]:
            return prepared, None
        stage_dir, response = runs.run_stage_dir(run, "cartographer", application)
        run_stage(stage_dir, response, 30, executable=str(self.fake))
        return prepared, runs.check_run_stage(run, "cartographer", application)[1]

    def survey(self, run):
        for application in runs.run_applications(run)["applications"]:
            runs.discover_application(run, application)
            self.cartographer(run, application)
        if runs.survey_failures(run)["failed"]:
            return None
        if not runs.prepare_run_stage(run, "curator-business")["upToDate"]:
            stage_dir, response = runs.run_stage_dir(run, "curator-business")
            run_stage(stage_dir, response, 30, executable=str(self.fake))
            self.assertEqual(runs.check_run_stage(run, "curator-business")[1], [])
        return runs.build_questionnaire(run)


class RunInitTests(RunFixture):
    def test_init_resolves_applications_through_topology_bindings(self):
        run = self.init()
        self.assertEqual(run.name, "2026-11-18-pilot")
        manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(runs.run_manifest_errors(manifest), [])
        self.assertEqual([item["id"] for item in manifest["applications"]], ["billing", "order-management"])
        order = manifest["applications"][1]
        self.assertEqual([(item["id"], item["kind"]) for item in order["selections"]],
                         [("order-api-production-kubernetes", "kubernetes"), ("order-code", "application")])
        self.assertEqual(manifest["respondent"], {"id": "technical-lead", "role": "technical lead"})
        frozen = json.loads((run / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in frozen["repositories"]],
                         ["k8s-manifests", "synthetic-java-service", "synthetic-kotlin-service"])
        self.assertTrue((run / "topology.json").is_file())
        for part in ("survey", "answers", "synthesis", "review"):
            self.assertTrue((run / part).is_dir())

    def test_init_refuses_an_existing_run_unknown_applications_and_bad_ids(self):
        self.init()
        with self.assertRaisesRegex(ValueError, "start a new run"):
            self.init()
        with self.assertRaisesRegex(ValueError, "no topology selection"):
            self.init(["inventory"], name="other")
        with self.assertRaisesRegex(ValueError, "must match"):
            self.init(["Order_Management"], name="third")

    def test_an_application_needs_exactly_one_whole_repository_application_selection(self):
        topology = json.loads(self.topology.read_text(encoding="utf-8"))
        code = next(item for item in topology["sourceSelections"] if item["id"] == "order-code")
        code["subpath"] = "src"
        self.write("topology.json", topology)
        with self.assertRaisesRegex(ValueError, "whole repository"):
            self.init(["order-management"])
        code["subpath"] = "."
        topology["bindings"] = [item for item in topology["bindings"] if item["sourceSelectionId"] != "order-code"]
        self.write("topology.json", topology)
        with self.assertRaisesRegex(ValueError, "exactly one application selection"):
            self.init(["order-management"])

    def test_cli_init_and_applications_print_json(self):
        code = main(["run", "init", "pilot", "--topology", str(self.topology), "--sources", str(self.sources),
                     "--application", "billing", "--runs-dir", str(self.runs_dir), "--created-at", CREATED_AT])
        self.assertEqual(code, 0)
        run = self.runs_dir / "2026-11-18-pilot"
        self.assertEqual(runs.run_applications(run), {"applications": ["billing"]})
        self.assertEqual(main(["run", "check", str(run)]), 0)


class RunSurveyTests(RunFixture):
    def test_discovery_writes_every_selection_at_fixed_paths_and_pins_commits(self):
        run = self.init()
        result = runs.discover_application(run, "order-management")
        self.assertFalse(result["skipped"])
        loaded = runs.Run(run)
        for selection in ("order-code", "order-api-production-kubernetes"):
            self.assertTrue(loaded.inventory("order-management", selection).is_file())
            self.assertTrue(loaded.evidence("order-management", selection).is_file())
        discovery = json.loads(loaded.discovery("order-management").read_text(encoding="utf-8"))
        self.assertEqual({item["repository"] for item in discovery["selections"]},
                         {"synthetic-java-service", "k8s-manifests"})
        self.assertTrue(runs.discover_application(run, "order-management")["skipped"])

    def test_a_full_survey_writes_questionnaire_and_answer_sheet_once(self):
        run = self.init()
        result = self.survey(run)
        loaded = runs.Run(run)
        self.assertTrue(loaded.questionnaire.is_file())
        self.assertTrue(loaded.questionnaire_markdown.is_file())
        self.assertEqual(result["answersSheet"], "written")
        sheet = loaded.answers_sheet.read_text(encoding="utf-8")
        loaded.answers_sheet.write_text(sheet + "\n", encoding="utf-8")
        self.assertEqual(runs.build_questionnaire(run)["answersSheet"], "kept")
        self.assertTrue(runs.build_questionnaire(run)["skipped"])
        self.assertTrue(loaded.answers_sheet.read_text(encoding="utf-8").endswith("\n\n"))

    def test_rerun_skips_stages_whose_inputs_are_unchanged_and_redoes_changed_ones(self):
        run = self.init()
        self.survey(run)
        loaded = runs.Run(run)
        prepared, _errors = self.cartographer(run, "billing")
        self.assertTrue(prepared["upToDate"])
        self.assertTrue(runs.prepare_run_stage(run, "curator-business")["upToDate"])
        evidence = loaded.evidence("billing", "billing-code")
        evidence.write_text(evidence.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        prepared, errors = self.cartographer(run, "billing")
        self.assertFalse(prepared["upToDate"])
        self.assertEqual(errors, [])
        self.assertFalse(runs.prepare_run_stage(run, "curator-business")["upToDate"])

    def test_the_survey_stops_and_names_every_application_whose_cartographer_failed(self):
        run = self.init()
        (runs.Run(run).application_dir("billing")).mkdir(parents=True, exist_ok=True)
        (runs.Run(run).application_dir("billing") / "FAIL").write_text("", encoding="utf-8")
        self.assertIsNone(self.survey(run))
        self.assertEqual(runs.survey_failures(run), {"failed": ["billing"]})
        self.assertFalse(runs.Run(run).questionnaire.exists())
        self.assertTrue(runs.Run(run).candidate("order-management").is_file())
        self.assertEqual(main(["run", "survey-check", str(run)]), 1)
        with self.assertRaisesRegex(ValueError, "missing inputs"):
            runs.prepare_run_stage(run, "curator-business")

    def test_a_shared_repository_that_moves_during_the_survey_is_refused(self):
        topology = json.loads(self.topology.read_text(encoding="utf-8"))
        topology["bindings"].append({"sourceSelectionId": "order-api-production-kubernetes", "status": "declared",
                                     "provenance": {"method": "manual", "detail": "Shared."},
                                     "target": {"type": "application", "id": "billing"}})
        self.write("topology.json", topology)
        run = self.init()
        runs.discover_application(run, "order-management")
        (self.k8s / "NOTES.txt").write_text("moved\n", encoding="utf-8")
        git(self.k8s, "add", "-A")
        git(self.k8s, "commit", "-q", "-m", "move")
        with self.assertRaisesRegex(ValueError, "moved during the survey"):
            runs.discover_application(run, "billing")

    def test_cli_stage_commands_take_only_the_run_folder(self):
        run = self.init(["billing"])
        self.assertEqual(main(["run", "discover", str(run), "--application", "billing"]), 0)
        self.assertEqual(main(["stage", "prepare", "cartographer", "--run", str(run), "--application", "billing"]), 0)
        self.assertNotEqual(main(["stage", "prepare", "cartographer", "--run", str(run), "--evidence", "x.json"]), 0)


class RunReviewTests(RunFixture):
    def answer(self, run):
        loaded = runs.Run(run)
        sheet = loaded.answers_sheet.read_text(encoding="utf-8")
        head, _, question = sheet.partition("## human-capability")  # the last section; binding questions stay open
        head = head.replace("Answerer: ", "Answerer: Technical lead", 1)
        question = question.replace("Status: unanswered", "Status: answered").rstrip("\n") + "\nOrders and billing.\n"
        sheet = head + "## human-capability" + question
        loaded.answers_sheet.write_text(sheet, encoding="utf-8")

    def synthesize(self, run, catalog):
        self.assertFalse(runs.record_run_answers(run, recorded_at=CREATED_AT)["skipped"])
        self.assertFalse(runs.prepare_run_stage(run, "synthesis", current=catalog)["upToDate"])
        stage_dir, response = runs.run_stage_dir(run, "synthesis")
        run_stage(stage_dir, response, 30, executable=str(self.fake))
        self.assertEqual(runs.check_run_stage(run, "synthesis", current=catalog)[1], [])
        runs.link_run(run)
        return runs.review_run(run, catalog)

    def test_synthesis_and_promotion_run_from_the_run_folder_alone(self):
        run = self.init()
        self.survey(run)
        self.answer(run)
        catalog = self.base / "catalog" / "landscape-catalog.json"
        result = self.synthesize(run, catalog)
        loaded = runs.Run(run)
        self.assertEqual(result["drift"], {})
        self.assertIn("Every repository is still at the commit", loaded.report.read_text(encoding="utf-8"))
        digest = loaded.review.read_text(encoding="utf-8").strip().splitlines()[-1].split()[-1].strip("`")
        self.assertEqual(main(["catalog", "approve", "--run", str(run), "--current", str(catalog), "--reviewed", digest,
                               "--by", "Technical lead"]), 0)
        self.assertTrue(loaded.approval.is_file())
        self.assertEqual(main(["catalog", "promote", "--run", str(run), "--catalog", str(catalog),
                               "--promotions", str(self.base / "catalog" / "promotions")]), 0)
        promoted = json.loads(catalog.read_text(encoding="utf-8"))
        self.assertEqual(sorted(item["id"] for item in promoted["applications"]),
                         ["synthetic-java-service-app", "synthetic-kotlin-service-app"])
        # Rerunning synthesis with nothing changed skips every step.
        self.assertTrue(runs.record_run_answers(run)["skipped"])
        self.assertTrue(runs.link_run(run)["skipped"])


    def test_review_report_warns_about_each_application_whose_repository_moved(self):
        run = self.init()
        for application in ("billing", "order-management"):
            runs.discover_application(run, application)
        loaded = runs.Run(run)
        self.assertEqual(runs.commit_drift(loaded), {})
        (self.kotlin / "CHANGELOG.md").write_text("later\n", encoding="utf-8")
        git(self.kotlin, "add", "-A")
        git(self.kotlin, "commit", "-q", "-m", "later")
        drift = runs.commit_drift(loaded)
        self.assertEqual(list(drift), ["billing"])
        self.assertEqual(drift["billing"][0]["moved"], "1 commits")
        self.assertEqual(drift["billing"][0]["selection"], "billing-code")


class RunManifestTests(unittest.TestCase):
    def test_the_manifest_rejects_unsorted_or_incomplete_entries(self):
        manifest = {"schemaVersion": 1, "name": "2026-11-18-pilot", "createdAt": CREATED_AT,
                    "respondent": {"id": "technical-lead", "role": "technical lead"},
                    "applications": [{"id": "b", "selections": [
                        {"id": "b-code", "kind": "application", "repository": "b-repo", "subpath": "."}]},
                        {"id": "a", "selections": [
                        {"id": "a-k8s", "kind": "kubernetes", "repository": "k8s", "subpath": "x"}]}]}
        errors = runs.run_manifest_errors(manifest)
        self.assertIn("applications[1] must have exactly one application selection", errors)
        self.assertIn("applications must be sorted by id without duplicates", errors)

    def test_the_schema_file_names_the_same_fields(self):
        schema = json.loads((ROOT / "schemas" / "run-manifest.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), {"schemaVersion", "name", "createdAt", "respondent", "applications"})


if __name__ == "__main__":
    unittest.main()
