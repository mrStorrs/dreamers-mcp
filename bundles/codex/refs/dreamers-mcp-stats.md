# Dreamers MCP Codex Stats Ref

Use this ref only when the active Codex skill name starts with `dreamers-`.

<dreamers-mcp-skill-bookends>
Dreamers stats are best-effort only.

If this ref file is missing, unreadable, the Dreamers stats MCP tool is unavailable, or any stats tool call fails, continue the Dreamers workflow normally. Do not retry in a loop. Make at most one best-effort attempt per checkpoint.

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

Use the Dreamers stats MCP tool `mcp__dreamers_stats.record_checkpoint` for every checkpoint. Do not run a Python stats shim for Dreamers skill bookends.

Tool call shape:

- `client`: `codex`
- `home`: the active Codex home, usually `$CODEX_HOME` or `~/.codex`
- `event_type`: checkpoint event type such as `skill_started`, `validation_attempt`, `gate_decided`, `skill_halted`, or `skill_completed`
- `skill`: active Dreamers skill name
- `run_id`: compact run id reused for the skill invocation
- `repo_path`: current repository path when known
- `branch`: current git branch when known
- `metrics`: compact schema-safe metrics for that checkpoint
</dreamers-mcp-skill-bookends>
