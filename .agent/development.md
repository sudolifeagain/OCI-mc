# Development Guide

## Commands
- **Install**: `pip install -r requirements.txt`
- **Run Local**: `python bot.py`
  - Ensure `.env` exists with `DISCORD_TOKEN`.
- **Lint**: Use `ruff` or `flake8`.

## Architecture: Cogs System
The bot uses `discord.py` Cogs extension pattern in `cogs/`.

### 1. Basic Control (`cogs.basic_control`)
- **File**: `cogs/basic_control.py`
- **Role**: Server lifecycle (start, stop, restart, status).
- **Key Logic**: Calls `MultiServerManager` to interact with subprocesses.
- **Log Streaming**: Bot captures stdout from spawned processes and forwards to Discord.
  - Only works for servers started via Bot (not SSH/manual startup)
  - Uses `asyncio.Queue` in `ServerInstance.log_queue`

### 2. Backup System (`cogs.backup_system`)
- **File**: `cogs/backup_system.py`
- **Role**: Backups to Notion.
- **Key Logic**: Compresses world files -> Uploads to Notion API.
- **Triggers**: Scheduled (loop) or Manual (`/backup`).

### 3. Plugin System (`cogs.plugin_system`)
- **File**: `cogs/plugin_system.py`
- **Role**: Update management.
- **Key Logic**: Checks SHA256 hashes against APIs (Geyser, GitHub Releases).

## Code Style
- **Slash Commands Only**: No `command_prefix`. Use `@app_commands.command`.
- **Type Hinting**: Required for all function arguments and returns.
- **Async**: Strictly used for all blocking operations (IO, Net, Subprocess).
