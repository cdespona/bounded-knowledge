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
| `docs/contracts/` | Reviewed producer-consumer and CLI contracts |
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
./tools/landscape sources validate sources.json
./tools/landscape sources resolve application-id --registry sources.json
./tools/landscape evidence select work/application-id.json --source /path/to/repository --output work/application-id-evidence.json
./tools/landscape candidate validate work/application-id-candidate.json --evidence work/application-id-evidence.json
./tools/landscape catalog validate work/landscape-catalog.json
./tools/landscape topology validate work/source-topology.json --sources sources.json --catalog work/landscape-catalog.json
```

`preflight` requires an independent, clean Git repository. It reports Copilot
customization files because adding a source directory to Copilot CLI is also a trust
decision.

`discover` emits a stable JSON inventory. It records file metadata and recognized
manifests, but it does not infer application purpose, ownership, dependencies, or domain
meaning.

`status` compares the current Git commit with a prior inventory. An unchanged commit can
be skipped without invoking Copilot.

The local source registry is `sources.json`. `sources validate` checks its persisted
contract without accessing configured repositories. `sources resolve` verifies one
enabled source as an independent, clean Git root and reports whether its repository-owned
Copilot customizations match the approved digest.

The full source-resolution and Copilot-approval rules are documented in
`docs/contracts/slice-2.md`.

Discovery also emits bounded literal Maven and Gradle observations. Unsupported,
dynamic, inherited, profile-dependent, or effective-model constructs remain visible as
manifest gaps. `evidence select` revalidates the inventory and clean source commit,
reapplies safety exclusions, and writes a bounded, contract-validated evidence bundle.
The exact supported forms and limits are documented in `docs/contracts/slice-3.md`.

Discovery also inventories operations from conventionally named OpenAPI and AsyncAPI
JSON documents. It keeps the version-specific operation vocabularies distinct and emits
visible gaps for unsupported versions, malformed structures, duplicate JSON keys, and
matching YAML documents. YAML contents are not parsed in detector version 1. The exact
boundary is documented in `docs/contracts/slice-6-api-inventory.md`.

Model-produced candidates are validated against the exact selected evidence bundle before
they can reach a later human gate. The read-only repository cartographer and its direct
Copilot CLI trial procedure are documented in `docs/contracts/slice-4.md`.

The canonical knowledge-model and source-topology contracts distinguish logical
applications and deployables from physical repositories and monorepo subpaths. `catalog
validate` checks evidence-backed entities and typed relationships. `topology validate`
checks path-free source selections and many-to-many bindings against both the local source
registry and catalog without resolving or modifying source repositories. The contracts are
documented in `docs/contracts/slice-5a-knowledge-model.md` and
`docs/contracts/slice-5a-source-topology.md`.

## Validation

Run all deterministic tests:

```text
python3 -m unittest discover -s tools/tests -v
```

The tests create temporary Git repositories from the Java and Kotlin fixtures. They do
not download dependencies or compile the synthetic applications.

The suite also validates the contract fixtures in `examples/contracts/` and exercises
the complete supported CLI surface through subprocesses. The Slice 1 contracts and exit
codes are documented in `docs/contracts/slice-1.md`.

## Safety principles

- Source repositories are read-only inputs.
- Every analyzed repository must be at a clean, explicit Git commit.
- Sensitive, generated, vendored, and build-output paths are excluded.
- Unknown artifacts remain visible as unsupported observations.
- Deterministic observations are not semantic claims.
- Unsupported or invalid model output must fail closed.
- No catalog mutation occurs without deterministic validation and human approval.
