"""Allowlisted reading of container environment variables from Kubernetes manifests.

Manifests hold many secrets as plain literals, so a variable is only recorded when its name or value
shape points to an infrastructure target; sensitive names and credential-shaped values are withheld.
"""

import re

from .config_files import SENSITIVE_KEY, mask_account_ids, WITHHELD_MASK

ENV_RESOURCES = {"jdbc", "sqs", "s3", "kafka", "kafka-topic", "schema-registry", "amqp"}
_CREDENTIAL_VALUE = re.compile(r"://[^/@\s:]+:[^/@\s]+@|(?:password|pwd|secret|token)\s*=", re.IGNORECASE)
_VALUE_LINE = re.compile(r"^(\s*(?:-\s+)?value:\s*)(.*)$")
_PROFILES = {"local", "dev", "development", "test", "int", "pre", "preprod", "staging", "pro", "prod", "production"}


def classify_env(name, value):
    """Name the infrastructure target an environment variable looks like, or None."""
    lowered_name = name.lower()
    lowered_value = (value or "").lower()
    if lowered_value.startswith("jdbc:") or "jdbc" in lowered_name:
        return "jdbc"
    if "sqs" in lowered_name or ("queue" in lowered_name and "url" in lowered_name) or (
            "amazonaws.com" in lowered_value and "sqs" in lowered_value):
        return "sqs"
    if "bucket" in lowered_name:
        return "s3"
    if "schema" in lowered_name and "registry" in lowered_name:
        return "schema-registry"
    if "topic" in lowered_name:
        return "kafka-topic"
    if "kafka" in lowered_name or "bootstrap" in lowered_name:
        return "kafka"
    if "rabbit" in lowered_name or "amqp" in lowered_name or lowered_value.startswith("amqp"):
        return "amqp"
    return None


def describe_env(name, value):
    """Return (disposition, stored value) for a literal value; secrets are never stored."""
    if not value:
        return "empty", ""
    if SENSITIVE_KEY.search(name) or _CREDENTIAL_VALUE.search(value):
        return "withheld", None
    return "literal", mask_account_ids(value)


def environment_names(key):
    """Environment variable names that Spring and Quarkus relaxed binding map to a property key."""
    return {key, re.sub(r"[^A-Za-z0-9]", "_", key).upper()}


def manifest_profile(path):
    """Return the environment named by a directory of the manifest path, if one is known."""
    for part in path.split("/")[:-1]:
        if part.lower() in _PROFILES:
            return part.lower()
    return None


def mask_env_lines(lines, ranges):
    """Replace withheld value lines (1-based inclusive ranges) of a manifest, keeping line numbers."""
    for first, last in ranges:
        for number in range(first, last + 1):
            index = number - 1
            if index >= len(lines):
                break
            match = _VALUE_LINE.match(lines[index]) if number == first else None
            if match:
                lines[index] = match.group(1) + WITHHELD_MASK
            else:
                lines[index] = lines[index][:len(lines[index]) - len(lines[index].lstrip())] + WITHHELD_MASK
    return lines
