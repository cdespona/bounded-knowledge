"""Built-in deterministic detectors."""

from .generic_files import GenericFileDetector
from .git import GitDetector
from .gradle import GradleManifestDetector
from .maven import MavenManifestDetector
from .api_contract import ApiContractDetector
from .config_properties import ConfigPropertiesDetector
from .container_images import ContainerImageDetector
from .io_points import IoPointsDetector
from .kafka_literals import KafkaLiteralDetector
from .kubernetes_env import KubernetesEnvDetector
from .kubernetes_images import KubernetesImageDetector
from .terraform import TerraformDetector


def built_in_detectors(source_selection=None):
    if source_selection is not None:
        if source_selection["kind"] == "kubernetes":
            return [GitDetector(), KubernetesImageDetector(source_selection), KubernetesEnvDetector(source_selection)]
        return [GitDetector(), TerraformDetector(source_selection)]
    return [
        GitDetector(),
        GenericFileDetector(),
        MavenManifestDetector(),
        GradleManifestDetector(),
        ApiContractDetector(),
        KafkaLiteralDetector(),
        ConfigPropertiesDetector(),
        IoPointsDetector(),
        ContainerImageDetector(),
    ]
