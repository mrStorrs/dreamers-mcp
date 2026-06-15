# Dreamers MCP Codex Stats Ref

Use this ref only when the active Codex skill name starts with `dreamers-`.

<dreamers-mcp-skill-bookends>
Dreamers stats are best-effort only.

If this ref file is missing, unreadable, the installed stats shim is missing, Python is unavailable, or any stats command fails, continue the Dreamers workflow normally. Do not retry in a loop. Make at most one best-effort attempt per checkpoint.

Never include prompts, responses, tool outputs, diffs, transcript text, secrets, credentials, auth tokens, or PII in checkpoint metrics.

When running any `dreamers-*` skill:

1. Create one compact `run_id` near the start and reuse it for the whole skill invocation.
2. Record `skill_started` once near the beginning.
   - `--event-type skill_started`
   - `--skill <dreamers-skill-name>`
   - `--run-id <run-id>`
   - When known, use `mode` values from `task-description`, `plan-path`, or `manifest`.
3. After each validation command you run yourself, record `validation_attempt`.
   - Required metrics: `command_kind`, `command_label`, `attempt_number`, `result`
   - `command_kind` must be one of `typecheck`, `test`, `build`, `lint`, or `manual`
   - `result` must be one of `pass`, `fail`, or `skipped`
4. When the user decides a gate, record `gate_decided`.
   - Required metrics: `gate_type`, `decision`
   - `gate_type` must be one of `plan-approval`, `implementation-start`, `major-refactor`, `review-rerun`, `user-testing`, `pre-pr`, `pr-selection`, or `push-decision`
   - `decision` must be a schema value such as `approved`, `approved_start_implementation`, `approved_start_incremental`, `approved_start_atomic`, `revise_plan`, `halt`, `other`, `apply_now`, `defer_follow_up_plan`, `run_vigil`, `run_full_triad`, `run_selected_lane`, `skip`, or `bug_found`, whichever best matches the user's actual choice
5. If the Dreamers skill halts before normal completion, record `skill_halted`.
   - Required metrics: `halt_reason_category`
   - Prefer `user_halt`, `validation_failure`, or `other_safe` unless a more specific schema value is clearly correct
6. If the Dreamers skill finishes normally, record `skill_completed` once.
   - When known, prefer `final_status` values such as `completed`, `resolved`, or `approved`

Use the installed Codex stats shim at `$CODEX_HOME/dreamers/scripts/dreamers_stats.py` when `CODEX_HOME` is set, otherwise `~/.codex/dreamers/scripts/dreamers_stats.py`.

Use the Python command that actually exists in the current environment. Prefer `python3` on Linux or macOS, `python` when that is the installed command, and `py -3` on Windows when that is the available launcher.

Checkpoint command shape:

`<python-command> "<stats-shim>" checkpoint --client codex --home "<codex-home>" --event-type <event-type> --skill "<dreamers-skill-name>" --run-id "<run-id>" --metrics-json '<json-object>'`
</dreamers-mcp-skill-bookends>
