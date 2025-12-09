import os
import shutil
import zipfile
import asyncio
import logging
import discord
from discord import app_commands
from datetime import datetime
from discord.ext import commands, tasks
from settings import CONFIG, CHANNEL_ID, check_role
from utils.notion_api import upload_to_notion, register_to_database, get_backups_list, download_file

class BackupSystem(commands.Cog):
    def __init__(self, bot, server_manager):
        self.bot = bot
        self.server_manager = server_manager
        self.scheduler.start()

    def cog_unload(self):
        self.scheduler.cancel()

    async def perform_backup(self, channel):
        try:
            # 1. サーバー停止と待機
            was_running = False
            if self.server_manager.is_running():
                was_running = True
                if channel: await channel.send("サーバーを停止してデータを保存します...", silent=True)
                await self.server_manager.stop_server()
                await self.server_manager.wait_for_exit()

            # 2. 圧縮 (ZIP)
            if channel: await channel.send("ワールドデータを圧縮中...", silent=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            zip_name = f"backup_{timestamp}.zip"

            original_cwd = os.getcwd()
            try:
                os.chdir("/opt/minecraft")
                with zipfile.ZipFile(zip_name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for d in CONFIG["backup"]["target_dirs"]:
                        if os.path.exists(d):
                            if os.path.isdir(d):
                                for root, dirs, files in os.walk(d):
                                    for file in files:
                                        file_path = os.path.join(root, file)
                                        # arcname is relative to /opt/minecraft (cwd)
                                        zipf.write(file_path, os.path.relpath(file_path, "."))
                            else:
                                zipf.write(d, os.path.basename(d))

                size_mb = os.path.getsize(zip_name) / (1024 * 1024)

                # 3. Notion Upload
                if channel: await channel.send(f"Notionへアップロード中... ({size_mb:.1f}MB)", silent=True)

                # 非同期実行のためにExecutorを使用
                loop = asyncio.get_event_loop()
                file_id = await loop.run_in_executor(None, upload_to_notion, zip_name)

                # DB登録
                await loop.run_in_executor(None, register_to_database, file_id, zip_name, f"{size_mb:.1f}MB")

                if channel: await channel.send("✅ バックアップ完了！", silent=True)

                # 4. 一時ファイル削除
                os.remove(zip_name)

            finally:
                os.chdir(original_cwd)

            # 5. 再起動
            if was_running:
                if channel: await channel.send("サーバーを再起動します。", silent=True)
                await self.server_manager.start_server()

        except Exception as e:
            if channel: await channel.send(f"❌ バックアップエラー: {str(e)}", silent=True)
            logging.error(e)

    @app_commands.command(name="backup", description="手動バックアップを実行します")
    async def backup(self, interaction: discord.Interaction):
        if not check_role(interaction, 'backup'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)
        
        await interaction.response.send_message("バックアップ処理を開始します...", silent=True)
        await self.perform_backup(interaction.channel)

    @app_commands.command(name="backups", description="Notionにある最新のバックアップリストを表示します")
    async def backups(self, interaction: discord.Interaction):
        """Notionにある最新のバックアップリストを表示"""
        if not check_role(interaction, 'backup'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        await interaction.response.send_message("Notionからバックアップリストを取得中...", silent=True)
        msg = await interaction.original_response()

        try:
            loop = asyncio.get_event_loop()
            backup_list = await loop.run_in_executor(None, get_backups_list, 10)

            if not backup_list:
                await msg.edit(content="バックアップが見つかりませんでした。")
                return

            text = "**利用可能なバックアップ (最新10件):**\n```\n"
            for i, bk in enumerate(backup_list):
                text += f"[{i}] {bk['date']} - {bk['filename']}\n"
            text += "```\n**現在のワールドデータは削除されます！**"

            await msg.edit(content=text)

        except Exception as e:
            await msg.edit(content=f"エラーが発生しました: {e}")

    @app_commands.command(name="rollback", description="指定したバックアップにロールバックします")
    @app_commands.describe(index="バックアップのインデックス番号")
    async def rollback(self, interaction: discord.Interaction, index: int):
        """指定した番号のバックアップにロールバックする"""
        if not check_role(interaction, 'backup'):
            return await interaction.response.send_message("この操作は管理者（Admin）のみ可能です。", ephemeral=True)

        await interaction.response.send_message("🔄 ロールバック準備中... Notion情報を取得しています。", silent=True)
        status_msg = await interaction.original_response()

        try:
            loop = asyncio.get_event_loop()
            backup_list = await loop.run_in_executor(None, get_backups_list, 10)

            if index < 0 or index >= len(backup_list):
                return await status_msg.edit(content="❌ 指定された番号のバックアップは存在しません。")

            target_backup = backup_list[index]
            filename = target_backup['filename']
            download_url = target_backup.get('url')
            if not download_url:
                return await status_msg.edit(content="❌ バックアップのダウンロード URL が見つかりません。")

            # --- 1. サーバー停止 ---
            if self.server_manager.is_running():
                await status_msg.edit(content=f"⏹️ サーバーを停止しています...")
                await self.server_manager.stop_server()
                await self.server_manager.wait_for_exit()

            # --- 2. バックアップのダウンロード ---
            await status_msg.edit(content=f"⬇️ ダウンロード中: {filename} ...")
            
            original_cwd = os.getcwd()
            try:
                os.chdir("/opt/minecraft")
                try:
                    await loop.run_in_executor(None, download_file, download_url, filename)
                except Exception as e:
                    logging.error(f"Download failed: {e}")
                    return await status_msg.edit(content=f"❌ ダウンロードに失敗しました: {e}")

                # --- 3. 既存データの削除 ---
                await status_msg.edit(content="🗑️ 既存のワールドデータを削除中...")

                for d in CONFIG["backup"]["target_dirs"]:
                    if os.path.exists(d):
                        try:
                            if os.path.isdir(d):
                                shutil.rmtree(d)
                            else:
                                os.remove(d)
                        except Exception as e:
                            logging.error(f"Failed to remove {d}: {e}")
                            return await status_msg.edit(content=f"既存データの削除中にエラーが発生しました: {e}")

                # --- 4. 解凍 ---
                await status_msg.edit(content="📦 バックアップを展開中...")

                if filename.endswith(".zip"):
                    def unzip_safe():
                        with zipfile.ZipFile(filename, 'r') as zipf:
                            zipf.extractall()
                    await loop.run_in_executor(None, unzip_safe)
                elif filename.endswith("tar.gz"):
                    def untar_safe():
                        with tarfile.open(filename, "r:gz") as tar:
                            tar.extractall()
                    await loop.run_in_executor(None, untar_safe)
                
                os.remove(filename)

            finally:
                os.chdir(original_cwd)

            # --- 5. サーバー再起動 ---
            await status_msg.edit(content="✅ ロールバック完了！サーバーを起動します。")
            await self.server_manager.start_server()

        except Exception as e:
            import traceback
            traceback.print_exc()
            await status_msg.edit(content=f"❌ 重大なエラーが発生しました: {e}")

    @tasks.loop(minutes=1)
    async def scheduler(self):
        now = datetime.now().strftime("%H:%M")
        if 'backup' in CONFIG and 'schedule_time' in CONFIG['backup']:
            if now == CONFIG['backup']['schedule_time']:
                channel = self.bot.get_channel(CHANNEL_ID)
                await self.perform_backup(channel)
    
    @scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(BackupSystem(bot, bot.server_manager))
