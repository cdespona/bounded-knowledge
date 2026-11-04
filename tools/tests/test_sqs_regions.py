"""Tests for comparing SQS queues by region as well as by name."""

from pathlib import Path
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from landscape_core.config_links import _feed_missing  # noqa: E402
from landscape_core.io_links import Resolved, feed_matches, matching_profiles  # noqa: E402


def url(region, name):
    return "https://sqs.{}.amazonaws.com/<aws-account>/{}".format(region, name)


def resolved(repository, role, values, texts=None):
    point = {"repository": repository, "value": {"role": role}}
    return Resolved(point, "sqs-queue", {profile: text.rsplit("/", 1)[-1] for profile, text in values.items()},
                    [], "queue name only", texts=texts or values)


class SqsRegionTests(unittest.TestCase):
    def test_same_name_in_different_regions_is_not_a_match(self):
        producer = resolved("a", "producer", {"pro": url("eu-west-1", "events"), "dev": url("eu-west-1", "events")})
        consumer = resolved("b", "consumer", {"pro": url("eu-west-1", "events"), "dev": url("us-east-1", "events")})
        self.assertEqual(matching_profiles(producer, consumer), ["pro"])

    def test_a_missing_region_does_not_block_a_name_match(self):
        producer = resolved("a", "producer", {"pro": url("eu-west-1", "events")})
        consumer = resolved("b", "consumer", {"pro": "events"})
        self.assertEqual(matching_profiles(producer, consumer), ["pro"])

    def test_caveat_names_the_region_only_when_every_match_compared_it(self):
        compared = {"resource": "sqs-queue", "regionCompared": True,
                    "producer": resolved("a", "producer", {"pro": url("eu-west-1", "q")}),
                    "consumer": resolved("b", "consumer", {"pro": url("eu-west-1", "q")})}
        self.assertIn("SQS queues are compared by queue name and region; account is not compared.",
                      _feed_missing([compared]))
        partial = dict(compared, regionCompared=False)
        self.assertIn("SQS queues are compared by queue name only; region and account are not compared.",
                      _feed_missing([partial]))


if __name__ == "__main__":
    unittest.main()
