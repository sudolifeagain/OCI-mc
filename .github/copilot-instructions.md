# Copilot Custom Instructions

## Project Overview

Discord bot for Minecraft multi-server management, hosted on OCI (Oracle Cloud Infrastructure).

- **Language**: Python 3.11
- **Framework**: discord.py with slash commands only (`@app_commands.command`)
- **Concurrency**: asyncio throughout — all I/O must use `async/await`
- **Architecture**: `ServerInstance` (per-server state) + `MultiServerManager` (orchestrator)
- **Deployment**: `develop` branch → PR → merge to `main` → auto-deploy via GitHub Actions

## Code Conventions

- Use `@app_commands.command` exclusively. Prefix commands are not allowed.
- Type hints are required on all function arguments and return types.
- All I/O operations must use `async/await`.
- Linting: `ruff check . --select=E,F,W --ignore=E501 --exclude=venv`
  - **E501 (line length) is intentionally disabled** — do not flag long lines.
- `silent=True` on Discord messages is an intentional design choice to suppress notifications — do not suggest removing it.
- `convert_v3_v2_fix.py` is a standalone utility script (not part of the bot). Its compact formatting is intentional — do not flag style issues in this file.

## Architecture

| Module | Role |
|--------|------|
| `utils/server_manager.py` | Core process management: PID files, psutil, async stdin/stdout |
| `utils/rcon.py` | Pure asyncio RCON client. `execute()` returns `tuple[bool, str]` |
| `utils/permissions.py` | Role-based permission checks via `check_role(interaction, action)` |
| `cogs/` | Discord command modules. Each Cog receives `bot` and `server_manager` |
| `settings.py` | Config loader (config.json + .env) with backwards compatibility |

## Error Handling Policy

- **Network/RCON code uses broad `except Exception`** — this is intentional. RCON connections can raise `ConnectionRefusedError`, `OSError`, `asyncio.TimeoutError`, and other diverse exceptions. Do not suggest splitting into specific exception types.
- Best-effort data retrieval (TPS, MSPT, etc.) returns `None` on failure and the caller skips display. This is by design.
- Callers apply `asyncio.wait_for()` timeouts around RCON operations.

## Review Focus

Prioritize these areas when reviewing:

1. **Security**: Command injection, hardcoded secrets/IPs, unsanitized user input
2. **Async correctness**: Missing timeouts on `await`, resource leaks (unclosed writers/connections), missing `finally` cleanup
3. **Discord API**: Missing `defer()` before long operations (3-second interaction timeout), correct use of `ephemeral=True` for error responses
4. **Code reuse**: Duplicated logic that should use existing utility functions (e.g., `get_machine_stats()` in `status_display.py`)

## Do NOT Flag

These are intentional design decisions — do not suggest changes for:

- Line length violations (E501 is disabled)
- Splitting `except Exception` in network/RCON code
- Adding comments to regex patterns
- Modernizing type hints (`dict` → `Dict[str, Any]` etc.) — this project uses Python 3.11+ built-in generics
- Adding unit tests (no test framework is configured)
- Redundant `is_running()` checks where `get_stats()` already handles process absence internally
- Removing `silent=True` from Discord message sends
- Style issues in `convert_v3_v2_fix.py`
