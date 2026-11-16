# Surviving a multi-day human pause in Conductor v0.1.44

Research for issue #12. Sources: `microsoft/conductor` at tag `v0.1.44`, commit
[`65e632328dedbb2a15ef546ca76fce64e5bdcc68`](https://github.com/microsoft/conductor/tree/65e632328dedbb2a15ef546ca76fce64e5bdcc68)
(`pyproject.toml` declares `version = "0.1.44"`). All links below are pinned to that commit.
`B` below is shorthand for `https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68`.

## Answer

Conductor v0.1.44 can keep a run parked at a gate for days. It can also restart a run that
was killed while parked, without rerunning the steps before the gate. Neither works with the
current workflow files as written, and Conductor has no step that deliberately pauses, saves
and exits so the run can be picked up later.

1. **Long-lived gate (`--web-bg`).** `conductor run --web-bg` forks a detached process that
   waits at a `human_gate` until someone answers from the dashboard or with
   `conductor gate respond --port <port> --choice <value>`. Gates have no timeout of their own.
   The limits are:
   - `workflow.limits.timeout_seconds` is measured in wall-clock time and includes the time
     spent waiting at the gate. `promotion.yaml` sets 7200 s, so the run would fail after
     2 hours.
   - The wait only lasts as long as the OS process does. Sleep, reboot, logout or a crash
     ends it. Conductor has no daemon or service mode.
2. **Checkpoint and resume.** By default a checkpoint is written only when the run fails.
   With `runtime.checkpoint.every_agent: true`, Conductor also writes one at every step
   boundary. The checkpoint just before the gate holds every earlier step's output. If the
   process dies, `conductor resume <workflow.yaml> [--web-bg]` reruns only the step that was
   about to run (the gate). Earlier paid steps are not repeated. Stopping a `--web-bg` run
   from the dashboard (Stop/Kill) or with `conductor stop` also writes a checkpoint first.
   A resumed run gets a fresh timeout window.
3. **What is not supported:**
   - **No "suspend and exit" step.** `type: terminate` with `status: failed` is explicitly
     not resumable, and `status: success` is a clean finish.
   - **No gate expiry.**
   - **No durable checkpoint store.** Checkpoints go to `$TMPDIR/conductor/checkpoints/`.
   - **No protection against edits.** If the workflow file changes between checkpoint and
     resume, Conductor only prints a warning, unless retained-workspace persistence (Docker)
     is in use.
   - **Sub-workflow state is not resumable.**
   - **Interrupted steps rerun.** A step that was in flight when the run died is rerun from
     the top, so a script killed mid-run (for example `landscape stage run`) is paid for again.
   - **`wait` is not a long pause.** A `wait` step is capped at 24 hours.

**Practical recommendation.** Split the flow into separate workflows chained by files on disk:

- One workflow ends once the questionnaire has been produced and validated.
- The curator takes as many days as needed.
- A second workflow starts from the answers file.

This matches how our workflows already work: only `type: script` steps calling
`./tools/landscape`, each writing validated files. It needs no long-running process, does not
depend on temp-directory checkpoints, and can be audited from the files.

If one continuous run is wanted anyway, every one of these is required:

- `--web-bg`
- no `limits.timeout_seconds`, or one large enough to cover the pause
- `runtime.checkpoint.every_agent: true`
- a recovery procedure: `conductor checkpoint list`, then `conductor resume … --web-bg`, then
  `conductor gate respond` on the new port

## Evidence

### Background runs and gates

- `--web-bg` means "Run in background, print dashboard URL, exit". The CLI forks a background
  process that shuts down after the workflow completes and every browser client has
  disconnected.
  [B/docs/cli-reference.md#L73-L74](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/cli-reference.md#L73-L74),
  [#L140](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/cli-reference.md#L140)
- Background runs answer gates through the dashboard modal or `conductor gate respond`, never
  through stdin. The same holds for `conductor resume --web-bg`.
  [docs/cli-reference.md#L201-L214](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/cli-reference.md#L201-L214)
- `conductor gate respond` sends an HTTP request to the running dashboard. It takes `--port`
  (required), `--choice` (required), `--agent` (auto-discovered through `/api/gate-status`),
  `--input` and `--token`. The token comes from `CONDUCTOR_GATE_TOKEN` or from
  `~/.conductor/runs/dashboard-<port>.token`. The command exits 1 if no gate is waiting. It
  only works while that process is alive.
  [docs/cli-reference.md#L866-L937](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/cli-reference.md#L866-L937)
- In the engine, a gate in background mode waits on the web dashboard only
  (`_wait_for_web_gate`), with no timeout. A parked gate ends only through the outer stop/kill
  path, which writes a checkpoint (issue #245). If the dashboard failed to start in a
  `--web-bg` child, the gate raises `HumanGateError`.
  [src/conductor/engine/workflow.py#L4930-L5011](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/src/conductor/engine/workflow.py#L4930-L5011)
- The `questions` step type has no `timeout_seconds`, and the docs say "there is no bound on
  how long a human may take". Partial answers are written to the context after each response,
  so a checkpoint taken mid-step keeps them and resume continues at the first unanswered
  question.
  [docs/workflow-syntax.md#L1101-L1125](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/workflow-syntax.md#L1101-L1125)

### Wall-clock timeout includes the pause

- The whole execution loop runs inside `self.limits.timeout_context()`, which is
  `asyncio.timeout(timeout_seconds)`. Gate waits are not excluded.
  [src/conductor/engine/workflow.py#L5897](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/src/conductor/engine/workflow.py#L5897),
  [src/conductor/engine/limits.py#L373-L412](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/src/conductor/engine/limits.py#L373-L412)
- The docs agree: the timeout "includes all agent execution time and overhead", and leaving it
  unset means no limit.
  [docs/workflow-syntax.md#L2101-L2105](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/workflow-syntax.md#L2101-L2105)
- On resume, the timeout is read from the current config and `start_time` is reset, so the
  window starts fresh.
  [src/conductor/engine/limits.py#L116-L157](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/src/conductor/engine/limits.py#L116-L157)

### Checkpoints

- By default a checkpoint is written only on failure. Periodic checkpoints are opt-in:
  `runtime.checkpoint.every_agent` saves at every step boundary, including gate and script
  boundaries; `every_seconds` sets a minimum gap between saves; `keep_last` defaults to 5.
  Each checkpoint points at the step about to run, so resume reruns only that step.
  Checkpoints are written by the root workflow only, so sub-workflows rerun from scratch.
  Periodic checkpoints are deleted when the run finishes cleanly or through an explicit
  `status: failed` terminate.
  [docs/workflow-syntax.md#L2170-L2215](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/workflow-syntax.md#L2170-L2215),
  [L76-L79](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/workflow-syntax.md#L76-L79)
- In the engine, `_maybe_save_periodic_checkpoint()` runs at the top of each loop iteration,
  after earlier outputs have been written to the context. It skips the first step boundary of
  a fresh run.
  [src/conductor/engine/workflow.py#L5913-L5918](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/src/conductor/engine/workflow.py#L5913-L5918),
  [#L4260-L4285](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/src/conductor/engine/workflow.py#L4260-L4285)
- Checkpoints are written to `Path(tempfile.gettempdir()) / "conductor" / "checkpoints"`,
  which is `$TMPDIR/conductor/checkpoints/`. The file records the workflow inputs, the
  context, and a SHA-256 hash of the workflow file.
  [src/conductor/engine/checkpoint.py#L336-L365](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/src/conductor/engine/checkpoint.py#L336-L365)
  The resume example in the CLI docs shows `~/.conductor/checkpoints/…` instead
  ([docs/cli-reference.md#L416](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/cli-reference.md#L416)).
  The code is what decides the location.
- `conductor checkpoint list [WORKFLOW]` lists saved checkpoints.
  [docs/cli-reference.md#L996-L1017](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/cli-reference.md#L996-L1017)

### Resume

- `conductor resume [WORKFLOW] | --from <checkpoint.json>` resumes "a failed or interrupted
  workflow run". It accepts `--web-bg`, `--skip-gates`, `--metadata` and `--guidance`.
  [docs/cli-reference.md#L337-L362](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/cli-reference.md#L337-L362)
- If the workflow file's hash differs from the one in the checkpoint, resume only prints a
  warning: "Resume may produce unexpected results".
  [src/conductor/cli/run.py#L3356-L3365](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/src/conductor/cli/run.py#L3356-L3365)
  The strict digest check, which fails closed, applies only to workflows using
  retained-workspace persistence.
  [docs/cli-reference.md#L370-L385](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/cli-reference.md#L370-L385)
- An interrupted step reruns from the top (`restart.mode: rerun`, the default) unless it sets
  `mode: fail`. The `interrupted_step` record that drives this exists only for
  retained-workspace workflows.
  [docs/cli-reference.md#L388-L407](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/cli-reference.md#L388-L407)

### Stopping without losing progress

- `conductor stop` steps up in this order: first a graceful cancel through the dashboard
  (`POST /api/kill`), which writes a checkpoint; then SIGTERM; then SIGKILL. Stopping a
  foreground run with plain SIGTERM loses in-flight progress unless periodic checkpoints are
  on.
  [docs/cli-reference.md#L568-L620](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/cli-reference.md#L568-L620)

### No deliberate suspend

- A `terminate` step with `status: failed` produces exit 1 and is listed as **not**
  resumable: "explicit terminations skip the on-failure checkpoint". A `status: success`
  terminate is a clean exit.
  [docs/workflow-syntax.md#L1708-L1711](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/workflow-syntax.md#L1708-L1711)
- `wait` step durations must be between 0 and 24 hours.
  [docs/workflow-syntax.md#L1299](https://github.com/microsoft/conductor/blob/65e632328dedbb2a15ef546ca76fce64e5bdcc68/docs/workflow-syntax.md#L1299)

## How this applies to our workflows

- `workflows/promotion.yaml` sets `limits.timeout_seconds: 7200` and does not configure
  `runtime.checkpoint`. A parked `approval_gate` therefore fails after 2 hours. If the process
  dies before that, no checkpoint exists, because a gate wait killed with SIGKILL or by a
  reboot is not a failure the engine can catch.
- The paid step in `workflows/cartographer.yaml` is the `run` script. If that script is
  interrupted, resume reruns it, and Conductor cannot tell that the model call already
  happened. To avoid paying twice, `run` and `check` would need to detect the
  `response.txt` already captured in `work_dir` and skip the call. That is our tooling's job,
  not Conductor's.
- Chaining workflows through files fits the existing contract: `landscape stage check` writes
  a document only when it is valid, and the following workflow takes that path as a
  `workflow.input`.

## Open questions

- **How long `$TMPDIR` lasts.** On macOS, `$TMPDIR` is a per-user directory under
  `/var/folders`, and the OS can delete files there that have not been used for a while. This
  was not checked against Apple sources. Whether a checkpoint survives several days or a
  reboot depends on the host. `tempfile.gettempdir()` respects `TMPDIR`, so pointing it at a
  durable directory might work, but this was not tested.
- **Gate re-prompt on resume.** Not tested by running it. The behaviour stated above comes
  from reading the source and docs: a resume from the periodic checkpoint before the gate
  shows the gate again with the earlier script outputs restored.
- **Behaviour while waiting with no browser attached.** The docs say a background process
  exits only after the workflow completes and every client has disconnected. They do not
  explicitly cover a gate parked for days with no client attached. The engine code shows no
  idle timeout on the web gate wait, but `src/conductor/web/server.py` was not audited for
  other shutdown paths.
- **Inputs on resume.** The checkpoint stores workflow inputs, so a resumed run reuses the
  original input paths. If files under `work_dir` are moved during the pause, steps after
  the gate will read stale paths.
