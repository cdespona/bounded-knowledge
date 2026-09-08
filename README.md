# Mercurio Knowledge

Mercurio Knowledge is a version-controlled, evidence-backed catalog of an application
landscape. It treats source repositories as independent systems and records only claims
that can be traced to an analyzed Git commit and source evidence.

The project combines three execution modes:

1. Deterministic discovery produces reproducible observations without an AI model.
2. GitHub Copilot interprets selected evidence and proposes semantic claims.
3. Deterministic validation and human approval control promotion into the catalog.

Microsoft Conductor will orchestrate these stages after the deterministic command
contracts are stable. No Conductor workflow is part of the first milestone.

## Repository layout

| Path | Purpose |
| --- | --- |
| `schemas/` | Persisted JSON contracts for sources, observations, claims, and profiles |
| `tools/` | Deterministic discovery and validation commands |
| `examples/` | Synthetic Java and Kotlin source fixtures used by tests |
| `catalog/` | Approved application, platform, interface and terminology knowledge |
| `findings/` | Evidence-backed improvement opportunities |
| `views/` | Human-readable projections of canonical catalog data |
| `work/` | Temporary and generated analysis artifacts; ignored by Git |
| `workflows/` | Conductor workflows, added after script contracts are stable |

## Deterministic commands

The tooling requires Python 3.9 or later and Git. It has no third-party runtime
dependencies.

```text
./tools/landscape preflight /path/to/repository
./tools/landscape discover /path/to/repository --repository application-id
./tools/landscape discover /path/to/repository --repository application-id --output work/application-id.json
./tools/landscape validate work/application-id.json --source /path/to/repository
./tools/landscape status /path/to/repository --inventory work/application-id.json
```

`preflight` requires an independent, clean Git repository. It reports Copilot
customization files because adding a source directory to Copilot CLI is also a trust
decision.

`discover` emits a stable JSON inventory. It records file metadata and recognized
manifests, but it does not infer application purpose, ownership, dependencies, or domain
meaning.

`status` compares the current Git commit with a prior inventory. An unchanged commit can
be skipped without invoking Copilot.

## Validation

Run all deterministic tests:

```text
python3 -m unittest discover -s tools/tests -v
```

The tests create temporary Git repositories from the Java and Kotlin fixtures. They do
not download dependencies or compile the synthetic applications.

## Safety principles

- Source repositories are read-only inputs.
- Every analyzed repository must be at a clean, explicit Git commit.
- Sensitive, generated, vendored, and build-output paths are excluded.
- Unknown artifacts remain visible as unsupported observations.
- Deterministic observations are not semantic claims.
- Unsupported or invalid model output must fail closed.
- No catalog mutation occurs without deterministic validation and human approval.

