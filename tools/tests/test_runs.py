"""Tests for run folders: init, per-selection survey, skip-on-rerun, stop-on-failure and commit drift."""

import contextlib
import io
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
from landscape_core.audit import downgrade_candidate, finding_kinds, validate_audit, validate_revision  # noqa: E402
from landscape_core.candidates import validate_candidate  # noqa: E402
from landscape_core.contracts import validate_caveats  # noqa: E402
from landscape_core.cli import main  # noqa: E402
from landscape_core.stages import prepare_stage, run_stage  # noqa: E402

EXAMPLES = ROOT / "examples"
CREATED_AT = "2026-11-18T10:00:00+01:00"

# A stand-in for Copilot: answers each stage from its staged inputs with a minimal valid document.
FAKE_COPILOT = r'''#!/usr/bin/env python3
import json, pathlib, sys
args = sys.argv[1:]
stage = pathlib.Path(args[args.index("-C") + 1])
agent = args[args.index("--agent") + 1]
inputs = stage / "inputs"
switches = stage.parent
if (switches / "FAIL").exists() or (switches / ("FAIL-" + agent)).exists():
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
                "observationId": item["observationIds"][0]}]},
                {"id": "business-capability", "statement": "The business capability is not stated.",
                "status": "unknown", "confidence": "low", "analyzedAt": "2026-11-18T10:00:00+01:00",
                "counterevidence": [], "evidence": [], "missingEvidence": ["An owner statement."]}]}}))
elif agent == "evidence-auditor":
    # AUDIT: "finding" reports an overreach on selected-file; "contradict" adds a contradicting location.
    candidate = json.loads(next(inputs.glob("*.candidate.json")).read_text())
    claim = candidate["proposedProfile"]["claims"][0]
    mode = (switches / "AUDIT").read_text().strip() if (switches / "AUDIT").exists() else "none"
    findings = []
    if mode in ("finding", "contradict"):
        findings.append({"id": "selected-file-overreach", "kind": "overreach", "claimId": claim["id"],
                         "explanation": "The statement says more than the cited lines show."})
    if mode == "contradict":
        findings[0]["contradicts"] = claim["evidence"]
    print(json.dumps({"schemaVersion": 1, "evidenceBundleId": bundles[0]["id"], "findings": findings}))
elif agent == "candidate-reviser":
    # REVISE: "apply" narrows the named claim, "decline" declines every finding, "unchanged" says applied but
    # changes nothing, "touch" also rewrites an unnamed claim, "upgrade" raises an unnamed claim's status.
    candidate = json.loads(next(inputs.glob("*.candidate.json")).read_text())
    audit = json.loads((inputs / "audit.json").read_text())
    mode = (switches / "REVISE").read_text().strip() if (switches / "REVISE").exists() else "apply"
    claims = {c["id"]: c for c in candidate["proposedProfile"]["claims"]}
    decisions = []
    for finding in audit["findings"]:
        declined = mode == "decline"
        decisions.append({"findingId": finding["id"], "decision": "declined" if declined else "applied",
                          "reason": "The bundle cannot settle it." if declined else "Narrowed the statement."})
        if mode in ("apply", "touch"):
            claims[finding["claimId"]]["statement"] += " (as far as the cited lines show)"
    if mode == "touch":
        claims["business-capability"]["statement"] = "The business capability is orders."
    if mode == "undecided":
        decisions = []
    print(json.dumps({"schemaVersion": 1, "evidenceBundleId": bundles[0]["id"], "decisions": decisions,
                      "candidate": candidate}))
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

    def stage(self, run, stage, application):
        """One per-application stage as cartographer.yaml runs it: up to three run/check attempts."""
        prepared = runs.prepare_run_stage(run, stage, application)
        if prepared["upToDate"]:
            return prepared, []
        stage_dir, response = runs.run_stage_dir(run, stage, application)
        errors = ["not run"]
        while errors and runs.attempts_made(run, stage, application) < runs.MAX_ATTEMPTS:
            run_stage(stage_dir, response, 30, executable=str(self.fake))
            runs.record_attempt(run, stage, application)
            errors = runs.check_run_stage(run, stage, application)[1]
        return prepared, errors

    def cartographer(self, run, application):
        """The whole sub-workflow after discovery; returns the cartographer's prepare result and the first errors."""
        first, errors = self.stage(run, "cartographer", application)
        for stage in ("auditor", "reviser"):
            if errors:
                return first, errors
            errors = self.stage(run, stage, application)[1]
        if not errors:
            runs.downgrade_application(run, application)
        return first, errors

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


class RunAuditTests(RunFixture):
    """The evidence auditor, reviser and downgrade (#30): cartographer -> audit -> reviser -> downgrade."""

    def setup_application(self, audit="none", revise="apply", application="billing", name="pilot"):
        run = self.init([application], name=name)
        loaded = runs.Run(run)
        runs.discover_application(run, application)
        folder = loaded.application_dir(application)
        (folder / "AUDIT").write_text(audit, encoding="utf-8")
        (folder / "REVISE").write_text(revise, encoding="utf-8")
        return run, loaded

    def final(self, loaded, application="billing"):
        document = json.loads(loaded.candidate(application).read_text(encoding="utf-8"))
        return {claim["id"]: claim for claim in document["proposedProfile"]["claims"]}

    def documents(self, loaded, application="billing"):
        return runs._audit_documents(loaded, application)  # bundle, cartographer candidate, audit, revision

    def test_without_findings_the_candidate_passes_through_and_every_output_is_stamped(self):
        run, loaded = self.setup_application()
        self.assertEqual(self.cartographer(run, "billing")[1], [])
        original = json.loads(loaded.cartographer_candidate("billing").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(loaded.candidate("billing").read_text(encoding="utf-8")), original)
        for path in (loaded.cartographer_candidate("billing"), loaded.audit("billing"), loaded.revision("billing"),
                     loaded.candidate("billing")):
            self.assertTrue(path.is_file(), path)
            self.assertTrue(path.with_name(path.name + ".inputs.sha256").is_file(), path)
        self.assertEqual(runs.survey_failures(run), {"failed": []})
        self.assertTrue(runs.downgrade_application(run, "billing")["skipped"])

    def test_an_overreaching_claim_is_revised_and_keeps_its_status(self):
        run, loaded = self.setup_application("finding", "apply")
        self.assertEqual(self.cartographer(run, "billing")[1], [])
        claim = self.final(loaded)["selected-file"]
        self.assertTrue(claim["statement"].endswith("(as far as the cited lines show)"))
        self.assertEqual((claim["status"], claim["confidence"]), ("confirmed", "high"))
        self.assertNotIn("caveats", claim)
        revision = json.loads(loaded.revision("billing").read_text(encoding="utf-8"))
        self.assertEqual([item["decision"] for item in revision["decisions"]], ["applied"])
        self.assertEqual(runs.audit_summary(loaded, "billing")["downgrades"], 0)

    def test_a_declined_finding_downgrades_the_claim_and_leaves_a_caveat_that_resolves_into_the_audit(self):
        run, loaded = self.setup_application("contradict", "decline")
        self.assertEqual(self.cartographer(run, "billing")[1], [])
        claim = self.final(loaded)["selected-file"]
        self.assertEqual((claim["status"], claim["confidence"]), ("inferred", "medium"))
        self.assertEqual([(item["findingId"], item["kind"]) for item in claim["caveats"]],
                         [("selected-file-overreach", "overreach")])
        self.assertIn("The bundle cannot settle it.", claim["caveats"][0]["explanation"])
        bundle, _original, audit, _revision = self.documents(loaded)
        self.assertEqual([item["id"] for item in audit["findings"]], ["selected-file-overreach"])
        # The contradicting location is also recorded as counterevidence.
        self.assertEqual(claim["counterevidence"], audit["findings"][0]["contradicts"])
        summary = runs.audit_summary(loaded, "billing")
        self.assertEqual((summary["declined"], summary["downgrades"], summary["caveats"]), (1, 1, 1))
        # A caveat must resolve into audit.json.
        final = json.loads(loaded.candidate("billing").read_text(encoding="utf-8"))
        self.assertEqual(validate_candidate(final, bundle, {"selected-file-overreach": "overreach"}), [])
        errors = validate_candidate(final, bundle, {})
        self.assertTrue(any("does not resolve into audit.json" in error for error in errors), errors)

    def test_a_finding_applied_without_changing_its_claim_counts_as_unaddressed(self):
        run, loaded = self.setup_application("finding", "unchanged")
        self.assertEqual(self.cartographer(run, "billing")[1], [])
        self.assertEqual(self.final(loaded)["selected-file"]["status"], "inferred")
        summary = runs.audit_summary(loaded, "billing")
        self.assertEqual((summary["applied"], summary["unaddressed"], summary["downgrades"]), (1, 1, 1))

    def test_several_unresolved_findings_lower_a_claim_to_unknown_and_never_below(self):
        run, loaded = self.setup_application("finding", "decline")
        self.cartographer(run, "billing")
        bundle, original, audit, revision = self.documents(loaded)
        for name in ("second-finding", "third-finding"):
            audit["findings"].append(dict(audit["findings"][0], id=name))
            revision["decisions"].append({"findingId": name, "decision": "declined", "reason": "No."})
        final, summary = downgrade_candidate(original, audit, revision)
        claim = {item["id"]: item for item in final["proposedProfile"]["claims"]}["selected-file"]
        self.assertEqual((claim["status"], claim["confidence"], summary["downgrades"], summary["caveats"]),
                         ("unknown", "low", 2, 3))
        self.assertTrue(claim["missingEvidence"])
        self.assertEqual(validate_candidate(final, bundle, finding_kinds(audit)), [])

    def test_a_reviser_that_touches_a_claim_no_finding_named_is_rejected(self):
        run, loaded = self.setup_application("finding", "touch")
        _prepared, errors = self.cartographer(run, "billing")
        self.assertTrue(any("business-capability was changed but no applied finding names it" in error
                            for error in errors), errors)
        self.assertEqual(runs.attempts_made(run, "reviser", "billing"), runs.MAX_ATTEMPTS)
        self.assertFalse(loaded.revision("billing").exists())
        self.assertFalse(loaded.candidate("billing").exists())
        self.assertEqual(runs.survey_failures(run), {"failed": ["billing"]})

    def test_a_revision_must_decide_every_finding(self):
        run, _loaded = self.setup_application("finding", "undecided")
        errors = self.cartographer(run, "billing")[1]
        self.assertTrue(any("has no decision" in error for error in errors), errors)

    def test_a_revision_may_not_raise_a_status_add_unsupported_claims_or_write_caveats(self):
        run, loaded = self.setup_application("finding", "apply")
        self.cartographer(run, "billing")
        bundle, original, audit, revision = self.documents(loaded)
        self.assertEqual(validate_revision(revision, original, audit, bundle), [])
        claims = revision["candidate"]["proposedProfile"]["claims"]
        by_id = {item["id"]: item for item in claims}
        by_id["business-capability"]["status"] = "confirmed"
        by_id["business-capability"]["evidence"] = by_id["selected-file"]["evidence"]
        errors = validate_revision(revision, original, audit, bundle)
        self.assertTrue(any("no applied finding names it" in error for error in errors), errors)
        claims.append(dict(by_id["selected-file"], id="extra-claim"))
        self.assertTrue(any("extra-claim was added without an applied missed-evidence finding" in error
                            for error in validate_revision(revision, original, audit, bundle)))
        claims[0]["caveats"] = [{"findingId": "x", "kind": "overreach", "explanation": "y"}]
        self.assertTrue(any("only the downgrade writes them" in error
                            for error in validate_revision(revision, original, audit, bundle)))
        # Raising the status of a named claim is refused too.
        run, loaded = self.setup_application("finding", "apply", "order-management", "second")
        self.cartographer(run, "order-management")
        bundle, original, audit, revision = self.documents(loaded, "order-management")
        audit["findings"][0]["claimId"] = "business-capability"
        revision["candidate"]["proposedProfile"]["claims"][1].update(
            status="confirmed", evidence=revision["candidate"]["proposedProfile"]["claims"][0]["evidence"])
        self.assertTrue(any("upgraded" in error for error in validate_revision(revision, original, audit, bundle)))

    def test_a_missed_evidence_finding_lets_the_reviser_add_a_claim_citing_it(self):
        run, loaded = self.setup_application("finding", "apply")
        self.cartographer(run, "billing")
        bundle, original, audit, revision = self.documents(loaded)
        reference = original["proposedProfile"]["claims"][0]["evidence"][0]
        audit["findings"] = [{"id": "missed-file", "kind": "missed-evidence", "explanation": "Uncited content.",
                              "citations": [reference]}]
        self.assertEqual(validate_audit(audit, original, bundle), [])
        revision["decisions"] = [{"findingId": "missed-file", "decision": "applied", "reason": "Added a claim."}]
        added = dict(original["proposedProfile"]["claims"][0], id="added-claim", statement="Also present.")
        revision["candidate"] = json.loads(json.dumps(original))
        revision["candidate"]["proposedProfile"]["claims"].append(added)
        self.assertEqual(validate_revision(revision, original, audit, bundle), [])
        final, summary = downgrade_candidate(original, audit, revision)
        self.assertEqual((summary["unaddressed"], summary["downgrades"], summary["unattached"]), (0, 0, []))
        revision["decisions"][0]["decision"] = "declined"
        self.assertTrue(validate_revision(revision, original, audit, bundle))  # a declined finding adds nothing
        revision["candidate"] = json.loads(json.dumps(original))
        final, summary = downgrade_candidate(original, audit, revision)
        self.assertEqual((summary["declined"], summary["unattached"]), (1, ["missed-file"]))

    def test_an_audit_must_point_at_real_claims_and_bundle_lines(self):
        run, loaded = self.setup_application("finding")
        self.cartographer(run, "billing")
        bundle, original, audit, _revision = self.documents(loaded)
        self.assertEqual(validate_audit(audit, original, bundle), [])
        broken = json.loads(json.dumps(audit))
        broken["findings"] = [
            {"id": "a", "kind": "overreach", "explanation": "x", "claimId": "no-such-claim"},
            {"id": "b", "kind": "overreach", "explanation": "x"},
            {"id": "c", "kind": "missed-evidence", "explanation": "x", "claimId": "selected-file"},
            {"id": "d", "kind": "missed-evidence", "explanation": "x", "citations": [
                {"repository": bundle["repository"], "commit": bundle["commit"], "path": "nowhere.txt",
                 "lines": "1", "observationId": "0" * 64}]},
            {"id": "e", "kind": "overreach", "explanation": "x", "claimId": "selected-file", "contradicts": []}]
        errors = " | ".join(validate_audit(broken, original, bundle))
        for expected in ("names no claim", "must name a claim", "must cite the bundle", "outside the bundle",
                         "must be a non-empty array"):
            self.assertIn(expected, errors)
        twice = dict(audit, findings=[audit["findings"][0]] * 2)
        self.assertTrue(any("duplicated" in error for error in validate_audit(twice, original, bundle)))

    def test_an_auditor_that_fails_its_check_fails_the_application_and_stops_the_survey(self):
        run = self.init()
        loaded = runs.Run(run)
        loaded.application_dir("billing").mkdir(parents=True, exist_ok=True)
        (loaded.application_dir("billing") / "FAIL-evidence-auditor").write_text("", encoding="utf-8")
        self.assertIsNone(self.survey(run))
        self.assertEqual(runs.survey_failures(run), {"failed": ["billing"]})
        self.assertEqual(runs.attempts_made(run, "auditor", "billing"), runs.MAX_ATTEMPTS)
        self.assertTrue(loaded.cartographer_candidate("billing").is_file())
        self.assertFalse(loaded.audit("billing").exists())
        self.assertFalse(loaded.stage_dir("reviser", "billing").exists())  # nothing after the failed stage ran
        self.assertFalse(loaded.candidate("billing").exists())
        self.assertFalse(loaded.questionnaire.exists())
        self.assertTrue(loaded.candidate("order-management").is_file())  # the other application finished
        self.assertEqual(main(["run", "survey-check", str(run)]), 1)

    def test_check_reports_exhaustion_only_after_three_attempts_and_prepare_resets_the_count(self):
        run, _loaded = self.setup_application("finding", "touch")
        self.stage(run, "cartographer", "billing")
        self.stage(run, "auditor", "billing")
        runs.prepare_run_stage(run, "reviser", "billing")
        stage_dir, response = runs.run_stage_dir(run, "reviser", "billing")
        output = io.StringIO()
        for attempt in (1, 2, 3):
            run_stage(stage_dir, response, 30, executable=str(self.fake))
            runs.record_attempt(run, "reviser", "billing")
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["stage", "check", "reviser", "--run", str(run), "--application", "billing"]), 1)
            result = json.loads("{" + output.getvalue().rsplit("{", 1)[-1])
            self.assertEqual((result["attempts"], result["exhausted"]), (attempt, attempt == 3))
        runs.prepare_run_stage(run, "reviser", "billing")
        self.assertEqual(runs.attempts_made(run, "reviser", "billing"), 0)

    def test_rerun_hashes_each_stage_input_so_a_changed_candidate_redoes_audit_and_revision(self):
        run, loaded = self.setup_application("finding", "apply")
        self.cartographer(run, "billing")
        for stage in ("cartographer", "auditor", "reviser"):
            self.assertTrue(runs.prepare_run_stage(run, stage, "billing")["upToDate"], stage)
        path = loaded.cartographer_candidate("billing")
        document = json.loads(path.read_text(encoding="utf-8"))
        document["proposedProfile"]["openQuestions"].append("A new question?")
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.assertTrue(runs.prepare_run_stage(run, "cartographer", "billing")["upToDate"])
        self.assertFalse(runs.prepare_run_stage(run, "auditor", "billing")["upToDate"])
        self.assertFalse(runs.prepare_run_stage(run, "reviser", "billing")["upToDate"])
        self.assertEqual(runs.survey_failures(run), {"failed": ["billing"]})

    def test_the_new_stage_folders_hold_their_own_agent_and_inputs_only(self):
        run, loaded = self.setup_application("finding")
        self.cartographer(run, "billing")
        for stage, agent, names in (
                ("auditor", "evidence-auditor", {"synthetic-kotlin-service.evidence.json",
                                                 "synthetic-kotlin-service.candidate.json"}),
                ("reviser", "candidate-reviser", {"synthetic-kotlin-service.evidence.json",
                                                  "synthetic-kotlin-service.candidate.json", "audit.json"})):
            folder = loaded.stage_dir(stage, "billing")
            self.assertEqual([path.name for path in (folder / ".github" / "agents").iterdir()], [agent + ".agent.md"])
            self.assertEqual({path.name for path in (folder / "inputs").iterdir()}, names)
        with self.assertRaisesRegex(ValueError, "needs --audit"):
            prepare_stage("reviser", self.base / "x", [loaded.evidence("billing", "billing-code")],
                          [loaded.cartographer_candidate("billing")])

    def test_the_review_reports_findings_downgrades_and_caveats_per_application(self):
        run, loaded = self.setup_application("finding", "decline")
        self.cartographer(run, "billing")
        lines = "\n".join(runs.evidence_audit_lines(loaded))
        self.assertIn("| billing | 1 | 0 | 1 | 0 | 1 | 1 | 0 |", lines)


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


class CaveatShapeTests(unittest.TestCase):
    def test_caveats_have_exactly_three_fields_and_resolve_only_into_known_findings(self):
        good = [{"findingId": "a-finding", "kind": "overreach", "explanation": "Why."}]
        self.assertEqual(validate_caveats(good, "caveats"), [])
        self.assertEqual(validate_caveats(good, "caveats", {"a-finding": "overreach"}), [])
        self.assertTrue(validate_caveats(good, "caveats", {"a-finding": "missed-evidence"}))
        self.assertTrue(validate_caveats(good, "caveats", {}))
        self.assertTrue(validate_caveats([dict(good[0], extra=1)], "caveats"))
        self.assertTrue(validate_caveats([dict(good[0], kind="doubt")], "caveats"))
        self.assertTrue(validate_caveats([], "caveats"))


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
