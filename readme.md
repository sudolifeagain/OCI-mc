# OCI-mc

A Discord bot designed to manage a Minecraft server running on Oracle Cloud Infrastructure (OCI), featuring a unique backup system integrated with Notion.

## Overview

OCI-mc allows you to control your Minecraft server directly from Discord. You can start/stop the server, execute console commands, and manage backups without needing to SSH into your server. It leverages Notion as a database and storage solution for your world backups.

## Features

- **Server Management**:
  - **Start/Stop**: Turn the server on or off with simple commands (`!start`, `!stop`).
  - **Console Commands**: Send commands directly to the Minecraft server console (`!cmd`).
  - **Live Logging**: Streams Minecraft server logs to a specified Discord channel in real-time.

- **Advanced Backup System**:
  - **Notion Integration**: Automatically uploads compressed backup files (`.tar.gz`) to Notion.
  - **Scheduled Backups**: Configurable automated backup schedule.
  - **Manual Backups**: Trigger backups on demand (`!backup`).
  - **Easy Rollback**: List available backups (`!backups`) and restore the server to a previous state with a single command (`!rollback`).

- **Permission Control**:
  - Role-based access control for commands (supports `start`, `stop`, `command`, `backup` roles).
  - Whitelist support for specific commands for user roles.

## Prerequisites

- Python 3.8+
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
   - Create a `config.json` or configure `settings.py` with your environment variables.
   - Required configuration includes:
     - `DISCORD_TOKEN`: Your Discord Bot Token.
     - `DISCORD_OWNER_ID`: Your Discord User ID (for admin/shell commands).
     - `CHANNEL_ID`: The Discord Channel ID for logs and notifications.
     - `NOTION_TOKEN`: Your Notion Integration Token.
     - `NOTION_DB_ID`: The ID of the Notion Database for backups.
     - `minecraft_server_jar`: Path to your server jar file.
     - `java_memory`: Memory allocation (e.g., "4G").

4. **Run the Bot**:
   ```bash
   python bot.py
   ```

## Usage

### Commands

66: | Command | Description | Permission |
67: | :--- | :--- | :--- |
68: | `/start` | Starts the Minecraft server. | `start` role |
69: | `/stop` | Stops the Minecraft server. | `stop` role |
70: | `/cmd <command>` | Sends a command to the server console. | `command` role |
71: | `/backup` | Creates a backup and uploads it to Notion. | `backup` role |
72: | `/backups` | Lists the latest 10 backups from Notion. | `backup` role |
73: | `/rollback <index>` | Restores the server from a selected backup. | `backup` role (Admin recommended) |
74: | `/shell <command>` | Executes a shell command on the host (Owner only). | Owner |

### Roles

To use the bot, ensure users have roles named correspondinly to the permissions (e.g., `start`, `stop`, `backup`). You can configure role names in `settings.py`.
