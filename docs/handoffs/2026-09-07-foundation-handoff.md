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

Pin Conductor to an explicit release rather than tracking `main`. The available design
session used the `microsoft/conductor` skill pinned to `v0.1.18` as a reference, but the
actual version must be verified before installation.

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

The new project is an initialized Git repository on branch `main`. No commit has been
created and all project files are currently untracked.

Implemented files include:

```text
README.md
AGENTS.md
.github/copilot-instructions.md
.gitignore
pyproject.toml
sources.yaml
schemas/source.schema.json
schemas/observation.schema.json
schemas/claim.schema.json
schemas/repository-profile.schema.json
tools/landscape
tools/landscape_core/
tools/detectors/git.py
tools/detectors/generic_files.py
tools/tests/test_landscape.py
examples/synthetic-java-service/
examples/synthetic-kotlin-service/
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

Seven tests pass:

1. Java/Maven inventory is deterministic and valid.
2. Kotlin/Gradle manifests and source files are recognized.
3. Sensitive files are excluded without being read.
4. Dirty repositories fail preflight and discovery.
5. Commit changes are detectable.
6. Source-repository Copilot customizations are reported.
7. Tampered observations fail validation.

An end-to-end Kotlin CLI smoke test also passed:

- preflight returned a clean repository and full SHA;
- discovery emitted Git and generic-file observations;
- validation returned `valid: true`;
- status returned `changed: false` for the same commit.

## Known limitations

- Maven and Gradle files are recognized but not parsed.
- Java and Kotlin files are classified but not semantically analyzed.
- `sources.yaml` is currently an empty declarative registry; CLI commands receive explicit
  paths.
- The observation inventory has an operational dependency-free validator. Claim and
  repository-profile schemas are not yet connected to a workflow.
- No Conductor workflow has been created.
- No private repository has been requested or analyzed.
- No project files have been committed.

## Recommended next milestone

Implement one synthetic vertical slice before using private source repositories:

1. Add deterministic Maven and Gradle parsers with fixture-based tests.
2. Add a source-registry loader and approved-path resolution.
3. Implement evidence selection without reading sensitive content.
4. Pin and install a reviewed Conductor release.
5. Create `workflows/analyze-repository.yaml`.
6. Add a read-only `repository-cartographer` prompt with a strict output schema.
7. Route its output through the deterministic validator.
8. Add a human approval gate.
9. Add a promotion script that shows a diff before writing to `catalog/`.
10. Exercise the complete flow against both synthetic repositories.

Do not create platform or reconciliation workflows until this single-repository flow is
reliable.

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

Start by reviewing the implemented deterministic foundation and propose the smallest
implementation plan for the next milestone: Maven/Gradle parsing, source-registry path
resolution, evidence selection, and the first synthetic Conductor workflow. Do not create
the workflow until the deterministic command contracts it consumes are stable and
reviewed.
```

