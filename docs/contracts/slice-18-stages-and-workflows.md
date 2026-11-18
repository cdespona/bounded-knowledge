# Slice 18: stage commands and Conductor workflows

The manual pilot ran its model steps through ad hoc shell scripts that staged inputs, pasted prompts and
looped on validation. This slice moves that into tested commands and lets Conductor only sequence them
(milestone 8 of the plan).

## `landscape stage`

| Command | Does |
| --- | --- |
| `stage prepare STAGE --output-dir DIR --evidence … [--candidate …] [--answers …] [--analyzed-at …]` | Validates the inputs, copies them into `DIR/inputs` under repository-based names, copies the one agent definition, renders `DIR/prompt.txt` from `workflows/prompts/`, and records `inputs.sha256`. Refuses a non-empty directory. |
| `stage run DIR --output FILE [--timeout S] [--max-ai-credits N] [--model M]` | Runs the staged agent once under a credit cap and stores stdout, stderr and `DIR/usage.json`. Prints the outcome with a `usage` summary. Exit code 1 if Copilot failed, 3 if the credit cap was reached (see Credit budgets). |
| `stage usage DIR…` | Sums `usage.json` of the given stage folders by stage and model; stable, sorted JSON. Folders without a readable report are listed under `problems`. |
| `stage check STAGE --response FILE --evidence … [--answers …] [--current CATALOG] --output FILE` | Extracts and validates a captured response with the same checks as `candidate validate`, `questionnaire assemble` and `catalog extract` plus `propose-validate`. Writes the document only when valid. |

Stages are `cartographer`, `curator-business` (the business-semantics batch; runtime bindings come from
`questionnaire bindings`) and `synthesis`. The questionnaire shape the curator must follow is generated from the
evidence bundles, not pasted into the prompt by hand.

Copilot is always started as `copilot -C DIR --agent NAME --available-tools=view --disable-builtin-mcps
--disallow-temp-dir --no-ask-user --silent --max-ai-credits N --usage-output-file DIR/usage.json [--model M]
--prompt …`, with `DIR` holding only the staged inputs.

## Credit budgets

`workflows/stage-budgets.json` (checked in, validated on every run) holds `minMaxAiCredits` (30, Copilot's minimum)
and per stage a `maxAiCredits` integer plus an optional `model`. It is a data file rather than constants so that
the survey, synthesis and auditor workflows (#26, #30) can set a stronger model per stage without code changes.
`--max-ai-credits` and `--model` override it per run; a value below 30 is refused before Copilot starts.

The cap is soft: Copilot may overshoot it by the credits of the call in flight. `stage run` therefore treats the
stage as capped, prints an error and exits 3 (never a success; `workflows/cartographer.yaml` ends on exit 3 instead
of retrying) when the report's `aiCreditsStatus` is not `complete`, when credits used reach the cap, or when Copilot
exits non-zero with a credit or budget message on stderr.

`usage.json` is Copilot's own report, kept as written. Fields read, confirmed by one real run (Copilot CLI 1.0.112,
cartographer on the synthetic fixture, 3.3 credits): `totalNanoAiu` and `modelMetrics.<model>.totalNanoAiu`
(credits = nano / 1e9) and `aiCreditsStatus`. A sanitized copy is `examples/contracts/copilot-usage.sample.json`.
Unconfirmed: the 1e9 nano-per-credit factor is inferred from the cap being in credits, and the exact
`aiCreditsStatus` value and exit status when the cap is hit were not observed (the real run stayed far under 30), so
any status other than `complete` is treated as capped. A missing, unparseable or unrecognized report is reported in
the outcome (`usage.status` and `notes`, credits null) and never guessed at.

Per-run totals come from `stage usage`; adding it to the review output is left to #26, since no review command
takes stage folders yet.

Before building that command, `stage run` checks the stage directory and refuses it, without starting Copilot, if:

- it holds any customization file other than its one agent definition: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`,
  `.github/copilot-instructions.md`, `*.instructions.md`, `*.prompt.md`, `*.agent.md`, `*.chatmode.md`, MCP
  configuration (`mcp.json`, `.mcp.json`, `mcp-config.json`), or anything under `.github/{agents,instructions,skills,
  prompts,chatmodes,hooks,copilot}`, `.copilot`, `.claude`, `.agents`, `.vscode`, `.cursor` or `.gemini`;
- the agent definition differs byte for byte from the observatory's copy in `.github/agents/`;
- it is, or contains, a symbolic link;
- it lies inside a Git repository other than this one, whose own customizations Copilot could load from a parent.

The check only reads names and compares bytes. It replaces the earlier manual review of source-repository
customizations, which mattered only while Copilot was pointed at source directories.

## Why Conductor does not call Copilot itself

Conductor's Copilot provider answers every tool permission request with approval and relies on the workflow's `tools:`
allowlist. That is equivalent to broad path permissions for `view`, which `AGENTS.md` forbids. Workflows therefore use
only `script` steps that run `./tools/landscape`, and `stage run` keeps the hardened flags in tested code.
`tools/tests/test_workflows.py` rejects agent steps, providers and broad permission flags in `workflows/`.

## Workflows

Pinned release: Conductor v0.1.44 (`microsoft/conductor`, commit `65e63232`). Run from the repository root.

- `workflows/cartographer.yaml`: prepare, run, check, with at most three attempts. Verified against real evidence.
- `workflows/promotion.yaml`: validate, review, human gate, approve, promote. The first gate option is reject, so
  `--skip-gates` never approves. The approver types the review digest; it is passed to `catalog approve --reviewed`.
  Verified on a throwaway catalog: the reject path writes nothing; the approve path promotes.

Limits:

- Cost is limited by the attempt cap (`max_iterations`) and by the per-invocation credit cap of `stage run`.
  Conductor does not meter the subprocess, and the credit cap is soft.
- Evidence arguments of `promotion.yaml` are space separated and must not contain spaces.
- Not orchestrated yet: discovery and evidence selection, the curator and synthesis stages, `link-config`, the
  evidence auditor and the candidate reviser.
