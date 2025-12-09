import os
import shutil
import tarfile
import asyncio
import logging
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
                if channel: await channel.send("サーバーを停止してデータを保存します...")
                await self.server_manager.stop_server()
                await self.server_manager.wait_for_exit()

            # 2. 圧縮
            if channel: await channel.send("ワールドデータを圧縮中...")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            zip_name = f"backup_{timestamp}.tar.gz"

            # 実行パスは /opt/minecraft を想定
            # Note: 実際にcwdを変更するのは非同期環境で副作用がある場合があるが、
            # 元のコードを踏襲。ただし server_manager は cwd を指定して起動している。
            # ここでは os.chdir を使うとプロセス全体のパスが変わることに注意。
            # 安全のため try-finally で戻すか、絶対パスを使用するのが良いが、
            # 元コードに合わせて /opt/minecraft に移動する。
            original_cwd = os.getcwd()
            try:
                os.chdir("/opt/minecraft")
                with tarfile.open(zip_name, "w:gz") as tar:
                    for d in CONFIG["backup"]["target_dirs"]:
                        if os.path.exists(d):
                            tar.add(d)

                size_mb = os.path.getsize(zip_name) / (1024 * 1024)

                # 3. Notion Upload
                if channel: await channel.send(f"Notionへアップロード中... ({size_mb:.1f}MB)")

                # 非同期実行のためにExecutorを使用
                loop = asyncio.get_event_loop()
                file_id = await loop.run_in_executor(None, upload_to_notion, zip_name)

                # DB登録
                await loop.run_in_executor(None, register_to_database, file_id, zip_name, f"{size_mb:.1f}MB")

                if channel: await channel.send("✅ バックアップ完了！")

                # 4. 一時ファイル削除
                os.remove(zip_name)

            finally:
                os.chdir(original_cwd)

            # 5. 再起動
            if was_running:
                if channel: await channel.send("サーバーを再起動します。")
                await self.server_manager.start_server()

        except Exception as e:
            if channel: await channel.send(f"❌ バックアップエラー: {str(e)}")
            logging.error(e)

    @commands.command()
    async def backup(self, ctx):
        if not check_role(ctx, 'backup'): return await ctx.send("権限がありません。")
        await ctx.send("バックアップ処理を開始します...")
        await self.perform_backup(ctx.channel)

    @commands.command()
    async def backups(self, ctx):
        """Notionにある最新のバックアップリストを表示"""
        if not check_role(ctx, 'backup'): return await ctx.send("権限がありません。")

        msg = await ctx.send("Notionからバックアップリストを取得中...")

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

    @commands.command()
    async def rollback(self, ctx, index: int):
        """指定した番号のバックアップにロールバックする"""
        if not check_role(ctx, 'backup'):
            return await ctx.send("この操作は管理者（Admin）のみ可能です。") # 元のコードのコメントに合わせていますが、設定次第

        status_msg = await ctx.send("🔄 ロールバック準備中... Notion情報を取得しています。")

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

                if filename.endswith("tar.gz"):
                    def untar_safe():
                        with tarfile.open(filename, "r:gz") as tar:
                            tar.extractall()
                    await loop.run_in_executor(None, untar_safe)
                else:
                    pass

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
