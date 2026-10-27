"""Tests for deterministic assembly of bounded questionnaire batch responses."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from landscape_core.contracts import validate_questionnaire_draft  # noqa: E402
from landscape_core.questionnaires import answers_template, answers_template_markdown, parse_answers_markdown, record_answers, assemble_questionnaire, plan_extracted_files, consolidate_questionnaire, extract_questionnaire_response, render_questionnaire  # noqa: E402

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
BUNDLE_A = "1" * 64
BUNDLE_B = "2" * 64
RESPONDENT = {"id": "technical-lead", "role": "Technical lead"}


def question(question_id, repository, commit, bundle, review_type="human-only"):
    return {
        "id": question_id,
        "wording": "Question {}?".format(question_id),
        "reviewType": review_type,
        "links": [{"type": "claim", "claimId": "claim-" + question_id,
                   "repository": repository, "commit": commit, "evidenceBundleId": bundle}],
        "whyCodeCannotSettle": "Evidence is silent.",
        "answerStatus": "unanswered",
    }


def batch(questions, scope=(("repo-a", COMMIT_A, BUNDLE_A),), respondent=RESPONDENT):
    return {
        "schemaVersion": 1,
        "respondent": dict(respondent),
        "scope": [{"repository": r, "commit": c, "evidenceBundleId": b} for r, c, b in scope],
        "questions": questions,
    }


def text(document):
    return "Here is the batch.\n" + json.dumps(document, indent=2)


class AssembleQuestionnaireTest(unittest.TestCase):
    def setUp(self):
        self.first = batch([question("q-b", "repo-a", COMMIT_A, BUNDLE_A)])
        self.second = batch(
            [question("q-a", "repo-b", COMMIT_B, BUNDLE_B, "code-answerable")],
            scope=(("repo-b", COMMIT_B, BUNDLE_B),),
        )

    def assemble(self, *documents):
        return assemble_questionnaire(
            [("batch-{}".format(index), text(document)) for index, document in enumerate(documents)]
        )

    def test_merges_batches_into_one_valid_sorted_draft(self):
        draft = self.assemble(self.first, self.second)
        self.assertEqual([], validate_questionnaire_draft(draft))
        self.assertEqual(["q-a", "q-b"], [item["id"] for item in draft["questions"]])
        self.assertEqual(["repo-a", "repo-b"], [item["repository"] for item in draft["scope"]])
        self.assertIn("q-a", render_questionnaire(draft))

    def test_output_is_independent_of_batch_order(self):
        self.assertEqual(self.assemble(self.first, self.second), self.assemble(self.second, self.first))

    def test_shared_scope_pin_is_compatible(self):
        extra = batch([question("q-c", "repo-a", COMMIT_A, BUNDLE_A)])
        self.assertEqual(3, len(self.assemble(self.first, self.second, extra)["questions"]))

    def test_rejects_conflicting_scope_pin(self):
        other = batch([question("q-c", "repo-a", COMMIT_B, BUNDLE_B)], scope=(("repo-a", COMMIT_B, BUNDLE_B),))
        with self.assertRaisesRegex(ValueError, "scope pin for repo-a conflicts"):
            self.assemble(self.first, other)

    def test_rejects_different_respondent(self):
        other = copy.deepcopy(self.second)
        other["respondent"] = {"id": "platform-owner", "role": "Platform owner"}
        with self.assertRaisesRegex(ValueError, "respondent differs"):
            self.assemble(self.first, other)

    def test_rejects_duplicate_question_ids_even_when_identical(self):
        with self.assertRaisesRegex(ValueError, "question id q-b is duplicated"):
            self.assemble(self.first, copy.deepcopy(self.first))

    def test_rejects_model_supplied_answers(self):
        answered = copy.deepcopy(self.first)
        answered["questions"][0].update(
            answerStatus="answered",
            answer={"text": "Invented", "answerer": "model", "recordedAt": "2026-10-21T10:00:00Z"},
        )
        with self.assertRaisesRegex(ValueError, "must not contain answers"):
            self.assemble(answered)

    def test_rejects_truncated_empty_and_multiple_object_responses(self):
        complete = text(self.first)
        for name, response in (
            ("truncated", complete[: len(complete) // 2]),
            ("empty", "No JSON produced."),
            ("two", complete + "\n" + json.dumps(self.second)),
        ):
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, "batch-0"):
                    assemble_questionnaire([("batch-0", response)])

    def test_one_bad_batch_rejects_everything(self):
        with self.assertRaises(ValueError):
            assemble_questionnaire([("good", text(self.first)), ("bad", "{")])

    def test_rejects_link_outside_batch_scope_and_no_batches(self):
        outside = batch([question("q-c", "repo-z", COMMIT_A, BUNDLE_A)])
        with self.assertRaisesRegex(ValueError, "outside scope"):
            self.assemble(outside)
        with self.assertRaisesRegex(ValueError, "at least one"):
            assemble_questionnaire([])


class ConsolidateQuestionnaireTest(unittest.TestCase):
    def setUp(self):
        self.original = assemble_questionnaire([("b", text(batch([
            question("q-1", "repo-a", COMMIT_A, BUNDLE_A),
            question("q-2", "repo-a", COMMIT_A, BUNDLE_A),
            question("q-3", "repo-a", COMMIT_A, BUNDLE_A, "code-answerable")])))])

    def merged(self):
        links = [self.original["questions"][0]["links"][0], self.original["questions"][1]["links"][0]]
        merged = question("q-12", "repo-a", COMMIT_A, BUNDLE_A)
        merged["links"] = links
        return batch([merged, copy.deepcopy(self.original["questions"][2])])

    def run_consolidation(self, proposal):
        return consolidate_questionnaire(self.original, text(proposal))

    def test_accepts_lossless_merge_with_new_hash(self):
        draft = self.run_consolidation(self.merged())
        self.assertEqual(2, len(draft["questions"]))
        self.assertEqual([], validate_questionnaire_draft(draft))
        self.assertNotEqual(self.original["id"], draft["id"])
        self.assertEqual(draft, self.run_consolidation(self.merged()))

    def test_rejects_dropped_link(self):
        proposal = self.merged(); proposal["questions"][0]["links"].pop()
        with self.assertRaisesRegex(ValueError, "link dropped: claim-q-2"):
            self.run_consolidation(proposal)

    def test_rejects_added_link_and_cross_type_merge(self):
        proposal = self.merged(); proposal["questions"][0]["links"].append(self.original["questions"][2]["links"][0])
        with self.assertRaisesRegex(ValueError, "link added"):
            self.run_consolidation(proposal)

    def test_rejects_scope_respondent_changes_answers_and_growth(self):
        proposal = self.merged(); proposal["respondent"] = {"id": "other", "role": "Other"}
        with self.assertRaisesRegex(ValueError, "respondent differs"):
            self.run_consolidation(proposal)
        proposal = self.merged(); proposal["scope"][0]["commit"] = COMMIT_B
        with self.assertRaises(ValueError):
            self.run_consolidation(proposal)
        proposal = self.merged(); proposal["questions"][0].update(
            answerStatus="unknown", answer={"text": "x", "answerer": "m", "recordedAt": "2026-10-13T10:00:00Z"})
        with self.assertRaisesRegex(ValueError, "must not contain answers"):
            self.run_consolidation(proposal)
        proposal = self.merged(); extra = copy.deepcopy(proposal["questions"][0]); extra["id"] = "q-extra"
        proposal["questions"].append(extra)
        second = copy.deepcopy(extra); second["id"] = "q-extra-2"; proposal["questions"].append(second)
        with self.assertRaisesRegex(ValueError, "increased"):
            self.run_consolidation(proposal)

    def test_rejects_truncated_response_and_answered_original(self):
        with self.assertRaises(ValueError):
            consolidate_questionnaire(self.original, '{"schemaVersion": 1,')
        answered = copy.deepcopy(self.original)
        answered["questions"][0].update(answerStatus="unknown", answer={
            "text": "x", "answerer": "m", "recordedAt": "2026-10-13T10:00:00Z"})
        answered["id"] = "0" * 64
        with self.assertRaises(ValueError):
            consolidate_questionnaire(answered, text(self.merged()))


class AnswerCaptureTest(unittest.TestCase):
    def setUp(self):
        self.draft = assemble_questionnaire([("b", text(batch([
            question("q-1", "repo-a", COMMIT_A, BUNDLE_A),
            question("q-2", "repo-a", COMMIT_A, BUNDLE_A),
            question("q-3", "repo-a", COMMIT_A, BUNDLE_A, "code-answerable")])))])
        self.sheet = answers_template(self.draft)
        self.sheet["answerer"] = "Technical lead"
        self.when = "2026-10-21T12:00:00Z"

    def test_template_lists_only_unanswered_human_only_questions(self):
        self.assertEqual(["q-1", "q-2"], [e["questionId"] for e in self.sheet["answers"]])
        self.assertEqual(self.draft["id"], self.sheet["draftId"])
        self.assertEqual([{"repository": "repo-a", "targets": ["claim claim-q-1"]}], self.sheet["answers"][0]["appliesTo"])
        self.assertIn("- `repo-a` \u2014 claim claim-q-1", answers_template_markdown(self.sheet))

    def test_records_exact_text_for_answered_unknown_and_deferred_and_skips_unanswered(self):
        self.sheet["answers"][0].update(answerStatus="unknown", text="  I do not know.\nAsk ops. ")
        recorded = record_answers(self.draft, self.sheet, self.when)
        first = next(q for q in recorded["questions"] if q["id"] == "q-1")
        self.assertEqual("  I do not know.\nAsk ops. ", first["answer"]["text"])
        self.assertEqual({"text": first["answer"]["text"], "answerer": "Technical lead", "recordedAt": self.when}, first["answer"])
        self.assertEqual("unanswered", next(q for q in recorded["questions"] if q["id"] == "q-2")["answerStatus"])
        self.assertEqual([], validate_questionnaire_draft(recorded))
        self.sheet["answers"][1].update(answerStatus="deferred", text="Revisit next sprint")
        self.assertEqual("deferred", next(q for q in record_answers(self.draft, self.sheet, self.when)["questions"] if q["id"] == "q-2")["answerStatus"])
        self.assertEqual("unanswered", next(q for q in self.draft["questions"] if q["id"] == "q-1")["answerStatus"])

    def test_rejects_bad_sheets(self):
        def attempt(mutate, message):
            sheet = json.loads(json.dumps(self.sheet)); mutate(sheet)
            with self.assertRaisesRegex(ValueError, message):
                record_answers(self.draft, sheet, self.when)
        attempt(lambda s: s["answers"][0].update(answerStatus="answered", text=""), "non-empty text")
        attempt(lambda s: s["answers"][0].update(answerStatus="maybe", text="x"), "invalid answerStatus")
        attempt(lambda s: s["answers"][0].update(answerStatus="answered", text="x", wording="changed"), "wording was changed")
        attempt(lambda s: s["answers"][0].update(questionId="nope", answerStatus="answered", text="x"), "unknown question id")
        attempt(lambda s: s["answers"].append(dict(s["answers"][0])), "duplicate answer")
        attempt(lambda s: s["answers"].append({"questionId": "q-3", "answerStatus": "answered", "text": "x"}), "does not belong on a human-only sheet")
        attempt(lambda s: s.update(answerer=" "), "answerer")
        attempt(lambda s: s.update(draftId="0" * 64), "different draft")
        attempt(lambda s: s["answers"][0].update(extra=1), "invalid fields")

    def test_code_answerable_review_sheet_and_dispositions(self):
        review = answers_template(self.draft, "code-answerable")
        self.assertEqual(["q-3"], [e["questionId"] for e in review["answers"]])
        self.assertIn("accepted, rejected, deferred", answers_template_markdown(review))
        review["answerer"] = "Reviewer"
        review["answers"][0].update(answerStatus="rejected", text="Code supports the claim as written.")
        recorded = record_answers(self.draft, review, self.when)
        item = next(q for q in recorded["questions"] if q["id"] == "q-3")
        self.assertEqual(("rejected", "Reviewer"), (item["answerStatus"], item["answer"]["answerer"]))
        self.assertEqual([], validate_questionnaire_draft(recorded))
        self.assertIn("rejected", render_questionnaire(recorded))
        for status in ("answered", "unknown"):
            review["answers"][0]["answerStatus"] = status
            with self.assertRaisesRegex(ValueError, "invalid answerStatus"):
                record_answers(self.draft, review, self.when)
        review["answers"][0].update(answerStatus="accepted", text=" ")
        with self.assertRaisesRegex(ValueError, "non-empty text"):
            record_answers(self.draft, review, self.when)
        mixed = answers_template(self.draft)
        mixed["answerer"] = "t"; mixed["answers"][0].update(answerStatus="accepted", text="x")
        with self.assertRaisesRegex(ValueError, "invalid answerStatus"):
            record_answers(self.draft, mixed, self.when)
        with self.assertRaisesRegex(ValueError, "review type"):
            answers_template(self.draft, "other")

    def test_cannot_overwrite_a_recorded_answer(self):
        self.sheet["answers"][0].update(answerStatus="answered", text="x")
        recorded = record_answers(self.draft, self.sheet, self.when)
        again = answers_template(recorded)
        self.assertEqual(["q-2"], [e["questionId"] for e in again["answers"]])
        again["answerer"] = "t"; again["answers"].append({"questionId": "q-1", "answerStatus": "answered", "text": "y"})
        with self.assertRaisesRegex(ValueError, "already answered"):
            record_answers(recorded, again, self.when)

    def test_markdown_round_trip_matches_json_path(self):
        markdown = answers_template_markdown(self.sheet)
        self.assertEqual(self.sheet["answers"][0]["questionId"], "q-1")
        filled = markdown.replace("Answerer: Technical lead", "Answerer: Technical lead").replace(
            "Status: unanswered\nAnswer:\n\n", "Status: unknown\nAnswer:\n\nI do not know.\n  Ask | ops.\n\n", 1)
        parsed = parse_answers_markdown(filled)
        self.assertEqual("I do not know.\n  Ask | ops.", parsed["answers"][0]["text"])
        self.assertEqual("unknown", parsed["answers"][0]["answerStatus"])
        self.assertEqual("unanswered", parsed["answers"][1]["answerStatus"])
        recorded = record_answers(self.draft, parsed, self.when)
        self.assertEqual("I do not know.\n  Ask | ops.", recorded["questions"][0]["answer"]["text"])
        self.assertEqual([], validate_questionnaire_draft(recorded))

    def test_markdown_accepts_answer_text_on_the_answer_line(self):
        markdown = answers_template_markdown(self.sheet).replace(
            "Status: unanswered\nAnswer:\n", "Status: answered\nAnswer: First line.\nSecond line.\n", 1)
        parsed = parse_answers_markdown(markdown)
        self.assertEqual("First line.\nSecond line.", parsed["answers"][0]["text"])

    def test_markdown_rejects_ambiguous_sheets(self):
        markdown = answers_template_markdown(self.sheet)
        with self.assertRaisesRegex(ValueError, "Draft"):
            parse_answers_markdown(markdown.replace("Draft:", "Dratf:"))
        with self.assertRaisesRegex(ValueError, "answer text but status is unanswered"):
            parse_answers_markdown(markdown.replace("Answer:\n\n", "Answer:\n\nSome text\n", 1))
        with self.assertRaisesRegex(ValueError, "must have"):
            parse_answers_markdown(markdown.replace("Status: unanswered", "State: x", 1))

    def test_cli_round_trip_and_failure_writes_nothing(self):
        landscape = TOOLS / "landscape"
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "d.json").write_text(json.dumps(self.draft), encoding="utf-8")
            run = lambda *a: subprocess.run([str(landscape), "questionnaire", *a], stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(0, run("answers-template", str(base / "d.json"), "--output", str(base / "a.json")).returncode)
            refused = run("answers-template", str(base / "d.json"), "--output", str(base / "a.json"))
            self.assertEqual(1, refused.returncode); self.assertIn("refusing to overwrite", refused.stderr)
            sheet = json.loads((base / "a.json").read_text(encoding="utf-8"))
            failed = run("record", str(base / "d.json"), str(base / "a.json"), "--output", str(base / "o.json"))
            self.assertEqual(1, failed.returncode); self.assertFalse((base / "o.json").exists())
            sheet["answerer"] = "Lead"; sheet["answers"][0].update(answerStatus="answered", text="Yes")
            (base / "a.json").write_text(json.dumps(sheet), encoding="utf-8")
            ok = run("record", str(base / "d.json"), str(base / "a.json"), "--output", str(base / "o.json"),
                     "--recorded-at", self.when)
            self.assertEqual(0, ok.returncode, ok.stderr)
            self.assertEqual([], validate_questionnaire_draft(json.loads((base / "o.json").read_text(encoding="utf-8"))))


class AssembleCliTest(unittest.TestCase):
    def test_cli_writes_only_on_success(self):
        landscape = TOOLS / "landscape"
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            good = base / "good.txt"
            good.write_text(text(batch([question("q-a", "repo-a", COMMIT_A, BUNDLE_A)])), encoding="utf-8")
            bad = base / "bad.txt"
            bad.write_text('{"schemaVersion": 1,', encoding="utf-8")
            output = base / "draft.json"
            failed = subprocess.run([str(landscape), "questionnaire", "assemble", str(good), str(bad),
                                     "--output", str(output)], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(1, failed.returncode)
            self.assertTrue(failed.stderr.startswith("error: "))
            self.assertFalse(output.exists())
            missing = subprocess.run([str(landscape), "questionnaire", "assemble", str(base / "absent.txt"),
                                      "--output", str(output)], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(1, missing.returncode)
            ok = subprocess.run([str(landscape), "questionnaire", "assemble", str(good),
                                 "--output", str(output)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
            self.assertEqual(0, ok.returncode, ok.stderr)
            self.assertEqual([], validate_questionnaire_draft(json.loads(output.read_text(encoding="utf-8"))))


class ExtractedDirTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([str(TOOLS / "landscape"), "questionnaire", "assemble"] + [str(a) for a in args],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.one = self.base / "batch-1.txt"
        self.two = self.base / "batch-2.txt"
        self.one.write_text(text(batch([question("q-a", "repo-a", COMMIT_A, BUNDLE_A)])), encoding="utf-8")
        self.two.write_text(text(batch([question("q-b", "repo-b", COMMIT_B, BUNDLE_B)],
                                        scope=(("repo-b", COMMIT_B, BUNDLE_B),))), encoding="utf-8")
        self.out = self.base / "draft.json"
        self.dir = self.base / "extracted"

    def test_writes_extracted_files(self):
        result = self.run_cli(self.one, self.two, "--output", self.out, "--extracted-dir", self.dir)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["batch-1.extracted.json", "batch-2.extracted.json"], sorted(p.name for p in self.dir.iterdir()))
        content = (self.dir / "batch-1.extracted.json").read_text(encoding="utf-8")
        expected = extract_questionnaire_response(self.one.read_text(encoding="utf-8"))
        self.assertEqual(json.dumps(expected, indent=2, sort_keys=True) + "\n", content)
        self.assertEqual([], validate_questionnaire_draft(json.loads(content)))
        self.assertIn("id", json.loads(content))
        self.assertTrue(self.one.read_text(encoding="utf-8").startswith("Here is the batch."))
        before = (self.dir / "batch-2.extracted.json").read_bytes()
        again = self.run_cli(self.one, self.two, "--output", self.out, "--extracted-dir", self.dir)
        self.assertEqual(0, again.returncode, again.stderr)
        self.assertEqual(before, (self.dir / "batch-2.extracted.json").read_bytes())

    def test_nothing_written_on_bad_batch(self):
        bad = self.base / "bad.txt"
        bad.write_text("{", encoding="utf-8")
        result = self.run_cli(self.one, bad, "--output", self.out, "--extracted-dir", self.dir)
        self.assertEqual(1, result.returncode)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.dir.exists())

    def test_duplicate_stem_rejected(self):
        other = self.base / "sub"
        other.mkdir()
        clash = other / "batch-1.md"
        clash.write_text(self.two.read_text(encoding="utf-8"), encoding="utf-8")
        result = self.run_cli(self.one, clash, "--output", self.out, "--extracted-dir", self.dir)
        self.assertEqual(1, result.returncode)
        self.assertIn("batch-1.extracted.json", result.stderr)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.dir.exists())

    def test_refuses_to_overwrite_differing_file(self):
        self.dir.mkdir()
        existing = self.dir / "batch-2.extracted.json"
        existing.write_text("different\n", encoding="utf-8")
        result = self.run_cli(self.one, self.two, "--output", self.out, "--extracted-dir", self.dir)
        self.assertEqual(1, result.returncode)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertEqual("different\n", existing.read_text(encoding="utf-8"))
        self.assertFalse(self.out.exists())
        self.assertFalse((self.dir / "batch-1.extracted.json").exists())

    def test_flag_absent_unchanged(self):
        result = self.run_cli(self.one, self.two, "--output", self.out)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(["draft.json"], sorted(p.name for p in self.base.iterdir() if p.suffix == ".json"))
        self.assertFalse(self.dir.exists())
        pairs = [(str(self.one), self.one.read_text(encoding="utf-8")), (str(self.two), self.two.read_text(encoding="utf-8"))]
        draft, batches = assemble_questionnaire(pairs, return_batches=True)
        self.assertEqual(draft, assemble_questionnaire(pairs))
        self.assertEqual(2, len(plan_extracted_files(batches, self.dir)))
        self.assertFalse(self.dir.exists())


if __name__ == "__main__":
    unittest.main()
