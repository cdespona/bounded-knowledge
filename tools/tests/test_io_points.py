"""Tests for messaging and persistence I/O points and the producer/consumer links built from them."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from landscape_core.catalog_proposals import empty_catalog, validate_catalog_proposal  # noqa: E402
from landscape_core.config_links import FEED_TYPE, SHARE_TYPE, propose_shared_resource_relationships  # noqa: E402
from landscape_core.discovery import discover  # noqa: E402
from landscape_core.evidence import select_evidence  # noqa: E402
from landscape_core.validation import validate_inventory  # noqa: E402

ANALYZED_AT = "2026-10-29T10:00:00Z"
DRAFT = json.loads((ROOT / "examples" / "contracts" / "questionnaire-draft.valid.json").read_text(encoding="utf-8"))
JAVA = "src/main/java/com/example/"


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def make_repository(base, name, files):
    repository = base / name
    for relative, text in files.items():
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(repository, "init", "-q")
    git(repository, "config", "user.name", "Test")
    git(repository, "config", "user.email", "test@example.invalid")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "Fixture")
    return repository


STREAMS = """package com.example;
import org.apache.kafka.streams.StreamsBuilder;
import org.springframework.beans.factory.annotation.Value;
public class Wiring {
    Topics build(@Value("${topic.in}") String in, @Value("${topic.out:fallback}") String out) {
        return new Topics(in, out);
    }
}
record Topics(String inputTopic, String outputTopic) {}
class Util {
    StreamsBuilder streamsBuilder;
    Object table(String topic) { return streamsBuilder.table(topic); }
}
class Stream extends Util {
    Topics topics;
    void run() {
        streamsBuilder.stream(topics.inputTopic());
        table(topics.inputTopic());
        streamsBuilder.stream("literal-topic").to(topics.outputTopic());
        list.stream().map(x -> x);
        // streamsBuilder.stream("commented-out");
    }
}
"""

MESSAGING = """package com.example;
import org.springframework.kafka.core.KafkaTemplate;
public class Messaging {
    @Incoming("orders-in")
    void consume(Object record) {}
    @Outgoing("orders-out")
    Object produce() { return null; }
    @KafkaListener(topics = {"a-topic", "${listener.topic}"})
    void listen() {}
    @KafkaListener(id = "x")
    void odd() {}
    void send() {
        kafkaTemplate.send("sent-topic", "payload");
        new ProducerRecord<String, String>(TOPIC, "k", "v");
    }
    static final String TOPIC = "constant-topic";
}
"""

SQS = """package com.example;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.SendMessageRequest;
import software.amazon.awssdk.services.sqs.model.ReceiveMessageRequest;
public class Queues {
    private final String url;
    Queues(@ConfigProperty(name = "queue.url") String url) { this.url = url; }
    void send(SqsClient client) {
        client.sendMessage(SendMessageRequest.builder().messageBody("m").queueUrl(url).build());
    }
    void receive(SqsClient client) {
        var request = ReceiveMessageRequest.builder().queueUrl("https://sqs.eu-west-1.amazonaws.com/123456789012/fixed").build();
    }
}
"""

S3 = """package com.example;
import software.amazon.awssdk.services.s3.S3Client;
public class Buckets {
    void run(S3Client client, String bucket) {
        client.putObject(PutObjectRequest.builder().bucket("out-bucket").key("k").build(), body);
        client.getObject(r -> r.bucket(bucket).key("k"));
        CopyObjectRequest.builder().sourceBucket("from-bucket").destinationBucket("to-bucket").build();
    }
}
"""

PERSISTENCE = """package com.example;
@Repository
public class Dao extends Base {
    static final String ADD = \"\"\"
        INSERT INTO ORDERS (ID) VALUES (:id)
        \"\"\";
    static final String LOAD = "SELECT o.id FROM ORDERS o " +
        "JOIN customers c ON c.id = o.customer_id WHERE EXTRACT(YEAR FROM o.at) = 1";
    static final String TEMPLATE = "MERGE INTO $TABLE$ T USING DUAL ON (1=1)";
    static final String PING = "select * from dual";
    void run() { super.merge("AUDIT_LOG", 1); }
}
@Entity
@Table(name = "invoices", schema = "billing")
class Invoice {}
@Entity
class Plain {}
interface Orders extends JpaRepository<Order, Long> {
    @Query("select o from Order o")
    List<Order> all();
    @Query(value = "select * from archive where id = 1", nativeQuery = true)
    List<Order> archived();
}
"""

CONSUMER_PROPERTIES = "mp.messaging.incoming.orders-in.connector=smallrye-kafka\nmp.messaging.incoming.orders-in.topic=orders-merged\n"


class IoPointDetectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.repository = make_repository(Path(cls.directory.name), "fixture", {
            JAVA + "Stream.java": STREAMS, JAVA + "Messaging.java": MESSAGING,
            JAVA + "Queues.java": SQS, JAVA + "Buckets.java": S3, JAVA + "Dao.java": PERSISTENCE,
            "src/test/java/com/example/Ignored.java": '@Incoming("test-only") class T {}',
        })
        cls.inventory = discover(cls.repository, "fixture")
        cls.points = [item for item in cls.inventory["observations"] if item["kind"] == "io-point"]
        cls.gaps = [item for item in cls.inventory["observations"] if item["kind"] == "io-gap"]

    def find(self, technology, role, construct=None, form=None, reference=None):
        return [item for item in self.points if item["value"]["technology"] == technology
                and item["value"]["role"] == role
                and (construct is None or item["value"]["construct"] == construct)
                and (form is None or item["value"]["binding"]["form"] == form)
                and (reference is None or item["value"]["binding"]["reference"] == reference)]

    def test_inventory_with_io_observations_validates_and_is_stable(self):
        self.assertEqual(validate_inventory(self.inventory), [])
        self.assertEqual(self.inventory["observations"], discover(self.repository, "fixture")["observations"])

    def test_streams_topics_resolve_through_records_and_wrapper_methods(self):
        consumed = self.find("kafka", "consumer", "StreamsBuilder.stream", "config-key", "topic.in")
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0]["value"]["binding"]["via"], "record Topics.inputTopic")
        self.assertEqual(len(self.find("kafka", "consumer", "StreamsBuilder.table via table", "config-key", "topic.in")), 1)
        self.assertEqual(len(self.find("kafka", "producer", "KStream.to", "config-key", "topic.out")), 1)
        self.assertEqual(len(self.find("kafka", "consumer", "StreamsBuilder.stream", "literal", "literal-topic")), 1)
        self.assertEqual(len(self.find("kafka", "consumer", "StreamsBuilder.table", "unresolved", "topic")), 1)

    def test_comments_tests_and_plain_java_streams_are_ignored(self):
        text = json.dumps(self.points)
        for ignored in ("commented-out", "test-only", "x -> x"):
            self.assertNotIn(ignored, text)

    def test_channels_listeners_and_producers(self):
        self.assertEqual(len(self.find("messaging", "consumer", "@Incoming", "channel", "orders-in")), 1)
        self.assertEqual(len(self.find("messaging", "producer", "@Outgoing", "channel", "orders-out")), 1)
        self.assertEqual(len(self.find("kafka", "consumer", "@KafkaListener", "literal", "a-topic")), 1)
        self.assertEqual(len(self.find("kafka", "consumer", "@KafkaListener", "config-key", "listener.topic")), 1)
        self.assertEqual(len(self.find("kafka", "producer", "KafkaTemplate.send", "literal", "sent-topic")), 1)
        self.assertEqual(len(self.find("kafka", "producer", "ProducerRecord", "literal", "constant-topic")), 1)
        self.assertEqual([item["value"]["code"] for item in self.gaps], ["unsupported-listener-form"])

    def test_sqs_queue_urls_resolve_parameters_and_mask_accounts(self):
        sent = self.find("sqs", "producer", "SendMessageRequest.queueUrl", "config-key", "queue.url")
        self.assertEqual(len(sent), 1)
        fixed = self.find("sqs", "consumer", "ReceiveMessageRequest.queueUrl", "literal")
        self.assertEqual(fixed[0]["value"]["binding"]["reference"], "https://sqs.eu-west-1.amazonaws.com/<aws-account>/fixed")
        self.assertNotIn("123456789012", json.dumps(self.points))

    def test_s3_buckets_by_request_lambda_and_copy(self):
        self.assertEqual(len(self.find("s3", "producer", "PutObjectRequest.bucket", "literal", "out-bucket")), 1)
        self.assertEqual(len(self.find("s3", "consumer", "S3Client.getObject.bucket", "unresolved", "bucket")), 1)
        self.assertEqual(len(self.find("s3", "consumer", "CopyObjectRequest.sourceBucket", "literal", "from-bucket")), 1)
        self.assertEqual(len(self.find("s3", "producer", "CopyObjectRequest.destinationBucket", "literal", "to-bucket")), 1)

    def test_persistence_declarations_tables_and_statements(self):
        self.assertTrue(self.find("persistence", "declaration", "@Repository", "type", "Dao"))
        self.assertTrue(self.find("persistence", "declaration", "extends JpaRepository", "type", "Orders"))
        self.assertTrue(self.find("persistence", "mapped", "@Table", "literal", "billing.invoices"))
        self.assertTrue(self.find("persistence", "mapped", "@Entity", "type", "Plain"))
        self.assertTrue(self.find("persistence", "mapped", "repository-literal-argument", "literal", "AUDIT_LOG"))
        self.assertTrue(self.find("persistence", "writer", "sql", "literal", "ORDERS"))
        self.assertTrue(self.find("persistence", "reader", "sql", "literal", "ORDERS"))
        self.assertTrue(self.find("persistence", "reader", "sql", "literal", "customers"))
        self.assertTrue(self.find("persistence", "writer", "sql", "placeholder", "TABLE"))
        self.assertTrue(self.find("persistence", "reader", "@Query", "type", "Order"))
        self.assertTrue(self.find("persistence", "reader", "@Query", "literal", "archive"))
        self.assertFalse(self.find("persistence", "reader", reference="dual"))
        self.assertFalse(self.find("persistence", "reader", reference="o.at"))


COVERAGE = """package com.example;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.streams.StreamsBuilder;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.sqs.SqsClient;
@ConfigurationProperties(prefix = "kafka.topics")
class TopicProperties {
    private String styleColor;
    String getStyleColor() { return styleColor; }
}
class Reader {
    final TopicProperties properties;
    final String bucket;
    Reader(TopicProperties properties, String bucket) { this.properties = properties; this.bucket = bucket; }
    void put() { PutObjectRequest.builder().bucket(bucket).build(); }
    void read(StreamsBuilder streamsBuilder) { streamsBuilder.table(properties.getStyleColor()); }
}
class Wiring {
    static final String OUT = System.getenv("OUT_BUCKET");
    static final String TOPIC = envOr("DERIVED_QUEUE", "d");
    void wire() { new Reader(null, OUT); }
    void send() { new ProducerRecord<String, String>(TOPIC, "k", "v"); }
    static String envOr(String key, String fallback) { return System.getenv(key) == null ? fallback : System.getenv(key); }
}
class Beans {
    public String sitemapBucket() { return System.getenv("SITEMAP_BUCKET"); }
}
class Sitemap {
    String sitemapBucket;
    void get() { GetObjectRequest.builder().bucket(sitemapBucket).build(); }
    void direct() { GetObjectRequest.builder().bucket(System.getenv("DIRECT_BUCKET")).build(); }
}
class Legacy {
    void send() { new SendMessageRequest().withQueueUrl(System.getenv("V1_QUEUE")).withMessageBody("x"); }
}
"""


class CoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.repository = make_repository(Path(cls.directory.name), "coverage", {JAVA + "Coverage.java": COVERAGE})
        cls.points = {(item["value"]["construct"], item["value"]["binding"]["reference"]): item["value"]["binding"]
                      for item in discover(cls.repository, "coverage")["observations"] if item["kind"] == "io-point"}

    def binding(self, construct, reference):
        return self.points[(construct, reference)]

    def test_configuration_properties_classes_resolve_accessors(self):
        binding = self.binding("StreamsBuilder.table", "kafka.topics.styleColor")
        self.assertEqual(binding["form"], "config-key")
        self.assertEqual(binding["via"], "configuration class TopicProperties.styleColor")

    def test_constructor_fields_follow_callers_that_read_environment_variables(self):
        self.assertEqual(self.binding("PutObjectRequest.bucket via Reader", "OUT_BUCKET")["form"], "config-key")

    def test_helper_methods_that_read_an_environment_variable_bind_their_callers(self):
        self.assertEqual(self.binding("ProducerRecord", "DERIVED_QUEUE")["form"], "config-key")

    def test_provider_methods_and_direct_reads_resolve(self):
        self.assertIn("provider method sitemapBucket()", self.binding("GetObjectRequest.bucket", "SITEMAP_BUCKET")["via"])
        self.assertEqual(self.binding("GetObjectRequest.bucket", "DIRECT_BUCKET")["via"], "configuration read")

    def test_v1_builder_chains_resolve(self):
        self.assertEqual(self.binding("SendMessageRequest.queueUrl", "V1_QUEUE")["form"], "config-key")

    def test_unresolved_call_sites_add_no_points(self):
        self.assertFalse([key for key in self.points if "via Reader" in key[0] and key[1] != "OUT_BUCKET"])

    def test_property_keys_also_match_their_kebab_case_spelling(self):
        from landscape_core.io_links import key_variants
        self.assertEqual(key_variants("kafka.topics.styleColor"), ["kafka.topics.styleColor", "kafka.topics.style-color"])
        self.assertEqual(key_variants("a.b"), ["a.b"])


def application(bundle):
    entry = bundle["selectedEvidence"][0]
    evidence = {"kind": "repository", "repositoryId": bundle["repository"], "commit": bundle["commit"],
                "path": entry["path"], "lines": entry["lines"], "observationId": entry["observationIds"][0]}
    return {"id": "app-" + bundle["repository"], "name": bundle["repository"], "purpose": "Synthetic application.",
            "assessment": {"status": "inferred", "confidence": "medium", "analyzedAt": ANALYZED_AT,
                           "evidence": [evidence], "counterevidence": []}}


PRODUCER = """package com.example;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.s3.S3Client;
class B {
    @Value("${bucket.name}") String bucket;
    void put() { PutObjectRequest.builder().bucket(bucket).build(); }
}
import org.apache.kafka.streams.StreamsBuilder;
class P {
    void run(@Value("${topic.out}") String out) { builder.stream("raw").to(out); }
}
class Q {
    @Value("${queue.url}") String url;
    void send() { SendMessageRequest.builder().queueUrl(url).build(); }
}
@Repository
class R { static final String S = "INSERT INTO SHARED_TABLE (ID) VALUES (1)"; static final String T = "INSERT INTO LATER_TABLE (ID) VALUES (2)"; }
"""

CONSUMER = """package com.example;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.s3.S3Client;
class B {
    void get() { GetObjectRequest.builder().bucket("audit-bucket").build(); }
}
class C {
    @Incoming("orders-in") void consume(Object value) {}
    void receive() { ReceiveMessageRequest.builder().queueUrl("https://sqs.eu-west-1.amazonaws.com/111111111111/pro-queue").build(); }
}
@Repository
class R { static final String S = "SELECT ID FROM SHARED_TABLE"; static final String L = "UPDATE LATER_TABLE SET A = 1"; }
"""

LOCAL_ONLY = """package com.example;
import org.apache.kafka.streams.StreamsBuilder;
class L { void run() { builder.stream("raw").to("never-shared"); } }
"""


class FeedLinkTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        resources = "src/main/resources/"
        self.repositories = {
            "producer": make_repository(base, "producer", {
                JAVA + "P.java": PRODUCER,
                resources + "application.properties": "queue.url=https://sqs.eu-west-1.amazonaws.com/222222222222/pro-queue\nbucket.name=audit-bucket\n",
                resources + "application-pro.properties": "topic.out=orders-merged\n",
                resources + "application-local.properties": "topic.out=local-only\n"}),
            "consumer": make_repository(base, "consumer", {
                JAVA + "C.java": CONSUMER,
                resources + "application.properties": CONSUMER_PROPERTIES.replace("orders-merged", "local-only") +
                                                      "%pro.mp.messaging.incoming.orders-in.topic=orders-merged\n"}),
            "isolated": make_repository(base, "isolated", {JAVA + "L.java": LOCAL_ONLY}),
        }
        self.inventories = [discover(path, name) for name, path in sorted(self.repositories.items())]
        self.bundles = [select_evidence(inventory, self.repositories[inventory["repository"]], purpose="semantic")
                        for inventory in self.inventories]
        self.proposal = empty_catalog()
        self.proposal["applications"] = [application(bundle) for bundle in self.bundles]

    def link(self, proposal=None):
        return propose_shared_resource_relationships(proposal or self.proposal, self.inventories, self.bundles, ANALYZED_AT)

    def relationships(self, kind):
        return [item for item in self.link()["relationships"] if item["type"] == kind]

    def test_producer_feeds_consumer_with_direction_and_valid_evidence(self):
        feeds = self.relationships(FEED_TYPE)
        self.assertEqual([item["id"] for item in feeds], ["rel-app-producer-feeds-app-consumer"])
        feed = feeds[0]
        self.assertEqual((feed["source"]["entityId"], feed["target"]["entityId"]), ("app-producer", "app-consumer"))
        for expected in ("kafka-topic `orders-merged` (profiles: pro)", "sqs-queue `pro-queue`", "table `SHARED_TABLE`", "writes to s3-bucket `audit-bucket`"):
            self.assertIn(expected, feed["statement"])
        self.assertNotIn("local-only", feed["statement"])
        self.assertEqual({item["repositoryId"] for item in feed["assessment"]["evidence"]}, {"producer", "consumer"})
        self.assertEqual(validate_catalog_proposal(self.link(), DRAFT, self.bundles), [])

    def test_unmatched_and_isolated_repositories_are_not_fed(self):
        self.assertNotIn("isolated", json.dumps(self.link()["relationships"]))
        self.assertNotIn("never-shared", json.dumps(self.link()["relationships"]))

    def test_tables_touched_in_the_same_direction_are_only_shared(self):
        shares = self.relationships(SHARE_TYPE)
        self.assertEqual(len(shares), 1)
        self.assertIn("table `LATER_TABLE`", shares[0]["statement"])
        self.assertNotIn("SHARED_TABLE", shares[0]["statement"])

    def test_linking_is_idempotent_and_rebuilds_stale_feeds(self):
        once = self.link()
        self.assertEqual(once, self.link(once))
        stale = json.loads(json.dumps(once))
        for item in stale["relationships"]:
            item["statement"] = "stale"
        self.assertEqual(once, self.link(stale))


if __name__ == "__main__":
    unittest.main()
