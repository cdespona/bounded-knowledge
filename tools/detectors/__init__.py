"""Built-in deterministic detectors."""

from .generic_files import GenericFileDetector
from .git import GitDetector
from .gradle import GradleManifestDetector
from .maven import MavenManifestDetector
from .api_contract import ApiContractDetector
from .container_images import ContainerImageDetector
from .kafka_literals import KafkaLiteralDetector
from .kubernetes_images import KubernetesImageDetector
from .terraform import TerraformDetector


def built_in_detectors(source_selection=None):
    if source_selection is not None:
        detector = KubernetesImageDetector if source_selection["kind"] == "kubernetes" else TerraformDetector
        return [GitDetector(), detector(source_selection)]
    return [
        GitDetector(),
        GenericFileDetector(),
        MavenManifestDetector(),
        GradleManifestDetector(),
        ApiContractDetector(),
        KafkaLiteralDetector(),
        ContainerImageDetector(),
    ]
