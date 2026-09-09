"""Built-in deterministic detectors."""

from .generic_files import GenericFileDetector
from .git import GitDetector
from .gradle import GradleManifestDetector
from .maven import MavenManifestDetector


def built_in_detectors():
    return [
        GitDetector(),
        GenericFileDetector(),
        MavenManifestDetector(),
        GradleManifestDetector(),
    ]
