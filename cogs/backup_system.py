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

    MC_DIR = "/opt/minecraft"

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
            zip_path = os.path.join(self.MC_DIR, zip_name)

            def create_zip():
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for d in CONFIG["backup"]["target_dirs"]:
                        # target_dir is relative name like "world"
                        full_path = os.path.join(self.MC_DIR, d)
                        if os.path.exists(full_path):
                            if os.path.isdir(full_path):
                                for root, dirs, files in os.walk(full_path):
                                    for file in files:
                                        file_path = os.path.join(root, file)
                                        # arcname should be relative to MC_DIR
                                        # e.g. /opt/minecraft/world/level.dat -> world/level.dat
                                        arcname = os.path.relpath(file_path, self.MC_DIR)
                                        zipf.write(file_path, arcname)
                            else:
                                zipf.write(full_path, os.path.basename(full_path))
            
            # Run zip creation in executor to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, create_zip)

            size_mb = os.path.getsize(zip_path) / (1024 * 1024)

            # 3. Notion Upload
            if channel: await channel.send(f"Notionへアップロード中... ({size_mb:.1f}MB)", silent=True)

            # 非同期実行のためにExecutorを使用
            # Notionの拡張子制限を回避するために .pdf としてアップロードする (中身はzip)
            upload_name = zip_name.replace(".zip", ".pdf")
            file_id = await loop.run_in_executor(None, upload_to_notion, zip_path, upload_name, "application/pdf")

            # DB登録
            await loop.run_in_executor(None, register_to_database, file_id, zip_name, size_mb)

            if channel: await channel.send("✅ バックアップ完了！", silent=True)

            # 4. 一時ファイル削除
            os.remove(zip_path)

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
        
        # ログメッセージを蓄積する変数
        log_content = "🔄 ロールバック準備中... Notion情報を取得しています。\n"

        async def update_status(text):
            nonlocal log_content
            timestamp = datetime.now().strftime("%H:%M:%S")
            new_line = f"[{timestamp}] {text}\n"
            log_content += new_line
            # Discordの文字数制限(2000文字)対策: 最後から1900文字程度に切り詰める
            if len(log_content) > 1900:
                log_content = "..." + log_content[-1900:]
            await status_msg.edit(content=f"```\n{log_content}\n```")

        try:
            loop = asyncio.get_event_loop()
            backup_list = await loop.run_in_executor(None, get_backups_list, 10)

            if index < 0 or index >= len(backup_list):
                await update_status("❌ 指定された番号のバックアップは存在しません。")
                return

            target_backup = backup_list[index]
            filename = target_backup['filename']
            download_url = target_backup.get('url')
            if not download_url:
                await update_status("❌ バックアップのダウンロード URL が見つかりません。")
                return

            # --- 1. サーバー停止 ---
            if self.server_manager.is_running():
                await update_status(f"⏹️ サーバーを停止しています...")
                await self.server_manager.stop_server()
                await self.server_manager.wait_for_exit()
                await update_status("サーバー停止を確認しました。")

            # --- 2. バックアップのダウンロード ---
            await update_status(f"⬇️ ダウンロード中: {filename} ...")
            
            # Use absolute path for saving
            save_path = os.path.join(self.MC_DIR, filename)
            logging.info(f"[Rollback] Downloading backup from {download_url} to {save_path}")

            try:
                await loop.run_in_executor(None, download_file, download_url, save_path)
                if os.path.exists(save_path):
                    size_mb = os.path.getsize(save_path) / (1024 * 1024)
                    logging.info(f"[Rollback] Download complete. File size: {size_mb:.2f}MB")
                    await update_status(f"ダウンロード完了: {size_mb:.2f}MB")
                else:
                    logging.error(f"[Rollback] File not found after download: {save_path}")
                    await update_status("❌ ダウンロード後にファイルが見つかりません。")
                    return

            except Exception as e:
                logging.error(f"Download failed: {e}")
                await update_status(f"❌ ダウンロードに失敗しました: {e}")
                return

            try:
                # --- 3. 既存データの削除 ---
                await update_status("🗑️ 既存のワールドデータを削除中...")
                logging.info("[Rollback] Removing existing world data...")

                for d in CONFIG["backup"]["target_dirs"]:
                    full_target_path = os.path.join(self.MC_DIR, d)
                    if os.path.exists(full_target_path):
                        logging.info(f"[Rollback] Deleting: {full_target_path}")
                        try:
                            if os.path.isdir(full_target_path):
                                shutil.rmtree(full_target_path)
                            else:
                                os.remove(full_target_path)
                        except Exception as e:
                            logging.error(f"Failed to remove {full_target_path}: {e}")
                            await update_status(f"既存データの削除中にエラーが発生しました: {e}")
                            return
                    else:
                        logging.info(f"[Rollback] Target not found (skipping): {full_target_path}")

                # --- 4. 解凍 ---
                await update_status(f"📦 バックアップを展開中... (File: {filename})")
                logging.info(f"[Rollback] Extracting {save_path} to {self.MC_DIR}")

                # --- 4. 解凍 ---
                await update_status(f"📦 バックアップを展開中... (File: {filename})")
                logging.info(f"[Rollback] Extracting {save_path} to {self.MC_DIR}")

                import tarfile  # Ensure tarfile is imported

                is_zip = zipfile.is_zipfile(save_path)
                is_tar = tarfile.is_tarfile(save_path)

                if is_zip:
                    def unzip_safe():
                        with zipfile.ZipFile(save_path, 'r') as zipf:
                            # extractall with path argument
                            zipf.extractall(path=self.MC_DIR)
                            logging.info(f"[Rollback] Unzipped {len(zipf.namelist())} files.")
                    await loop.run_in_executor(None, unzip_safe)
                    await update_status("ZIP展開完了。")
                    
                elif is_tar:
                    def untar_safe():
                        with tarfile.open(save_path, "r") as tar:
                            tar.extractall(path=self.MC_DIR)
                            logging.info(f"[Rollback] Untarred files.")
                    await loop.run_in_executor(None, untar_safe)
                    await update_status("TAR展開完了。")
                
                else:
                    error_msg = f"❌ 未知のファイル形式です (Extensions check skipped, Magic byte mismatch). Filename: {filename}"
                    logging.error(f"[Rollback] {error_msg}")
                    await update_status(error_msg)
                    return # Exit if extraction didn't happen
                
                logging.info("[Rollback] Extraction complete. Cleaning up archive file.")
                if os.path.exists(save_path):
                    os.remove(save_path)
                
                logging.info("[Rollback] Rollback process finished successfully.")
                await update_status("展開処理終了。")

            except Exception as e:
                logging.error(f"Rollback file operation failed: {e}")
                raise e

            # --- 5. サーバー再起動 ---
            await update_status("✅ ロールバック完了！サーバーを起動します。")
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
