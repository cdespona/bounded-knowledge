"""Built-in deterministic detectors."""

from .generic_files import GenericFileDetector
from .git import GitDetector
from .gradle import GradleManifestDetector
from .maven import MavenManifestDetector
from .api_contract import ApiContractDetector
from .kafka_literals import KafkaLiteralDetector


def built_in_detectors():
    return [
        GitDetector(),
        GenericFileDetector(),
        MavenManifestDetector(),
        GradleManifestDetector(),
        ApiContractDetector(),
        KafkaLiteralDetector(),
    ]
