# Agent Guidance

## Purpose

This repository stores an evidence-backed catalog of independently versioned application
and platform repositories. It is an observatory, not a monorepo. Never treat sibling
source repositories as writable project modules.

## Architecture

- `tools/` contains deterministic discovery and validation logic.
- `schemas/` defines persisted contracts.
- `examples/` contains synthetic Java and Kotlin fixtures only.
- `work/` contains disposable generated artifacts and is not canonical.
- `catalog/`, `findings/`, and `views/` contain reviewed knowledge.
- Future `workflows/` files orchestrate scripts, Copilot agents, and human gates through
  Microsoft Conductor. Workflow YAML must not contain parsing or domain logic.

## Evidence rules

- Classify semantic assertions as `confirmed`, `inferred`, or `unknown`.
- Record the repository identifier, full commit SHA, source path, and line range when
  practical.
- Never invent a relationship, owner, term definition, deployment, or dependency.
- Deterministic scanners emit observations, not semantic claims.
- Treat unsupported formats as visible gaps rather than silently ignoring them.
- Keep evidence and counterevidence separate.

## Safety boundaries

- Do not modify source repositories.
- Do not execute builds, tests, wrappers, hooks, or application scripts from analyzed
  repositories during discovery.
- Do not read or persist `.env` files, Terraform state, kubeconfigs, private keys,
  certificates, secret values, generated code, vendor trees, or build output.
- Inspect source-repository Copilot customizations before granting Copilot CLI access to
  that directory.
- Do not use broad Copilot permissions such as `--allow-all` or `--allow-all-paths`.
- Keep model-backed analysis read-only. Promotion into the canonical catalog must pass a
  deterministic validator and a human approval gate.

## Development

The deterministic tooling supports Python 3.9 or later and uses only the standard
library in the initial milestone.

Run tests with:

```text
python3 -m unittest discover -s tools/tests -v
```

Run the command locally with:

```text
./tools/landscape --help
```

Keep changes small. Add fixture-based tests for every detector. Ensure JSON output is
stable for the same repository commit. Report changed files, validation performed, and
known gaps at handoff.

