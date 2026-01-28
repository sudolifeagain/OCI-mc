This command helps you deploy the bot to the OCI instance.

1. Check current branch is clean: `git status`
2. Push to master to trigger GitHub Actions:
   ```bash
   git push origin master
   ```
3. Monitor the Action tab in GitHub or check Discord for the success notification.

Context:
- Workflow file: `.github/workflows/deploy.yml`
- The deploy process uses `rsync` to sync files and restarts the `discord-bot` service.
