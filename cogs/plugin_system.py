import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from settings import SERVERS_CONFIG, DEFAULT_SERVER
from utils.permissions import check_role
from utils.plugin_manager import list_plugins, format_plugins_list, get_plugins_dir


def get_server_choices():
    """Discord用のサーバー選択肢を生成する"""
    choices = []
    for server_id, config in SERVERS_CONFIG.items():
        choices.append(app_commands.Choice(name=config.get('name', server_id), value=server_id))
    return choices


SERVER_CHOICES = get_server_choices()


class PluginSystem(commands.Cog):
    def __init__(self, bot, server_manager):
        self.bot = bot
        self.server_manager = server_manager

    @app_commands.command(name="plugins", description="インストールされているプラグイン一覧を表示します")
    @app_commands.describe(
        server="対象サーバー",
        detailed="詳細表示（ファイル名・サイズなど）"
    )
    @app_commands.choices(server=SERVER_CHOICES)
    async def plugins(
        self, 
        interaction: discord.Interaction, 
        server: str = DEFAULT_SERVER,
        detailed: bool = False
    ):
        """インストール済みプラグインの一覧を表示"""
        if not check_role(interaction, 'status'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        server_instance = self.server_manager.get_server(server)
        if not server_instance:
            return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

        await interaction.response.send_message("プラグイン一覧を取得中...", silent=True)
        
        try:
            plugins_dir = get_plugins_dir(server_instance.cwd)
            
            loop = asyncio.get_event_loop()
            plugins = await loop.run_in_executor(None, list_plugins, plugins_dir)
            
            if not plugins:
                await interaction.edit_original_response(
                    content=f"[{server_instance.name}] プラグインが見つかりませんでした。\nパス: `{plugins_dir}`"
                )
                return

            # フォーマット
            output = f"**[{server_instance.name}]** {format_plugins_list(plugins, detailed=detailed)}"
            
            # Discordメッセージ制限対応
            if len(output) > 2000:
                # 長すぎる場合は分割
                chunks = [output[i:i+1900] for i in range(0, len(output), 1900)]
                await interaction.edit_original_response(content=chunks[0])
                for chunk in chunks[1:]:
                    await interaction.followup.send(chunk, silent=True)
            else:
                await interaction.edit_original_response(content=output)

            logging.info(f"User {interaction.user} ({interaction.user.id}) executed /plugins server={server}")

        except Exception as e:
            logging.error(f"Plugin list error: {e}")
            await interaction.followup.send(f"エラーが発生しました: {e}", silent=True)


async def setup(bot):
    await bot.add_cog(PluginSystem(bot, bot.server_manager))
