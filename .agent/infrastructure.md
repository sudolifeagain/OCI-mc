# Infrastructure & Deployment

## Server Environment (OCI)
- **OS**: Ubuntu (ARM based)
- **IP**: *Retrieved from context/user* (e.g. `161.xxx`)
- **SSH Access**: `ssh -i <local_key> ubuntu@<IP>`

## Directory Map (Remote)
- **/opt/minecraft/**: Root
  - **paper/**: Paper server (Vanilla compatible)
    - `paper.jar`, `plugins/`, `world/`
  - **forge/**: Forge server (Modded, Better MC 1.20.1)
    - `run.sh`, `mods/` (~590 mods, 1.1GB), `world/`
    - Memory: 10G, Port: 25566
  - **bot/**: This repository deployment
    - `bot.py`, `.env`, `venv/`

## Server Operations (SSH)

### Process Management
```bash
# Check running Java processes
ps aux | grep java | grep -v grep

# Start Forge server (background)
cd /opt/minecraft/forge && nohup ./run.sh nogui > /tmp/forge.log 2>&1 &

# Stop server (graceful -> force)
kill <PID>        # SIGTERM first
kill -9 <PID>     # SIGKILL if needed

# View startup log
tail -f /opt/minecraft/forge/logs/latest.log
```

### Whitelist Management (Direct Edit)
When console access is unavailable (e.g., nohup startup):
```bash
# Get player UUID from Mojang API
curl -s https://api.mojang.com/users/profiles/minecraft/<PlayerName>

# Edit whitelist.json directly (UUID format: 8-4-4-4-12)
echo '[{"uuid":"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx","name":"PlayerName"}]' > /opt/minecraft/forge/whitelist.json
```

### Important Notes
- **Discord Logs**: Only work when server is started via Bot (`/start`), not SSH
- **Bot monitors stdout** of processes it spawns; SSH-started servers are invisible to Bot

## Deployment Flow
1. **GitHub Actions**: Triggered on push to `master`.
2. **Rsync**: Syncs files to `/opt/minecraft/bot/`.
   - Excludes: `.env`, `.git`, `venv`.
3. **Systemd**:
   - Service: `discord-bot`
   - Path: `/etc/systemd/system/discord-bot.service`
   - Restarted automatically after deploy.

## Environment Variables (.env)

### Required
- `DISCORD_TOKEN`: Bot token

### Channels
- `DISCORD_CHANNEL_ID`: Default log channel (fallback)
- `DISCORD_PAPER_LOG_CHANNEL_ID`: Paper server log channel (optional)
- `DISCORD_FORGE_LOG_CHANNEL_ID`: Forge server log channel (optional)
- `DISCORD_STATUS_CHANNEL_ID`: Real-time status display channel (optional)

### Roles/Users
- `DISCORD_ADMIN_ID`: Admin role ID
- `DISCORD_MOD_ID`: Mod role ID
- `DISCORD_OWNER_ID`: Bot owner user ID (for `/shell`)
- `DISCORD_USER_IDS`: Allowed user IDs (comma-separated)

### Notion (Backup)
- `NOTION_TOKEN`: Notion API token
- `NOTION_DB_ID`: Notion database ID for backups

## Sensitive Data Handling
- **Public Repo**: This codebase is public.
- **Secrets**:
  - `DISCORD_TOKEN`: Managed in `.env` (remote) and GitHub Secrets for CI.
  - SSH Keys: Never stored in repo.
