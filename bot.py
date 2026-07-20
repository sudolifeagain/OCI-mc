import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from settings import DISCORD_TOKEN, NOTION_TOKEN, SERVERS_CONFIG, SERVER_RUNTIME_DIR
from utils.discord_security import is_allowed_command_context
from utils.server_manager import MultiServerManager


class SecureCommandTree(app_commands.CommandTree):
    """全スラッシュコマンドを許可されたサーバーとチャンネルに限定する。"""

    def __init__(self, client):
        super().__init__(
            client,
            allowed_contexts=app_commands.AppCommandContext(
                guild=True,
                dm_channel=False,
                private_channel=False,
            ),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        command_name = getattr(interaction.command, "name", "")
        return is_allowed_command_context(
            interaction,
            shell=command_name == "shell",
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = "このサーバーまたはチャンネルではコマンドを実行できない。"
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        logging.error(
            "Application command failed",
            exc_info=(type(error), error, error.__traceback__),
        )
        message = "コマンドの実行中にエラーが発生した。"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


# Bot Class Definition
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # Prefix commands are disabled as per user request (Slash commands only)
        super().__init__(command_prefix=[], intents=intents, tree_cls=SecureCommandTree)
        self.server_manager = MultiServerManager(SERVERS_CONFIG, SERVER_RUNTIME_DIR)
        self._auto_start_done = False  # 初回のみ実行フラグ

    async def setup_hook(self):
        # Load Extensions
        initial_extensions = [
            'cogs.basic_control',
            'cogs.backup_system',
            'cogs.plugin_system',
            'cogs.status_display',
            'cogs.permission_system',
            'cogs.system_monitor',
            'cogs.claude_manager',
            'cogs.reaction_roles'
        ]

        for extension in initial_extensions:
            await self.load_extension(extension)

        # Sync Slash Commands
        # Note: In production, it's better to sync to a specific guild for faster updates during dev,
        # or use a command to sync globally. For now, we sync globally on startup.
        print("Syncing commands...")
        await self.tree.sync()
        print("Commands synced.")

        # Notion API の data_source_id を事前解決（設定不備の早期検出）
        if NOTION_TOKEN:
            try:
                from utils.notion_api import get_data_source_id
                loop = asyncio.get_event_loop()
                ds_id = await loop.run_in_executor(None, get_data_source_id)
                logging.info(f"Notion API: data_source_id resolved ({ds_id[:8]}...)")
            except Exception as e:
                logging.warning(f"Notion API: data_source_id resolution failed: {e}")

    async def on_ready(self):
        print(f'Logged in as {self.user}')

        # サーバー自動起動（初回のみ）
        if not self._auto_start_done:
            self._auto_start_done = True
            await self._auto_start_servers()

    async def _auto_start_servers(self):
        """設定または永続化された稼働希望状態からサーバーを復元する。"""
        for server_id in self.server_manager.get_desired_servers():
            if not self.server_manager.is_running(server_id):
                logging.info(f"Auto-starting server '{server_id}'...")
                await self._start_and_log(server_id)
            else:
                logging.info(f"Server '{server_id}' is already running")

    async def _start_and_log(self, server_id: str):
        """サーバーを起動してログ出力"""
        success = await self.server_manager.start_server(server_id)
        if success:
            logging.info(f"Server '{server_id}' auto-started successfully")
        else:
            logging.error(f"Failed to auto-start server '{server_id}'")

    async def on_message(self, message):
        # Disable prefix commands by not calling process_commands
        pass

# Bot Setup
bot = MyBot()

async def main():
    async with bot:
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
