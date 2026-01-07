# OCI-mc

A Discord bot designed to manage Minecraft servers running on Oracle Cloud Infrastructure (OCI), featuring backup integration with Notion and automated plugin management.

## Overview

OCI-mc allows you to control your Minecraft servers directly from Discord. You can start/stop servers, execute console commands, manage backups, and update plugins without needing to SSH into your server.

## Features

- **Multi-Server Management**:
  - Support for multiple Minecraft servers (Paper, Forge, etc.)
  - Start/Stop/Restart individual servers (`/start`, `/stop`, `/restart`)
  - Send console commands (`/cmd`)
  - Live server log streaming to Discord
  - Server status monitoring with CPU/Memory usage (`/status`)

- **Advanced Backup System**:
  - **Notion Integration**: Automatically uploads backup files to Notion with multipart upload support for large files (>20MB).
  - **Scheduled Backups**: Configurable automated backup schedule.
  - **Manual Backups**: Trigger backups on demand (`/backup`).
  - **Easy Rollback**: List available backups (`/backups`) and restore with a single command (`/rollback`).

- **Plugin Management**:
  - **List Plugins**: View all installed plugins with version info (`/plugins`).
  - **Update Check**: Check for plugin updates without stopping server (`/check_updates`).
  - **Auto Update**: Download and install latest plugin versions (`/update_plugins`).
  - Supports GeyserMC API and GitHub Releases for update detection via SHA256 hash comparison.

- **Permission Control**:
  - Role-based access control for commands.
  - Configurable role permissions in `config.json`.

## Prerequisites

- Python 3.10+
- An Oracle Cloud Infrastructure (OCI) instance (ARM based recommended for free tier).
- A Notion Integration Token and Database ID.
- A Discord Bot Token.

## Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd OCI-mc
   ```

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configuration**:
   - Copy `.env.example` to `.env` and configure environment variables.
   - Edit `config.json` for server-specific settings.

4. **Run the Bot**:
   ```bash
   python bot.py
   ```

## Commands

| Command | Description | Permission |
| :--- | :--- | :--- |
| `/start [server]` | Starts the Minecraft server. | `start` role |
| `/stop [server]` | Stops the Minecraft server. | `stop` role |
| `/restart [server]` | Restarts the Minecraft server. | `restart` role |
| `/cmd <command> [server]` | Sends a command to the server console. | `command` role |
| `/status [server]` | Shows server status (CPU, Memory, Uptime). | `status` role |
| `/whitelist_add <player> [server]` | Adds a player to whitelist. | `whitelist_add` role |
| `/backup [server]` | Creates a backup and uploads it to Notion. | `backup` role |
| `/backups` | Lists the latest 10 backups from Notion. | `backup` role |
| `/rollback <index>` | Restores the server from a selected backup. | `backup` role |
| `/plugins [server]` | Lists installed plugins with versions. | `status` role |
| `/check_updates [server]` | Checks for plugin updates (no server stop required). | `status` role |
| `/update_plugins [server]` | Updates all configured plugins to latest versions. | `backup` role |
| `/shell <command>` | Executes a shell command on the host. | Owner only |

## Plugin Configuration

Configure plugins in `config.json`:

```json
"plugins": {
  "paper": {
    "geyser": {
      "source": "geysermc",
      "project": "geyser",
      "platform": "spigot",
      "filename": "Geyser-Spigot.jar"
    },
    "bluemap": {
      "source": "github",
      "repo": "BlueMap-Minecraft/BlueMap",
      "asset_pattern": "bluemap-*-paper.jar",
      "filename": "bluemap-paper.jar"
    }
  }
}
```

Supported sources:
- `geysermc`: GeyserMC download API (Geyser, Floodgate)
- `github`: GitHub Releases API

