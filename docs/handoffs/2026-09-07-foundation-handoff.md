# Bounded Knowledge Initiative Handoff

## Purpose

This document transfers the durable context of the Bounded Knowledge initiative into the
new Codex local project at `/Users/cdespona/personal/bounded-knowledge`.

The initiative supports a technical lead responsible for a collection of important,
independently versioned Java and Kotlin applications. The real application repositories
are private and must ultimately be analyzed locally with GitHub Copilot CLI.

The objective is not to build an artificial brain that claims to have read everything.
The objective is to build a verifiable catalog that knows where every answer came from.

## Operating model

Treat the parent directory as an observatory, not a monorepo:

```text
company-landscape/
├── bounded-knowledge/       # independent Git repository; canonical knowledge
└── repositories/
    ├── application-a/       # independent Git repository
    ├── application-b/
    ├── application-c/
    ├── kubernetes/
    └── terraform/
```

Do not merge source repositories or remove their independent Git boundaries.

The canonical knowledge store is reviewable, version-controlled Markdown and YAML/JSON.
A database, knowledge graph, or embeddings may later be generated as projections, but
must not become the source of truth.

## Confirmed decisions

1. All repository content must be written in English.
2. GitHub Copilot CLI is the required AI execution environment. The user is intentionally
   IDE-independent.
3. Microsoft Conductor will orchestrate deterministic and model-backed workflow stages.
4. Deterministic discovery must do as much mechanical work as practical before a model is
   invoked.
5. Scripts emit observations; agents propose semantic claims.
6. Model-backed analysis remains read-only.
7. Deterministic validation and a human gate control promotion into the canonical catalog.
8. Source repositories are never modified by this system.
9. Results are cached by repository commit SHA and unchanged repositories are skipped.
10. Shared aggregate files have a single writer. Parallel work may write only isolated
    per-repository outputs.
11. No vector database or graph database is required for the pilot.
12. The pilot will eventually cover three closely related applications, one Kubernetes
    repository, and one Terraform repository.
13. The private repositories must not be requested or analyzed until the synthetic
    workflow is reviewed and accepted.

## Evidence policy

Every semantic assertion must use one of these statuses:

| Status | Meaning |
| --- | --- |
| `confirmed` | Direct, traceable evidence supports the assertion. |
| `inferred` | Evidence suggests the assertion, but direct confirmation is incomplete. |
| `unknown` | Evidence is missing, contradictory, or insufficient. |

Claims should include, where practical:

- repository identifier;
- full analyzed commit SHA;
- source path and line range;
- confidence;
- analysis timestamp;
- supporting evidence;
- counterevidence;
- missing evidence or open questions.

Never invent relationships, ownership, terminology, deployment topology, or dependencies.

## Deterministic and agentic boundary

### Deterministic responsibilities

- Git repository root, commit SHA, remote, and clean working-tree checks;
- changed-commit detection;
- file and language inventory;
- Maven, Gradle, and NPM manifests;
- OpenAPI and AsyncAPI documents;
- literal Kafka configuration and topic names;
- Dockerfiles and image references;
- Kubernetes, Helm, and Kustomize resources;
- Terraform providers, modules, resources, and explicit references;
- CI/CD workflow structure;
- database migration object names;
- exact string-based cross-references;
- schema, evidence-path, commit, and scope validation;
- controlled promotion of approved results.

Deterministic tools produce observations and explicit derivations, not unsupported
architectural conclusions.

### Copilot responsibilities

- application purpose;
- domain definitions and synonyms;
- terminology collisions across bounded contexts;
- ambiguous producer and consumer relationships;
- data ownership;
- architectural intent;
- likely blast radius;
- improvement opportunities;
- evidence versus counterevidence synthesis;
- questions requiring human confirmation.

Use efficient models for bounded extraction and ordinary catalog questions. Reserve the
strongest available Copilot model for cross-repository reconciliation, terminology
collisions, architectural synthesis, and evidence auditing.

## Conductor architecture

Conductor is the orchestration layer, not the implementation layer. Parsing and business
logic belong in independently testable scripts, never in workflow YAML or complex Jinja
expressions.

The intended repository-analysis flow is:

```text
preflight script
  -> deterministic discovery
  -> skip if commit is already analyzed
  -> evidence selection
  -> read-only Copilot cartographer
  -> deterministic candidate validation
  -> human approval gate
  -> deterministic promotion
```

Expected future workflow files:

```text
workflows/
├── analyze-repository.yaml
├── analyze-platform.yaml
└── reconcile-pilot.yaml
```

Safe parallelism:

- deterministic read-only discovery;
- per-repository analysis;
- isolated temporary output.

Sequential operations:

- cross-repository reconciliation;
- terminology merging;
- relationship aggregation;
- findings ranking;
- canonical catalog promotion.

Pin Conductor to an explicit release rather than tracking `main`. The original design
session used the `microsoft/conductor` skill pinned to `v0.1.18` as a reference. That
version is historical context, not an installation recommendation. Review the current
release and pin an explicit tag or full commit only after the deterministic contracts and
direct synthetic Copilot trial are stable.

## GitHub Copilot CLI safety

Copilot CLI supports adding source directories, but `--add-dir` also loads agents and
skills from the added repository as trusted configuration. Inspect and approve those
customizations before granting access.

Do not use `--allow-all` or `--allow-all-paths`.

Source analysis should deny write tools. A deterministic wrapper should validate Copilot's
structured output and perform any eventual catalog write itself.

Exclude at minimum:

- `.env` files;
- Terraform state and environment-specific sensitive variable files;
- kubeconfigs;
- private keys, certificates, keystores, and credentials;
- secret values;
- generated code;
- vendor directories;
- dependency caches;
- build output.

Do not execute builds, tests, hooks, wrappers, or application scripts from analyzed source
repositories during discovery.

## Matt Pocock engineering skills assessment

- `domain-modeling` provides useful principles for ubiquitous language, definitions, and
  terminology collisions. Adapt those principles to evidence-backed structured terms
  rather than adopting its `CONTEXT.md` output unchanged.
- `grill-with-docs` is useful later for human validation of ambiguous terminology and
  decisions.
- `codebase-design` and `zoom-out` are useful supporting lenses.
- `improve-codebase-architecture` is useful later within one application or bounded
  subsystem. It is not the initial landscape crawler.
- Prefer the custom `system-landscape` skill over blindly running every upstream skill
  across every repository.
- Inspect and pin any upstream skill before adoption.

## Implemented foundation

The project has been moved safely from `/Users/cdespona/code/mercurio-knowledge` to
`/Users/cdespona/personal/bounded-knowledge`. The old copy remains temporarily available
for rollback.

The new project is an initialized Git repository on branch `main`, with remote `origin`
configured. The deterministic foundation was committed as `1356054` (`Initial Mercurio
Knowledge foundation`).

Implemented files include:

```text
README.md
AGENTS.md
.github/copilot-instructions.md
.gitignore
pyproject.toml
sources.json
schemas/source.schema.json
schemas/observation.schema.json
schemas/claim.schema.json
schemas/repository-profile.schema.json
schemas/manifest-observation.schema.json
schemas/source-resolution.schema.json
schemas/evidence-bundle.schema.json
schemas/candidate-envelope.schema.json
tools/landscape
tools/landscape_core/
tools/landscape_core/contracts.py
tools/detectors/git.py
tools/detectors/generic_files.py
tools/tests/test_landscape.py
tools/tests/test_cli.py
tools/tests/test_contracts.py
examples/synthetic-java-service/
examples/synthetic-kotlin-service/
examples/contracts/
docs/contracts/slice-1.md
work/.gitkeep
workflows/.gitkeep
```

The deterministic tool currently supports:

```text
./tools/landscape preflight /path/to/repository

./tools/landscape discover /path/to/repository \
  --repository application-id \
  --output work/application-id.json

./tools/landscape validate work/application-id.json \
  --source /path/to/repository

./tools/landscape status /path/to/repository \
  --inventory work/application-id.json
```

The initial implementation deliberately uses Python 3.9 standard library only because
the original local environment had Python 3.9, Java, and Git, but no `uv`, Maven, Gradle,
or third-party Python YAML/schema libraries available. The synthetic applications are
source fixtures; tests copy them into temporary Git repositories and do not download or
compile dependencies.

## Verified behaviour

The actual test command is:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/tests -v
```

Twenty-two tests pass: seven original foundation tests, six subprocess-level CLI
acceptance tests, and nine persisted-contract tests.

The original foundation behaviours remain covered:

1. Java/Maven inventory is deterministic and valid.
2. Kotlin/Gradle manifests and source files are recognized.
3. Sensitive files are excluded without being read.
4. Dirty repositories fail preflight and discovery.
5. Commit changes are detectable.
6. Source-repository Copilot customizations are reported.
7. Tampered observations fail validation.

The CLI acceptance suite now protects the complete current command surface, including:

- preflight returned a clean repository and full SHA;
- discovery emitted Git and generic-file observations;
- validation returned `valid: true`;
- status returned `changed: false` for the same commit.
- dirty-preflight, invalid-inventory, operational-error, and argument-error exit codes;
- `discover` standard-output and `--output` destination behaviour.

## Known limitations

- Maven and Gradle files are recognized but not parsed.
- Java and Kotlin files are classified but not semantically analyzed.
- `sources.json` is currently an empty declarative registry; CLI commands receive explicit
  paths.
- The observation inventory has an operational dependency-free validator. Claim and
  repository-profile schemas are not yet connected to a workflow.
- No Conductor workflow has been created.
- No private repository has been requested or analyzed.
- The source-registry contract is defined, but its loader and approved-path resolver are
  intentionally deferred to Slice 2.

## Recommended next milestone

Proceed through small, gated slices. Stabilize every deterministic producer-consumer
boundary before adding model execution or orchestration.

```text
contract fixtures
  -> approved source resolution
  -> bounded manifest extraction
  -> deterministic evidence selection
  -> candidate validation
  -> direct read-only Copilot trial
  -> Conductor orchestration
  -> human approval
  -> diff-based promotion
```

### Slice 1: stabilize deterministic contracts

Implemented on 2026-09-08. This slice defines contracts and tests; it does not implement
Maven or Gradle parsing, evidence selection, Copilot execution, Conductor workflows, or
catalog promotion.

Define representative JSON fixtures and the corresponding dependency-free validation
rules for:

1. Parsed manifest observations, including stable observation kinds, value shapes,
   detector versions, source paths, and line ranges.
2. Unsupported or dynamic manifest constructs, which must remain visible as explicit
   gaps rather than being silently omitted or promoted into inferred dependencies.
3. Source-registry entries and resolution results, including repository identity, kind,
   enabled state, resolved path, exclusions, and approved Copilot-configuration digest.
4. Evidence bundles, including repository identifier, analyzed commit, inventory digest,
   bounded selected content, observation references, exclusions, and unsupported gaps.
5. Model-produced candidate envelopes, including repository identifier, analyzed commit,
   evidence-bundle identity, and the proposed repository profile.
6. CLI JSON envelopes, exit-code meanings, error output, stable ordering, and destination
   path behavior for every planned command.

Add subprocess-level acceptance tests for the existing CLI commands so their complete
surface becomes a protected contract:

```text
./tools/landscape preflight SOURCE
./tools/landscape discover SOURCE --repository ID [--output PATH]
./tools/landscape validate INVENTORY [--source SOURCE]
./tools/landscape status SOURCE --inventory INVENTORY
```

Contract decisions required during this slice:

- Use JSON for machine-consumed persisted artifacts. `sources.yaml` was replaced with
  `sources.json` so the registry remains compatible with the standard-library-only
  runtime. JSON also remains YAML-compatible for a later integration if required.
- Treat Maven and Gradle extraction as bounded literal observation, not dependency
  resolution. Effective POMs, transitive dependencies, executed Gradle models, and
  computed declarations are outside the discovery safety boundary.
- Require exact source paths and line ranges where practical. Commit SHA pins the source
  revision; evidence bundles additionally carry an inventory digest so consumers can
  detect mismatched artifacts.
- Keep ordinary failures on standard error and machine-readable successful or validation
  results on standard output. Document every non-zero exit code before workflows consume
  it.
- Keep candidate data outside `catalog/` until it passes deterministic validation and a
  human approval gate.

Slice 1 is complete when the fixtures, schemas or equivalent operational validators,
CLI acceptance tests, and contract documentation agree; repeated serialization is
byte-for-byte stable; invalid examples fail closed; and no downstream implementation has
to guess a field or exit-code meaning.

### Slice 2: source resolution

Implement the registry loader and approved-path resolver against the Slice 1 contract.
Fail closed for duplicate identifiers or resolved paths, disabled sources, missing or
dirty repositories, non-root Git paths, symbolic-link or traversal escapes, invalid
exclusions, and unapproved Copilot customizations.

### Slice 3: manifest extraction and evidence selection

Add fixture-tested Maven XML extraction and conservative Gradle literal extraction. Never
execute Maven, Gradle, wrappers, hooks, or application code. Then produce deterministic,
bounded evidence bundles that reapply safety exclusions and verify that repository HEAD
still matches the inventory commit immediately before reading selected content.

### Slice 4: candidate validation and direct Copilot trial

Connect the claim and repository-profile contracts to dependency-free operational
validators. Run the read-only repository cartographer directly against both synthetic
repositories before adding orchestration. Reject evidence paths, commits, observation
references, statuses, or line ranges that cannot be verified from the selected bundle.

### Slice 5: Conductor and promotion

Review and pin a current Conductor release only after Slices 1-4 are stable. Create the
single-repository `workflows/analyze-repository.yaml` as a thin orchestration layer, add a
human approval gate, and implement single-writer promotion that displays the canonical
diff before writing.

Do not request private repositories or create platform and reconciliation workflows until
the complete single-repository flow is reliable against both synthetic repositories.

## Pilot success questions

The later private pilot must answer with evidence:

- What different meanings does a term such as `Customer` have?
- Who publishes and consumes an event?
- Where and how is an application deployed?
- Which Terraform resources support it?
- What is the likely blast radius of a contract change?
- Which claims are confirmed, inferred, or unknown?
- Which improvement has the most value, and what evidence and counterevidence support it?

Validate 10-20 terms and 5-10 relationships with humans before scaling.

## Bootstrap prompt for the new Codex task

Use this prompt from the `bounded-knowledge` project:

```text
Continue the Bounded Knowledge initiative. Read AGENTS.md, README.md, and
docs/handoffs/2026-09-07-foundation-handoff.md completely before taking action.

Treat the handoff as the durable design context and verify its implementation claims
against the current working tree. Preserve independent source-repository boundaries and
keep all repository artifacts in English. Do not request or analyze private application
repositories yet.

Slice 1 is implemented. Review `docs/contracts/slice-1.md`, its schemas, validators,
fixtures, and CLI acceptance tests. The next proposed milestone is Slice 2: implement the
source-registry loader and approved-path resolver against the reviewed contract. Do not
implement manifest parsers, evidence selection, Copilot execution, Conductor workflows,
or promotion as part of that slice.
```
