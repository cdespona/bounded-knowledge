"""Focused tests for the bounded Kafka literal inventory."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]
PROJECT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from landscape_core.discovery import discover  # noqa: E402
from landscape_core.evidence import select_evidence  # noqa: E402
from landscape_core.observations import observation  # noqa: E402
from landscape_core.safety import MAX_FILE_BYTES  # noqa: E402
from landscape_core.validation import validate_inventory  # noqa: E402


def git(path, *args):
    subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class KafkaLiteralDetectorTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)

    def make_repository(self, fixture="synthetic-java-service"):
        target = self.base / fixture
        shutil.copytree(str(PROJECT / "examples" / fixture), str(target))
        git(target, "init", "-q")
        git(target, "config", "user.name", "Mercurio Test")
        git(target, "config", "user.email", "mercurio@example.invalid")
        git(target, "add", ".")
        git(target, "commit", "-q", "-m", "Synthetic fixture")
        return target

    def commit(self, repository, message="Add Kafka test case"):
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", message)

    def kafka_observations(self, inventory):
        return [
            item
            for item in inventory["observations"]
            if item["detector"]["name"] == "kafka-literals"
        ]

    def write_source(self, repository, name, content, language="java"):
        extension = "kt" if language == "kotlin" else "java"
        root = (
            repository / "src/main/kotlin/com/example/mercurio/customer"
            if language == "kotlin"
            else repository / "src/main/java/com/example/mercurio/customer"
        )
        root.mkdir(parents=True, exist_ok=True)
        path = root / "{}.{}".format(name, extension)
        path.write_text(content, encoding="utf-8")
        return path

    def test_versioned_java_fixture_covers_supported_literal_forms(self):
        repository = self.make_repository()
        inventory = discover(repository, "synthetic-java-service")
        self.assertEqual([], validate_inventory(inventory, source=repository))
        observations = self.kafka_observations(inventory)

        values = {
            (item["kind"], item["value"].get("role"), item["value"].get("form")):
            item
            for item in observations
            if item["kind"] != "kafka-gap"
        }
        expected = {
            ("kafka-topic-reference", "declaration", "new-topic"):
                ("customer.updated", "19"),
            ("kafka-topic-reference", "producer-send", "kafka-template-send"):
                ("customer.updated", "23"),
            ("kafka-topic-reference", "producer-default", "default-topic-property"):
                ("customer.default", "1"),
            ("kafka-schema-reference", "schema-lookup", "schema-registry-subject-lookup"):
                ("customer-updated-value", "27"),
        }
        self.assertEqual(set(expected), set(values))
        for identity, (literal, lines) in expected.items():
            item = values[identity]
            field = "subject" if item["kind"] == "kafka-schema-reference" else "topic"
            self.assertEqual(literal, item["value"][field])
            self.assertEqual(lines, item["source"]["lines"])

        serialized = json.dumps(observations, sort_keys=True)
        self.assertNotIn("customer.unrelated", serialized)

    def test_versioned_kotlin_fixture_covers_supported_literal_forms(self):
        repository = self.make_repository("synthetic-kotlin-service")
        inventory = discover(repository, "synthetic-kotlin-service")
        self.assertEqual([], validate_inventory(inventory, source=repository))
        observations = self.kafka_observations(inventory)
        references = {
            (
                item["kind"],
                item["value"].get("role"),
                item["value"].get("form"),
                item["source"]["lines"],
            )
            for item in observations
            if item["kind"] != "kafka-gap"
        }
        self.assertEqual(
            {
                ("kafka-topic-reference", "declaration", "new-topic", "12"),
                ("kafka-topic-reference", "producer-send", "kafka-template-send", "15"),
                ("kafka-topic-reference", "consumer-registration", "kafka-listener", "18"),
                ("kafka-topic-reference", "producer-default", "default-topic-property", "1"),
                (
                    "kafka-schema-reference",
                    "schema-lookup",
                    "schema-registry-subject-lookup",
                    "22",
                ),
            },
            references,
        )
        self.assertNotIn(
            "customer.unrelated", json.dumps(observations, sort_keys=True)
        )
        self.assertNotIn(
            "#/channels/customerUpdated", json.dumps(observations, sort_keys=True)
        )

    def test_dynamic_interpolated_property_spread_and_multi_topic_gaps(self):
        repository = self.make_repository("synthetic-kotlin-service")
        self.write_source(
            repository,
            "KafkaGapCases",
            """package com.example.mercurio.customer

import io.confluent.kafka.schemaregistry.client.SchemaRegistryClient
import org.springframework.kafka.annotation.KafkaListener
import org.springframework.kafka.core.KafkaTemplate

class KafkaGapCases(
    private val kafkaTemplate: KafkaTemplate<String, String>,
    private val schemaRegistryClient: SchemaRegistryClient,
) {
    fun dynamic(payload: String) = kafkaTemplate.send(topicFor(payload), payload)
    fun property(payload: String) = kafkaTemplate.send(TOPIC, payload)
    fun interpolated(suffix: String, payload: String) = kafkaTemplate.send("customer.$suffix", payload)
    fun dynamicSchema(subject: String) = schemaRegistryClient.getLatestSchemaMetadata(subject)
    fun interpolatedSchema(suffix: String) = schemaRegistryClient.getLatestSchemaMetadata("customer-$suffix")

    @KafkaListener(topics = [*TOPICS])
    fun spread(payload: String) = payload

    @KafkaListener(topics = ["customer.one", "customer.two"])
    fun multiple(payload: String) = payload

    companion object {
        const val TOPIC = "customer.constant"
        val TOPICS = arrayOf("customer.one")
    }
}
""",
            language="kotlin",
        )
        self.commit(repository)

        observations = self.kafka_observations(
            discover(repository, "synthetic-kotlin-service")
        )
        codes = {
            item["value"]["code"]
            for item in observations
            if item["kind"] == "kafka-gap"
        }
        self.assertTrue(
            {
                "dynamic-topic",
                "property-derived-topic",
                "interpolated-topic",
                "dynamic-schema-subject",
                "interpolated-schema-subject",
                "spread-topic",
                "unsupported-multi-topic",
            }
            <= codes,
            codes,
        )
        gap_values = [
            item["value"] for item in observations if item["kind"] == "kafka-gap"
        ]
        self.assertTrue(all(value["detail"] for value in gap_values))
        self.assertNotIn("customer.constant", json.dumps(gap_values, sort_keys=True))

    def test_malformed_ambiguous_and_unsupported_source_forms_are_gaps(self):
        repository = self.make_repository()
        cases = {
            "AmbiguousProducer": """package example;
import org.springframework.kafka.core.KafkaTemplate;
class AmbiguousProducer {
  KafkaTemplate<String, String> template;
  KafkaTemplate<String, String> template;
  void send(String value) { template.send("ambiguous.topic", value); }
}
""",
            "UnsupportedForms": """package example;
import io.confluent.kafka.schemaregistry.client.SchemaRegistryClient;
import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
class UnsupportedForms {
  KafkaTemplate<String, String> template;
  SchemaRegistryClient registry;
  void producer(String value) { template.sendDefault("outside.allowlist", value); }
  @KafkaListener(topicPattern = "customer.*") void consumer(String value) {}
  Object topic() { return new NewTopic(); }
  Object schema() { return registry.getSchemaById(1); }
}
""",
            "MalformedKafka": """package example;
import org.springframework.kafka.core.KafkaTemplate;
class MalformedKafka {
  KafkaTemplate<String, String> template;
  void send(String value) { template.send("broken.topic",
}
""",
            "UnsupportedLiteral": '''package example;
import org.springframework.kafka.core.KafkaTemplate;
class UnsupportedLiteral {
  KafkaTemplate<String, String> template;
  void send(String value) { template.send("""raw.topic""", value); }
}
''',
            "UnicodeEscapeKafka": """package example;
import org.springframework.kafka.core.KafkaTemplate;
class UnicodeEscapeKafka {
  KafkaTemplate<String, String> template;
  void send(String value) { template.send("\\u0063ustomer.topic", value); }
}
""",
            "WildcardKafka": """package example;
import org.springframework.kafka.core.*;
class WildcardKafka {
  KafkaTemplate<String, String> template;
  void send(String value) { template.send("wildcard.topic", value); }
}
""",
        }
        for name, content in cases.items():
            self.write_source(repository, name, content)
        self.commit(repository)

        codes = {
            item["value"]["code"]
            for item in self.kafka_observations(
                discover(repository, "synthetic-java-service")
            )
            if item["kind"] == "kafka-gap"
        }
        self.assertTrue(
            {
                "ambiguous-kafka-binding",
                "unsupported-producer-form",
                "unsupported-consumer-form",
                "unsupported-topic-declaration",
                "unsupported-schema-reference",
                "malformed-kafka-construct",
                "unsupported-source-literal",
                "unsupported-unicode-escape",
                "unsupported-kafka-import",
            }
            <= codes,
            codes,
        )

    def test_properties_gaps_are_visible_without_guessed_values(self):
        repository = self.make_repository()
        resources = repository / "src/main/resources"
        (resources / "application-duplicate.properties").write_text(
            "spring.kafka.template.default-topic=customer.one\n"
            "spring.kafka.template.default-topic=customer.two\n",
            encoding="utf-8",
        )
        (resources / "application-syntax.properties").write_text(
            "spring.kafka.template.default-topic : customer.syntax\n",
            encoding="utf-8",
        )
        (resources / "application-placeholder.properties").write_text(
            "spring.kafka.template.default-topic=${CUSTOMER_TOPIC}\n",
            encoding="utf-8",
        )
        (resources / "application-mixed-duplicate.properties").write_text(
            "spring.kafka.template.default-topic=customer.one\n"
            "spring.kafka.template.default-topic : customer.two\n",
            encoding="utf-8",
        )
        (resources / "application-leading.properties").write_text(
            " spring.kafka.template.default-topic=customer.leading\n",
            encoding="utf-8",
        )
        self.commit(repository)

        gaps = [
            item
            for item in self.kafka_observations(
                discover(repository, "synthetic-java-service")
            )
            if item["kind"] == "kafka-gap"
        ]
        codes = {item["value"]["code"] for item in gaps}
        self.assertTrue(
            {
                "duplicate-config-key",
                "unsupported-config-syntax",
                "unsupported-property-value",
            }
            <= codes
        )
        self.assertNotIn("customer.one", json.dumps(gaps, sort_keys=True))
        self.assertNotIn("CUSTOMER_TOPIC", json.dumps(gaps, sort_keys=True))

    def test_false_positive_boundary_remains_silent(self):
        repository = self.make_repository()
        self.write_source(
            repository,
            "KafkaLookalikes",
            """package example;
class KafkaLookalikes {
  static final class KafkaTemplate { void send(String topic, String value) {} }
  static final class NewTopic { NewTopic(String topic, int partitions) {} }
  KafkaTemplate template;
  Object value() {
    template.send("not.kafka.evidence", "payload");
    return new NewTopic("also.not.evidence", 1);
  }
  String comment = "KafkaListener SchemaRegistryClient customer.string";
}
""",
        )
        resources = repository / "src/main/resources"
        (resources / "custom.properties").write_text(
            "custom.kafka.topic=not.kafka.evidence\n", encoding="utf-8"
        )
        self.commit(repository)

        serialized = json.dumps(
            self.kafka_observations(discover(repository, "synthetic-java-service")),
            sort_keys=True,
        )
        for value in ("not.kafka.evidence", "also.not.evidence", "customer.string"):
            self.assertNotIn(value, serialized)

    def test_method_returns_comments_unicode_and_property_prefixes_stay_silent(self):
        repository = self.make_repository()
        self.write_source(
            repository,
            "MethodReturnLookalike",
            """package example;
import org.springframework.kafka.core.KafkaTemplate;
class MethodReturnLookalike {
  static final class Sender { void send(String topic, String value) {} }
  Sender fake;
  KafkaTemplate<String, String> fake() { return null; }
  void publish() { fake.send("not.kafka.evidence", "payload"); }
}
""",
        )
        self.write_source(repository, "CommentOnlyKafka", "/* KafkaTemplate")
        self.write_source(
            repository,
            "UnicodeCommentKafka",
            "// KafkaTemplate \\u0063omment\nclass UnicodeCommentKafka {}\n",
        )
        resources = repository / "src/main/resources"
        (resources / "application-prefix.properties").write_text(
            "spring.kafka.template.default-topic.extra=not.kafka.evidence\n",
            encoding="utf-8",
        )
        self.commit(repository)

        observations = self.kafka_observations(
            discover(repository, "synthetic-java-service")
        )
        serialized = json.dumps(observations, sort_keys=True)
        self.assertNotIn("not.kafka.evidence", serialized)
        self.assertNotIn("unsupported-unicode-escape", serialized)
        paths = {item["source"]["path"] for item in observations}
        self.assertFalse(any("CommentOnlyKafka" in path for path in paths))

    def test_same_line_import_and_duplicate_references_are_valid_and_stable(self):
        repository = self.make_repository()
        path = self.write_source(
            repository,
            "SameLineKafka",
            'import org.springframework.kafka.core.KafkaTemplate; class SameLineKafka { '
            'KafkaTemplate<String, String> template; void publish() { '
            'template.send("same.topic", "a"); template.send("same.topic", "b"); } }\n',
        )
        self.commit(repository)

        inventory = discover(repository, "synthetic-java-service")
        self.assertEqual([], validate_inventory(inventory, source=repository))
        same_line = [
            item for item in self.kafka_observations(inventory)
            if item["source"]["path"] == str(path.relative_to(repository))
        ]
        self.assertEqual(1, len(same_line))
        self.assertEqual("same.topic", same_line[0]["value"]["topic"])
        self.assertEqual("1", same_line[0]["source"]["lines"])

    def test_control_character_literal_is_a_gap(self):
        repository = self.make_repository()
        self.write_source(
            repository,
            "ControlCharacterKafka",
            "package example;\n"
            "import org.springframework.kafka.core.KafkaTemplate;\n"
            "class ControlCharacterKafka {\n"
            "  KafkaTemplate<String, String> template;\n"
            '  void publish() { template.send("customer.\ttopic", "payload"); }\n'
            "}\n",
        )
        self.commit(repository)

        gaps = [
            item for item in self.kafka_observations(
                discover(repository, "synthetic-java-service")
            )
            if item["kind"] == "kafka-gap"
            and item["source"]["path"].endswith("ControlCharacterKafka.java")
        ]
        self.assertEqual(
            ["unsupported-literal-value"],
            [item["value"]["code"] for item in gaps],
        )

    def test_valid_prefix_survives_unrelated_lexer_error(self):
        repository = self.make_repository()
        path = self.write_source(
            repository,
            "ValidBeforeBrokenText",
            """package example;
import org.springframework.kafka.core.KafkaTemplate;
class ValidBeforeBrokenText {
  KafkaTemplate<String, String> template;
  void publish() { template.send("valid.before.error", "payload"); }
  String unrelated = "unterminated;
}
""",
        )
        self.commit(repository)

        observations = [
            item for item in self.kafka_observations(
                discover(repository, "synthetic-java-service")
            )
            if item["source"]["path"] == str(path.relative_to(repository))
        ]
        references = [
            item for item in observations
            if item["kind"] == "kafka-topic-reference"
        ]
        self.assertEqual(["valid.before.error"], [
            item["value"]["topic"] for item in references
        ])

    def test_plain_dollar_is_literal_in_java_topic_and_subject(self):
        repository = self.make_repository()
        path = self.write_source(
            repository,
            "JavaDollarKafka",
            """package example;
import io.confluent.kafka.schemaregistry.client.SchemaRegistryClient;
import org.springframework.kafka.core.KafkaTemplate;
class JavaDollarKafka {
  KafkaTemplate<String, String> template;
  SchemaRegistryClient registry;
  void publish() { template.send("cost$center", "payload"); }
  void schema() { registry.getLatestSchemaMetadata("subject$literal"); }
}
""",
        )
        self.commit(repository)

        observations = [
            item for item in self.kafka_observations(
                discover(repository, "synthetic-java-service")
            )
            if item["source"]["path"] == str(path.relative_to(repository))
        ]
        self.assertEqual(
            {"cost$center"},
            {
                item["value"]["topic"] for item in observations
                if item["kind"] == "kafka-topic-reference"
            },
        )
        self.assertEqual(
            {"subject$literal"},
            {
                item["value"]["subject"] for item in observations
                if item["kind"] == "kafka-schema-reference"
            },
        )

    def test_candidate_safety_exclusions_and_skips(self):
        repository = self.make_repository()
        source_root = repository / "src/main/java/com/example/mercurio/customer"
        binary = source_root / "BinaryKafka.java"
        binary.write_bytes(b"KafkaTemplate\xff")
        oversized = source_root / "OversizedKafka.java"
        oversized.write_bytes(b"KafkaTemplate " + b"x" * MAX_FILE_BYTES)
        symlink = source_root / "LinkedKafka.java"
        symlink.symlink_to("CustomerKafkaReferences.java")
        generated = repository / "build/generated/GeneratedKafka.java"
        generated.parent.mkdir(parents=True)
        generated.write_text(
            "import org.apache.kafka.clients.admin.NewTopic;\n"
            "class GeneratedKafka { Object x = new NewTopic(\"generated.topic\", 1); }\n",
            encoding="utf-8",
        )
        self.commit(repository)

        inventory = discover(repository, "synthetic-java-service")
        kafka_paths = {
            item["source"]["path"] for item in self.kafka_observations(inventory)
        }
        self.assertFalse(
            {
                str(binary.relative_to(repository)),
                str(oversized.relative_to(repository)),
                str(symlink.relative_to(repository)),
                str(generated.relative_to(repository)),
            }
            & kafka_paths
        )
        self.assertIn(
            {
                "path": str(binary.relative_to(repository)),
                "reason": "unsupported-or-unreadable-content",
            },
            inventory["excluded"],
        )
        self.assertIn(
            {
                "path": str(oversized.relative_to(repository)),
                "reason": "file-too-large",
            },
            inventory["excluded"],
        )

    def test_discovery_is_stable_registered_and_operationally_valid(self):
        repository = self.make_repository()
        first = discover(repository, "synthetic-java-service")
        second = discover(repository, "synthetic-java-service")
        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )
        self.assertIn(
            {"name": "kafka-literals", "version": 1}, first["detectors"]
        )
        self.assertEqual([], validate_inventory(first, source=repository))

    def test_topic_literal_and_source_range_participate_in_identity(self):
        common = {
            "kind": "kafka-topic-reference",
            "repository": "synthetic-java-service",
            "commit": "a" * 40,
            "detector": "kafka-literals",
            "detector_version": 1,
            "source_path": "src/main/java/example/Producer.java",
        }
        value = {
            "role": "producer-send",
            "form": "kafka-template-send",
            "topic": "customer.updated",
        }
        first = observation(source_lines="10", value=value, **common)
        moved = observation(source_lines="11", value=value, **common)
        changed = observation(
            source_lines="10",
            value=dict(value, topic="customer.created"),
            **common
        )
        self.assertEqual(3, len({first["id"], moved["id"], changed["id"]}))

    def test_references_and_gaps_route_to_bounded_evidence(self):
        repository = self.make_repository("synthetic-kotlin-service")
        self.write_source(
            repository,
            "DynamicKafkaTopic",
            """package com.example.mercurio.customer
import org.springframework.kafka.core.KafkaTemplate
class DynamicKafkaTopic(private val template: KafkaTemplate<String, String>) {
    fun publish(topic: String, payload: String) = template.send(topic, payload)
}
""",
            language="kotlin",
        )
        self.commit(repository)
        inventory = discover(repository, "synthetic-kotlin-service")
        observations = self.kafka_observations(inventory)
        reference_ids = {
            item["id"] for item in observations if item["kind"] != "kafka-gap"
        }
        gap_ids = {
            item["id"] for item in observations if item["kind"] == "kafka-gap"
        }
        bundle = select_evidence(inventory, repository)
        selected_ids = {
            observation_id
            for item in bundle["selectedEvidence"]
            for observation_id in item["observationIds"]
        }
        bundled_gap_ids = {item["observationId"] for item in bundle["gaps"]}
        self.assertTrue(reference_ids <= selected_ids)
        self.assertTrue(gap_ids <= bundled_gap_ids)
        self.assertTrue(gap_ids.isdisjoint(selected_ids))


if __name__ == "__main__":
    unittest.main()
