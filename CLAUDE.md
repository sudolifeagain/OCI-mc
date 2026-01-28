# Bash commands
- `pip install -r requirements.txt`: Install dependencies
- `python bot.py`: Run bot locally (requires .env)
- `ssh -i ~/.ssh/id_ed25519 ubuntu@<OCI_IP>`: SSH into server
- `ps aux | grep java`: Check running server processes (remote)

# Code style
- **Slash Commands**: Use `@app_commands.command` (no prefix commands)
- **Type Hints**: Required for all arguments and returns
- **Async**: Use `async/await` for all I/O functions

# Workflow
- **Deploy**: Push to `master` to trigger OCI deployment via GitHub Actions
- **Secrets**: Do NOT commit real IPs or keys. Use `.env` or GitHub Secrets
- **Documentation**: See `.agent/infrastructure.md` for server paths and `.agent/development.md` for architecture details
