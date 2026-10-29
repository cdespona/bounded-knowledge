"""Tests for gap-derived binding questions, structured answers, and their use in producer/consumer links."""

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

from landscape_core.binding_questions import generate_binding_questions  # noqa: E402
from landscape_core.catalog_proposals import empty_catalog, validate_catalog_proposal  # noqa: E402
from landscape_core.config_links import FEED_TYPE, propose_shared_resource_relationships  # noqa: E402
from landscape_core.contracts import validate_questionnaire_draft  # noqa: E402
from landscape_core.discovery import discover  # noqa: E402
from landscape_core.evidence import select_evidence  # noqa: E402
from landscape_core.questionnaires import (  # noqa: E402
    answers_template, answers_template_markdown, parse_answers_markdown, record_answers, render_questionnaire,
)
from test_env_vars import ANALYZED_AT, JAVA, application, make_repository  # noqa: E402

RESPONDENT = {"id": "technical-lead", "role": "Technical lead"}
SENDER = """package com.example;
import software.amazon.awssdk.services.sqs.SqsClient;
class Sender {
    @ConfigProperty(name = "queue.url") String url;
    void send() { SendMessageRequest.builder().queueUrl(url).build(); }
}
"""
RECEIVER = """package com.example;
import software.amazon.awssdk.services.sqs.SqsClient;
class Receiver {
    Receiver(@Value("${QUEUE_URL}") String url) { this.url = url; }
    void receive() { ReceiveMessageRequest.builder().queueUrl(url).build(); }
    void wrapped(String bucket) { PutObjectRequest.builder().bucket(bucket).build(); }
    void caller() { wrapped("fixed-bucket"); }
}
"""


class BindingQuestionTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        resources = "src/main/resources/"
        self.repositories = {
            "receiver": make_repository(self.base, "receiver", {JAVA + "Receiver.java": RECEIVER}),
            "sender": make_repository(self.base, "sender", {
                JAVA + "Sender.java": SENDER,
                resources + "application.properties": "%pro.queue.url=https://sqs.eu-west-1.amazonaws.com/111111111111/pro-events\n"}),
        }
        self.inventories = [discover(path, name) for name, path in sorted(self.repositories.items())]
        self.bundles = [select_evidence(inventory, self.repositories[inventory["repository"]], purpose="semantic")
                        for inventory in self.inventories]
        self.draft, self.skipped = generate_binding_questions(self.inventories, self.bundles, RESPONDENT)

    def test_only_unresolved_targets_get_a_valid_deterministic_question(self):
        self.assertEqual(validate_questionnaire_draft(self.draft), [])
        self.assertEqual([item["id"] for item in self.draft["questions"]], ["binding-receiver-sqs-consumer-queue-url"])
        question = self.draft["questions"][0]
        self.assertEqual((question["reviewType"], question["expects"], question["answerStatus"]),
                         ("human-only", "target-values", "unanswered"))
        self.assertEqual([link["type"] for link in question["links"]], ["observation"])
        self.assertIn("`QUEUE_URL`", question["whyCodeCannotSettle"])
        again, _ = generate_binding_questions(self.inventories, self.bundles, RESPONDENT)
        self.assertEqual(self.draft, again)

    def test_parameters_whose_callers_resolve_ask_nothing_and_resolved_targets_are_skipped(self):
        text = json.dumps(self.draft)
        self.assertNotIn("queue.url", text)
        self.assertNotIn("`bucket`", text)

    def test_no_unresolved_targets_means_no_draft(self):
        draft, _ = generate_binding_questions(self.inventories[1:], self.bundles[1:], RESPONDENT)
        self.assertIsNone(draft)


class StructuredAnswerTest(unittest.TestCase):
    def setUp(self):
        BindingQuestionTest.setUp(self)
        self.sheet = answers_template(self.draft)

    def answer(self, entry):
        return record_answers(self.draft, dict(self.sheet, answerer="Technical lead", answers=[entry]), "2026-10-29T10:00:00Z")

    def entry(self, **changes):
        return dict(self.sheet["answers"][0], **dict({"answerStatus": "answered"}, **changes))

    def test_the_sheet_asks_for_values_and_masks_account_ids(self):
        self.assertEqual(self.sheet["answers"][0]["values"], [])
        answered = self.answer(self.entry(values=[
            {"profile": "PRO", "value": "https://sqs.eu-west-1.amazonaws.com/222222222222/pro-events"},
            {"profile": "default", "value": "local-events"}]))
        answer = answered["questions"][0]["answer"]
        self.assertEqual(answer["values"], [
            {"profile": None, "value": "local-events"},
            {"profile": "pro", "value": "https://sqs.eu-west-1.amazonaws.com/<aws-account>/pro-events"}])
        self.assertTrue(answer["text"].startswith("Target values: default = local-events"))
        self.assertEqual(validate_questionnaire_draft(answered), [])

    def test_answers_without_values_or_with_credentials_are_refused(self):
        for changes in ({}, {"values": [{"profile": "pro", "value": "https://user:secret@host/q"}]}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.answer(self.entry(**changes))
        unknown = self.answer(self.entry(answerStatus="unknown", text="I do not know."))
        self.assertNotIn("values", unknown["questions"][0]["answer"])

    def test_values_are_not_accepted_where_none_are_expected(self):
        text = dict(self.entry(values=[{"profile": None, "value": "x"}]), answerStatus="unknown", text="no")
        with self.assertRaises(ValueError):
            self.answer(text)

    def test_markdown_form_round_trips_values(self):
        form = answers_template_markdown(self.sheet)
        self.assertIn("Values:", form)
        filled = form.replace("Status: unanswered", "Status: answered").replace(
            "Values:\n", "Values:\n\n- pro: https://sqs.eu-west-1.amazonaws.com/222222222222/pro-events\n- dev: dev-events\n", 1)
        parsed = parse_answers_markdown(filled)
        self.assertEqual(parsed["answers"][0]["values"][0], {"profile": "pro", "value": "https://sqs.eu-west-1.amazonaws.com/222222222222/pro-events"})
        recorded = record_answers(self.draft, dict(parsed, answerer="Technical lead"), "2026-10-29T10:00:00Z")
        self.assertEqual([item["profile"] for item in recorded["questions"][0]["answer"]["values"]], ["dev", "pro"])
        self.assertIn("Linked observation", render_questionnaire(recorded))
        self.assertIn("dev = dev-events", render_questionnaire(recorded))


class AnswerLinkTest(unittest.TestCase):
    def setUp(self):
        BindingQuestionTest.setUp(self)
        answered = record_answers(self.draft, dict(answers_template(self.draft), answerer="Technical lead", answers=[
            dict(answers_template(self.draft)["answers"][0], answerStatus="answered", values=[
                {"profile": "pro", "value": "https://sqs.eu-west-1.amazonaws.com/222222222222/pro-events"}])]),
            "2026-10-29T10:00:00Z")
        self.answered = answered
        proposal = empty_catalog()
        proposal["applications"] = [application(bundle) for bundle in self.bundles]
        self.proposal = proposal

    def feeds(self, answers):
        result = propose_shared_resource_relationships(self.proposal, self.inventories, self.bundles, ANALYZED_AT, None, answers)
        return result, [item for item in result["relationships"] if item["type"] == FEED_TYPE]

    def test_an_answer_closes_the_gap_with_curated_evidence_and_a_caveat(self):
        result, feeds = self.feeds(self.answered)
        self.assertEqual([item["id"] for item in feeds], ["rel-app-sender-feeds-app-receiver"])
        feed = feeds[0]
        self.assertIn("sqs-queue `pro-events` (profiles: pro)", feed["statement"])
        self.assertEqual(feed["assessment"]["status"], "inferred")
        self.assertTrue(any(item["kind"] == "curated" for item in feed["assessment"]["evidence"]))
        self.assertTrue(any("interview answer" in text for text in feed["assessment"]["missingEvidence"]))
        self.assertEqual(validate_catalog_proposal(result, self.answered, self.bundles), [])

    def test_without_the_answer_nothing_is_linked_and_unknown_answers_support_nothing(self):
        self.assertEqual(self.feeds(None)[1], [])
        unknown = record_answers(self.draft, dict(answers_template(self.draft), answerer="Technical lead", answers=[
            dict(answers_template(self.draft)["answers"][0], answerStatus="unknown", text="I do not know.")]),
            "2026-10-29T10:00:00Z")
        self.assertEqual(self.feeds(unknown)[1], [])

    def test_cli_generates_questions_and_links_with_answers(self):
        files = {"draft-answered.json": self.answered, "proposal.json": self.proposal}
        files.update({"inv{}.json".format(i): value for i, value in enumerate(self.inventories)})
        files.update({"ev{}.json".format(i): value for i, value in enumerate(self.bundles)})
        for name, value in files.items():
            (self.base / name).write_text(json.dumps(value), encoding="utf-8")
        tool = [str(ROOT / "tools" / "landscape")]
        generated = subprocess.run(tool + ["questionnaire", "bindings", "--inventory", str(self.base / "inv0.json"), str(self.base / "inv1.json"),
                                           "--evidence", str(self.base / "ev0.json"), str(self.base / "ev1.json"),
                                           "--respondent-id", "technical-lead", "--respondent-role", "Technical lead",
                                           "--output", str(self.base / "generated.json")], capture_output=True, text=True)
        self.assertEqual(generated.returncode, 0, generated.stderr)
        self.assertEqual(json.loads((self.base / "generated.json").read_text(encoding="utf-8")), self.draft)
        linked = subprocess.run(tool + ["catalog", "link-config", str(self.base / "proposal.json"), "--inventory", str(self.base / "inv0.json"),
                                        str(self.base / "inv1.json"), "--evidence", str(self.base / "ev0.json"), str(self.base / "ev1.json"),
                                        "--analyzed-at", ANALYZED_AT, "--answers", str(self.base / "draft-answered.json"),
                                        "--output", str(self.base / "linked.json")], capture_output=True, text=True)
        self.assertEqual(linked.returncode, 0, linked.stderr)
        self.assertEqual(json.loads((self.base / "linked.json").read_text(encoding="utf-8")), self.feeds(self.answered)[0])


if __name__ == "__main__":
    unittest.main()
