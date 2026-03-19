import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from settings import CONFIG, SERVERS_CONFIG, DEFAULT_SERVER
from utils.permissions import check_role
from utils.plugin_manager import (
    list_plugins, format_plugins_list, get_plugins_dir,
    update_all_plugins, check_all_plugin_updates
)


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

    def get_plugins_config(self, server_id: str) -> dict:
        """サーバーのプラグイン設定を取得"""
        return CONFIG.get("plugins", {}).get(server_id, {})

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
            logging.exception(f"User {interaction.user} ({interaction.user.id}) /plugins server={server} error: {e}")
            await interaction.followup.send(f"エラーが発生しました: {e}", silent=True)

    @app_commands.command(name="update_plugins", description="設定されたプラグインを最新版に更新します")
    @app_commands.describe(server="対象サーバー")
    @app_commands.choices(server=SERVER_CHOICES)
    async def update_plugins(
        self,
        interaction: discord.Interaction,
        server: str = DEFAULT_SERVER
    ):
        """設定されたプラグインを最新版に更新"""
        if not check_role(interaction, 'backup'):  # admin権限を要求
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        server_instance = self.server_manager.get_server(server)
        if not server_instance:
            return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

        plugins_config = self.get_plugins_config(server)
        if not plugins_config:
            return await interaction.response.send_message(
                f"[{server_instance.name}] プラグイン設定が見つかりません。config.jsonに設定してください。",
                ephemeral=True
            )

        await interaction.response.send_message(
            f"🔄 [{server_instance.name}] プラグイン更新を開始します...",
            silent=True
        )

        server_name = server_instance.name
        plugins_dir = get_plugins_dir(server_instance.cwd)
        loop = asyncio.get_event_loop()

        try:
            # 1. 更新チェック
            await interaction.edit_original_response(
                content=f"📋 [{server_name}] 更新をチェック中..."
            )

            check_results = await loop.run_in_executor(
                None, check_all_plugin_updates, plugins_dir, plugins_config
            )

            # 更新が必要なプラグインを抽出
            plugins_to_update = {
                r["plugin_name"]: plugins_config[r["plugin_name"]]
                for r in check_results if r["has_update"] and not r["error"]
            }

            if not plugins_to_update:
                await interaction.edit_original_response(
                    content=f"✅ [{server_name}] すべてのプラグインが最新です。更新は不要です。"
                )
                return

            # 2. サーバー停止（更新がある場合のみ）
            was_running = False
            if self.server_manager.is_running(server):
                was_running = True
                await interaction.edit_original_response(
                    content=f"🔄 [{server_name}] サーバーを停止中..."
                )
                await self.server_manager.stop_server(server)
                await self.server_manager.wait_for_exit(server)

            # 3. 更新が必要なプラグインのみダウンロード
            await interaction.edit_original_response(
                content=f"⬇️ [{server_name}] {len(plugins_to_update)}件のプラグインを更新中..."
            )

            results = await loop.run_in_executor(
                None, update_all_plugins, plugins_dir, plugins_to_update
            )

            # 4. 結果表示
            success_count = sum(1 for r in results if r["success"])
            fail_count = len(results) - success_count
            skipped_count = len(plugins_config) - len(plugins_to_update)

            output_lines = [f"**[{server_name}] プラグイン更新結果**\n```"]
            for r in results:
                status = "✅" if r["success"] else "❌"
                output_lines.append(f"{status} {r['plugin_name']}: {r['message']}")
            if skipped_count > 0:
                output_lines.append(f"⏭️ {skipped_count}件はスキップ (最新)")
            output_lines.append("```")
            output_lines.append(f"\n更新: {success_count} / 失敗: {fail_count} / スキップ: {skipped_count}")

            # 5. サーバー再起動
            if was_running:
                output_lines.append(f"\n🚀 [{server_name}] サーバーを再起動中...")
                await interaction.edit_original_response(content="\n".join(output_lines))
                await self.server_manager.start_server(server)
                output_lines[-1] = f"✅ [{server_name}] サーバーを再起動しました。"

            await interaction.edit_original_response(content="\n".join(output_lines))
            logging.info(f"User {interaction.user} ({interaction.user.id}) executed /update_plugins server={server}")

        except Exception as e:
            logging.exception(f"User {interaction.user} ({interaction.user.id}) /update_plugins server={server} error: {e}")
            await interaction.edit_original_response(
                content=f"❌ [{server_name}] プラグイン更新中にエラーが発生しました: {e}"
            )

    @app_commands.command(name="check_updates", description="プラグインの更新状況を確認します（サーバー停止不要）")
    @app_commands.describe(server="対象サーバー")
    @app_commands.choices(server=SERVER_CHOICES)
    async def check_updates(
        self,
        interaction: discord.Interaction,
        server: str = DEFAULT_SERVER
    ):
        """プラグインの更新状況を確認（サーバー停止不要）"""
        if not check_role(interaction, 'status'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        server_instance = self.server_manager.get_server(server)
        if not server_instance:
            return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

        plugins_config = self.get_plugins_config(server)
        if not plugins_config:
            return await interaction.response.send_message(
                f"[{server_instance.name}] プラグイン設定が見つかりません。config.jsonに設定してください。",
                ephemeral=True
            )

        await interaction.response.send_message(
            f"📋 [{server_instance.name}] プラグイン更新をチェック中...",
            silent=True
        )

        server_name = server_instance.name
        plugins_dir = get_plugins_dir(server_instance.cwd)
        loop = asyncio.get_event_loop()

        try:
            results = await loop.run_in_executor(
                None, check_all_plugin_updates, plugins_dir, plugins_config
            )

            update_count = sum(1 for r in results if r["has_update"])
            error_count = sum(1 for r in results if r["error"])

            output_lines = [f"**📋 [{server_name}] プラグイン更新チェック**\n```"]
            for r in results:
                name = r['plugin_name']
                installed = r['installed_version']
                latest = r['latest_version']

                if r["error"]:
                    output_lines.append(f"⚠️ {name}: {r['error']}")
                elif r["has_update"]:
                    output_lines.append(f"⬆️ {name}: {installed} → {latest}")
                else:
                    output_lines.append(f"✅ {name}: {installed} (最新)")

            output_lines.append("```")

            if update_count > 0:
                output_lines.append(f"\n更新が必要: **{update_count}件** `/update_plugins` で更新できます")
            else:
                output_lines.append("\n✅ すべてのプラグインが最新です")

            if error_count > 0:
                output_lines.append(f"\n⚠️ チェックできなかったプラグイン: {error_count}件")

            await interaction.edit_original_response(content="\n".join(output_lines))
            logging.info(f"User {interaction.user} ({interaction.user.id}) executed /check_updates server={server}")

        except Exception as e:
            logging.exception(f"User {interaction.user} ({interaction.user.id}) /check_updates server={server} error: {e}")
            await interaction.edit_original_response(
                content=f"❌ [{server_name}] 更新チェック中にエラーが発生しました: {e}"
            )


async def setup(bot):
    await bot.add_cog(PluginSystem(bot, bot.server_manager))
