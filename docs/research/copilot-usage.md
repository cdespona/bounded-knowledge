# What usage data can a Copilot CLI subprocess report for a cost limit?

Research for issue #13. Investigated 2026-11-16 against GitHub Copilot CLI **1.0.107**
(installed) using only `copilot --help`, `copilot help <topic>`, the official docs, and the
upstream changelog (latest entry 1.0.112, 2026-11-13). No prompt was run, so no paid call
was made.

## Answer

Yes. The CLI can both report usage for each run and enforce a spend cap on it. Conductor
does not need to meter anything.

1. **Hard-ish cap per invocation: `--max-ai-credits <credits>`.** This is a session AI-credit
   limit (minimum 30). It applies to prompt (`-p`) runs, and subagents share it. It is a
   **soft cap**: the CLI only learns the cost after a model response returns, so one call can
   overshoot. After that, the next model call is blocked.
2. **Per-run usage record: `--usage-output-file <file>`.** This writes "final usage
   statistics as JSON" to a file. It works alongside `--silent`, so stdout stays the agent
   response that `run_stage` already captures. Since 1.0.81 the file also has per-agent
   usage metrics.
3. **Model and effort per invocation:** `--model <model>` (or the `COPILOT_MODEL` env var),
   `--reasoning-effort <level>`, and `--context <tier>`.
4. **Richer streams, if wanted:** `--output-format json` (JSONL events plus a final result
   record with exit status), or OpenTelemetry written to a local JSONL file through
   `COPILOT_OTEL_FILE_EXPORTER_PATH`. The OTel spans and metrics carry the model name, input,
   output, cache and reasoning token counts, durations and "costs".

Smallest useful change to `landscape stage run`: add `--max-ai-credits N` and
`--usage-output-file <stage-dir>/usage.json` to `COPILOT_FLAGS` or the command builder, and
optionally `--model`. Record the parsed file next to the response, and let a workflow gate
stop on cumulative credits across attempts. The units are **AI credits**. On the legacy
billing platform the CLI reports **premium requests** instead (see uncertainties).

## Evidence

### Current invocation

`tools/landscape_core/stages.py` builds
`copilot -C <dir> --agent <name> --available-tools=view --disable-builtin-mcps --disallow-temp-dir --no-ask-user --silent --prompt ...`
and keeps only `returncode`, stdout (the response) and stderr. Because of `--silent`, the
stats summary is never printed, so nothing usage-related is captured today.

### Flags (`copilot --help`, v1.0.107)

| Flag | Help text (verbatim) |
|---|---|
| `-s, --silent` | "Output only the agent response (no stats), useful for scripting with -p" |
| `--usage-output-file <file>` | "Write final usage statistics as JSON to the specified file" |
| `--max-ai-credits <credits>` | "Set max AI credits for this session" |
| `--model <model>` | "Set the AI model to use (use 'auto' to let Copilot pick automatically)" |
| `--reasoning-effort <level>` | "Set the reasoning effort level; supported levels depend on the model [possible values: none, minimal, low, medium, high, xhigh, max]" |
| `--context <tier>` | "Set the context window tier (overrides persisted setting) [possible values: default, long_context]" |
| `--output-format <format>` | "Output format: 'text' (default) or 'json' (JSONL, one JSON object per line)" |
| `--log-dir <directory>` / `--log-level <level>` | "Set log file directory (default: ~/.copilot/logs/)" / levels none to all |
| `--share [<path>]` | "Share session to markdown file after completion in non-interactive mode" |
| `--max-autopilot-continues <count>` | "Maximum number of continuation messages in autopilot mode [default: 5]" (only applies in autopilot mode) |

### `copilot help limits`

- "Session limits are opt-in. Usage accumulates across the entire session in both
  interactive and non-interactive runs."
- "In non-interactive prompt runs, usage accumulates across the whole run."
  "Subagents share the same session limit as the parent session."
- "Minimum: 30 AI credits."
- "The AI credit limit is a soft cap: usage is known only after a model response returns. A
  response can therefore exceed or exhaust the limit before the CLI can observe that it has
  done so; the next model call is then blocked."
- "Hidden model work such as compaction counts toward --max-ai-credits".
- When 10% or less remains: "If this session continues, the next model call is final; tools
  will be disabled."

### `copilot help billing`

- "Usage is measured in AI credits. If you're on the legacy billing platform, you may see
  premium requests instead."
- `/usage` "Shows AI credit usage for the current session, plus a token breakdown (input,
  output, and cached)". `/exit` "The exit summary reports the session's usage when you
  leave."
- Billing docs: <https://docs.github.com/en/copilot/concepts/billing> and legacy
  <https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy>.

### `copilot help monitoring` (OpenTelemetry)

- OTel is off by default. It turns on with `COPILOT_OTEL_ENABLED=true`,
  `OTEL_EXPORTER_OTLP_ENDPOINT`, or `COPILOT_OTEL_FILE_EXPORTER_PATH` ("Write all signals to
  this file as JSON-lines").
- "Spans carry attributes such as model name, token counts, durations, costs, and error
  info." The span tree is `invoke_agent` > `chat <model>` / `execute_tool <tool>`.
- Metrics include `gen_ai.client.inference.usage.input_tokens`, `.output_tokens`,
  `.cache_read.input_tokens`, `.cache_write.input_tokens`, `.reasoning.output_tokens`,
  `gen_ai.invoke_agent.inference_calls`, and `github.copilot.agent.turn.count`.
- "By default, no prompt content, responses, or tool arguments are captured — only
  metadata." This matters here because the repo must not persist secrets.

### `copilot help environment` / `copilot help config`

- `COPILOT_MODEL`: "optionally set the agent model. Can be overridden by the --model command
  line option".
- Config `workflows.defaultLimits.maxAiCredits`: "Fallback soft post-paid AI-credit ceiling
  for a workflow". This applies to Copilot's own `copilot workflow`, not Conductor.
- Config `subagents.agents.<agent-name>`: "per-subagent model, effortLevel, and contextTier
  selection."

### Upstream changelog (<https://github.com/github/copilot-cli/blob/main/changelog.md>)

- 0.0.333 (2025-10-02): "Added `/usage` slash command to provide stats about Premium request
  usage, session time, code changes, and per-model token use."
- 0.0.422 (2026-03-05): "Add --output-format json flag to emit JSONL in prompt mode for
  programmatic integrations"
- 1.0.81 (2026-08-27): "Add per-agent usage metrics to --usage-output-file JSON output"
- 1.0.108 (2026-11-09): "Include session start, resume, and model-change events in
  `-p --output-format json` output"
- 1.0.109 (2026-11-10): "Prompt mode with --output-format json emits a final result record
  with an exit status even when a run fails early"
- 1.0.112 (2026-11-13): "Subagents now respect the session-wide AI credit limit set with
  --max-ai-credits" and "Count previously used AI credits toward the session limit when
  resuming a session"

The two 1.0.112 entries mean that before 1.0.112 (which includes the installed 1.0.107),
subagents could get around the cap. Stages that keep `--available-tools=view` do not launch
subagents, so this is low risk here, but a minimum version is worth pinning.

### Official command reference

<https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference>
documents `--output-format json` and `--result-file` for `copilot workflow run`, and
`--max-ai-credits N` for `/goal` / `/autopilot` ("When the cap is reached, autopilot pauses
and opens a panel reporting credits used against the cap"). In the part read (the first
100k characters of a very long page), it does not document the `--usage-output-file` JSON
schema.

## Open uncertainties

- **Schema of `--usage-output-file`.** I found no documented field names (credits, premium
  requests, tokens, per-model breakdown, model id). Confirming them takes one real run, which
  this ticket did not allow. The parser should accept unknown fields and record the raw file.
- **What the process does when the cap is hit.** The docs say the next model call is
  "blocked". They do not say whether the process exits non-zero, with which code, or what the
  JSON result record contains. A workflow should check the usage file, not only the exit
  code.
- **Legacy vs usage-based billing.** Which units the account reports (AI credits or premium
  requests), and whether `--max-ai-credits` is enforced on a legacy-billing account, is not
  stated. The `/limits` text only mentions AI credits.
- **Cap granularity.** The minimum of 30 AI credits per session may be coarser than a single
  small stage needs. A cumulative bound across attempts still has to live in landscape or
  Conductor, by summing the per-run usage files.
- **Version drift.** Several relevant fixes (JSON result record, subagent cap enforcement)
  are newer than the installed 1.0.107. `copilot` auto-updates unless `--no-auto-update` is
  set, so the version that actually runs can change between attempts.
- **Model availability.** Valid `--model` ids depend on the account's plan and catalog. The
  help shows no command that lists them non-interactively (the `/model` picker is
  interactive).
