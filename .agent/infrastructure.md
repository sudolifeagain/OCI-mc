# Infrastructure & Deployment

## Server Environment (OCI)
- **OS**: Ubuntu (ARM based)
- **IP**: *Retrieved from context/user* (e.g. `161.xxx`)
- **SSH Access**: `ssh -i <local_key> ubuntu@<IP>`

## Directory Map (Remote)
- **/opt/minecraft/**: Root
  - **paper/**: Paper server (Vanilla compatible)
    - `paper.jar`, `plugins/`, `world/`
  - **forge/**: Forge server (Modded)
    - `run.sh`, `mods/`, `world/`
  - **bot/**: This repository deployment
    - `bot.py`, `.env`, `venv/`

## Deployment Flow
1. **GitHub Actions**: Triggered on push to `master`.
2. **Rsync**: Syncs files to `/opt/minecraft/bot/`.
   - Excludes: `.env`, `.git`, `venv`.
3. **Systemd**:
   - Service: `discord-bot`
   - Path: `/etc/systemd/system/discord-bot.service`
   - Restarted automatically after deploy.

## Sensitive Data Handling
- **Public Repo**: This codebase is public.
- **Secrets**:
  - `DISCORD_TOKEN`: Managed in `.env` (remote) and GitHub Secrets for CI.
  - SSH Keys: Never stored in repo.
