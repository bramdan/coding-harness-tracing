# Codex CLI Tracing

Automatic [OpenInference](https://github.com/Arize-ai/openinference) tracing for the OpenAI Codex CLI. Spans are exported to [Arize AX](https://arize.com) or [Phoenix](https://github.com/Arize-ai/phoenix).

## Setup
The installer prompts for your backend (Phoenix or Arize AX) and project name, writes credentials to `~/.arize/harness/config.json`, and registers the hook entries plus the `notify` token-usage backstop in `~/.codex/config.toml`. After installing, approve the hooks via Codex's `/hooks` command (one time per user account).

Pass `--with-skills` to also symlink the `manage-codex-tracing` skill into the current directory's `.agents/skills/` so coding agents in this workspace can help manage Codex tracing configuration.

### Remote setup

#### macOS / Linux

Install:

```bash
curl -sSL https://raw.githubusercontent.com/Arize-ai/coding-harness-tracing/main/install.sh | bash -s -- codex
```

Uninstall:

```bash
curl -sSL https://raw.githubusercontent.com/Arize-ai/coding-harness-tracing/main/install.sh | bash -s -- uninstall codex
```

#### Windows (PowerShell)

Install:

```powershell
iwr -useb https://raw.githubusercontent.com/Arize-ai/coding-harness-tracing/main/install.bat -OutFile $env:TEMP\install.bat
& $env:TEMP\install.bat codex
```

Uninstall:

```powershell
iwr -useb https://raw.githubusercontent.com/Arize-ai/coding-harness-tracing/main/install.bat -OutFile $env:TEMP\install.bat
& $env:TEMP\install.bat uninstall codex
```

### Local setup

```bash
git clone https://github.com/Arize-ai/coding-harness-tracing.git
cd coding-harness-tracing
```

**macOS / Linux**

Install:

```bash
./install.sh codex
```

Uninstall:

```bash
./install.sh uninstall codex
```

**Windows (PowerShell)**

Install:

```powershell
install.bat codex
```

Uninstall:

```powershell
install.bat uninstall codex
```

## Default Settings

| Setting | Default |
|---------|---------|
| Harness key | `codex` |
| Project name | `codex` |
| Phoenix endpoint | `http://localhost:6006` |
| Arize AX endpoint | `otlp.arize.com:443` |
| Hook config file | `~/.codex/config.toml` |
| Hook events handled | `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `Stop` (via real Codex hooks); `agent-turn-complete` (via `notify`) for token usage |
| Env override file | `~/.codex/arize-env.sh` |
| State directory | `~/.arize/harness/state/codex/` (state files + tool span JSONLs) |
| Log file | `~/.arize/harness/logs/codex.log` |

## Trust prompt

Codex requires explicit user trust for non-managed hooks before they fire. After install, run:

1. `codex` (start a session)
2. Type `/hooks` and approve each `arize-hook-codex-*` entry.

Without this one-time approval, hooks won't fire and traces will be limited to the `notify`-based fallback (single LLM span per turn, no tool spans).

## Verifying tracing

### Structured MCP completions in notify rollouts

When the rollout contains `event_msg` / `mcp_tool_call_end`, the notify parser
emits a TOOL span named `<server>.<tool>` using the recorded invocation, arguments,
result and call ID. Tool names are preserved exactly, including underscores.
`Err` results and successful MCP responses with `isError: true` produce error
status. Missing/unrecognized results remain unset. Argument and result logging
continue to follow `ARIZE_LOG_TOOL_DETAILS` and `ARIZE_LOG_TOOL_CONTENT`; status
messages never contain raw error content.

The completion timestamp anchors the end; a valid `{secs, nanos}` duration supplies
the start. Nanosecond duration is preserved relative to the rollout timestamp's
precision. Missing/invalid duration produces a zero-length interval. Duplicate
representations with the same call ID emit one MCP span; calls without IDs are
retained separately.

MCP spans are children of the turn: these events do not identify their parent
exec invocation. If both transcript order and the recorded interval fit exactly
one completed custom `exec`, that exec is retained as a CHAIN context span rather
than counted as another TOOL. Parallel MCP calls are all retained. Ambiguous or
incomplete exec intervals, discovery-only exec calls and other tool types retain
their existing tool representation. No JavaScript is parsed to infer execution.
Older rollouts without structured MCP completions retain their existing behavior.

### Fresh-session check

Run any Codex command:

```bash
codex exec "explain what this file does" path/to/file.py
```

Then check:

- Hook activity in `~/.arize/harness/logs/codex.log`.
- Per-thread state files in `~/.arize/harness/state/codex/` show recent activity.
- Spans appear in your configured project in Arize AX or Phoenix.

Errors are always logged. For routine hook activity, add `export ARIZE_VERBOSE=true` to `~/.codex/arize-env.sh` (or your shell) and re-run. See the [main README's Environment variables section](../../README.md#environment-variables) for the full list of runtime overrides (`ARIZE_TRACE_ENABLED`, `ARIZE_DRY_RUN`, `ARIZE_TRACE_DEBUG`, etc.).

## Troubleshooting

**Hooks not firing.** Run `codex` → `/hooks` and confirm the `arize-hook-codex-*` entries are listed and trusted. If they aren't listed at all, re-run the installer.

**No spans appear.** Re-source your shell profile (or open a new terminal) so `~/.codex/arize-env.sh` is loaded. Check `~/.arize/harness/logs/codex.log` for backend/auth errors. Confirm the hooks are trusted via `/hooks`.

**Disable temporarily.** Untrust the entries via `codex` → `/hooks`, or set `ARIZE_TRACE_ENABLED=false` in `~/.codex/arize-env.sh` and restart Codex. Full uninstall: `./install.sh uninstall codex`.
