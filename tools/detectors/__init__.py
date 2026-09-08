"""Built-in deterministic detectors."""

from .generic_files import GenericFileDetector
from .git import GitDetector


def built_in_detectors():
    return [GitDetector(), GenericFileDetector()]

