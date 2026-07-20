import os
import json
import hashlib
import tempfile
import shutil
import zipfile
import asyncio
import logging
import stat
import tarfile
import discord
from discord import app_commands
from datetime import datetime
from discord.ext import commands, tasks
from settings import CONFIG, CHANNEL_ID, SERVER_IDS, SERVERS_CONFIG, DEFAULT_SERVER
from utils.permissions import check_role
from utils.notion_api import upload_to_notion, register_to_database, get_backups_list, download_file
from utils.discord_security import escape_discord_code_block

FINGERPRINT_FILE = ".backup_fingerprints.json"
MAX_ARCHIVE_MEMBERS = 1_000_000


def _validate_archive_path(base_dir: str, name: str, allowed_roots: set[str]) -> None:
    """展開先とバックアップ対象外への書き込みを拒否する。"""
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError(f"絶対パスを含むアーカイブは拒否する: {name}")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or ".." in parts or parts[0] not in allowed_roots:
        raise ValueError(f"許可されていないアーカイブパスである: {name}")

    base_real = os.path.realpath(base_dir)
    target_real = os.path.realpath(os.path.join(base_real, *parts))
    if os.path.commonpath((base_real, target_real)) != base_real:
        raise ValueError(f"展開先の外部を参照するアーカイブパスである: {name}")


def _ensure_archive_fits(base_dir: str, member_count: int, expanded_size: int) -> None:
    if member_count > MAX_ARCHIVE_MEMBERS:
        raise ValueError("アーカイブのファイル数が上限を超えている")
    free_bytes = shutil.disk_usage(base_dir).free
    if expanded_size > int(free_bytes * 0.9):
        raise ValueError("アーカイブ展開後のサイズが空き容量の90%を超える")


def _extract_zip_safely(path: str, base_dir: str, allowed_roots: set[str]) -> int:
    with zipfile.ZipFile(path, "r") as archive:
        members = archive.infolist()
        _ensure_archive_fits(
            base_dir,
            len(members),
            sum(member.file_size for member in members),
        )
        for member in members:
            _validate_archive_path(base_dir, member.filename, allowed_roots)
            file_type = (member.external_attr >> 16) & 0o170000
            if file_type == stat.S_IFLNK:
                raise ValueError(f"シンボリックリンクを含むZIPは拒否する: {member.filename}")
        archive.extractall(path=base_dir)
        return len(members)


def _extract_tar_safely(path: str, base_dir: str, allowed_roots: set[str]) -> int:
    with tarfile.open(path, "r") as archive:
        members = archive.getmembers()
        _ensure_archive_fits(
            base_dir,
            len(members),
            sum(member.size for member in members),
        )
        for member in members:
            _validate_archive_path(base_dir, member.name, allowed_roots)
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"リンクまたは特殊ファイルを含むTARは拒否する: {member.name}")
        archive.extractall(path=base_dir, members=members)
        return len(members)


def _remove_path(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


def _replace_backup_roots(stage_dir: str, base_dir: str, roots: list[str]) -> int:
    """展開済みデータを同一ファイルシステム上で置換し、失敗時は元へ戻す。"""
    previous_dir = os.path.join(stage_dir, ".previous")
    moved: list[tuple[str, str | None, bool]] = []
    replaced = 0
    try:
        for root in roots:
            source = os.path.join(stage_dir, root)
            if not os.path.lexists(source):
                continue
            target = os.path.join(base_dir, root)
            previous = None
            if os.path.lexists(target):
                previous = os.path.join(previous_dir, root)
                os.makedirs(os.path.dirname(previous), exist_ok=True)
                os.replace(target, previous)
            moved.append((target, previous, False))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(source, target)
            moved[-1] = (target, previous, True)
            replaced += 1
        if replaced == 0:
            raise ValueError("バックアップ対象データを含まないアーカイブである")
        return replaced
    except Exception:
        for target, previous, installed in reversed(moved):
            if installed:
                _remove_path(target)
            if previous and os.path.lexists(previous):
                os.makedirs(os.path.dirname(target), exist_ok=True)
                os.replace(previous, target)
        raise


def get_server_choices():
    """Discord用のサーバー選択肢を生成する"""
    choices = []
    for server_id, config in SERVERS_CONFIG.items():
        choices.append(app_commands.Choice(name=config.get('name', server_id), value=server_id))
    return choices


SERVER_CHOICES = get_server_choices()


class BackupSystem(commands.Cog):
    def __init__(self, bot, server_manager):
        self.bot = bot
        self.server_manager = server_manager
        self.scheduler.start()

    def cog_unload(self):
        self.scheduler.cancel()

    def get_backup_dirs(self, server_id: str) -> list[str]:
        """指定されたサーバーのバックアップ対象ディレクトリを取得する"""
        backup_config = CONFIG.get("backup", {}).get("target_dirs", {})

        # 新形式: サーバーごとにディレクトリが分かれている
        if isinstance(backup_config, dict):
            return backup_config.get(server_id, ["world"])

        # 旧形式: 単一リスト（後方互換性）
        return backup_config if isinstance(backup_config, list) else ["world"]

    def get_server_cwd(self, server_id: str) -> str:
        """指定されたサーバーの作業ディレクトリを取得する"""
        server = self.server_manager.get_server(server_id)
        if server:
            return server.cwd
        return "/opt/minecraft"

    def _load_fingerprints(self) -> dict:
        """保存済みフィンガープリントを読み込む"""
        try:
            with open(FINGERPRINT_FILE, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_fingerprints(self, data: dict) -> None:
        """フィンガープリントをファイルに保存する"""
        with open(FINGERPRINT_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    def _compute_fingerprint(self, base_dir: str, target_dirs: list[str]) -> str:
        """パス・サイズ・更新時刻からバックアップ対象のフィンガープリントを計算する。"""
        entries = []
        for d in sorted(target_dirs):
            full_path = os.path.join(base_dir, d)
            if not os.path.exists(full_path):
                continue
            if os.path.isdir(full_path):
                for root, dirs, files in os.walk(full_path):
                    dirs.sort()
                    for fname in sorted(files):
                        file_path = os.path.join(root, fname)
                        rel_path = os.path.relpath(file_path, base_dir)
                        stat = os.stat(file_path)
                        entries.append(f"{rel_path}|{stat.st_size}|{stat.st_mtime_ns}")
            else:
                stat = os.stat(full_path)
                entries.append(f"{d}|{stat.st_size}|{stat.st_mtime_ns}")
        return hashlib.sha256("\n".join(entries).encode()).hexdigest()

    def _has_changes(self, server_id: str, base_dir: str, target_dirs: list[str]) -> bool:
        """前回バックアップから変更があるか判定する"""
        current_fp = self._compute_fingerprint(base_dir, target_dirs)
        saved = self._load_fingerprints()
        prev_fp = saved.get(server_id)
        if prev_fp is None:
            return True
        return current_fp != prev_fp

    def _update_fingerprint(self, server_id: str, base_dir: str, target_dirs: list[str]) -> None:
        """フィンガープリントを計算して保存する。サーバー停止後に呼ぶこと"""
        fp = self._compute_fingerprint(base_dir, target_dirs)
        saved = self._load_fingerprints()
        saved[server_id] = fp
        self._save_fingerprints(saved)

    async def perform_backup(self, channel, server_id: str = None, *, force: bool = False):
        """バックアップを実行する"""
        # サーバー指定がない場合は全サーバーをバックアップ
        if server_id is None:
            servers_to_backup = SERVER_IDS
        else:
            servers_to_backup = [server_id]

        for srv_id in servers_to_backup:
            await self._backup_single_server(channel, srv_id, force=force)

    async def _backup_single_server(self, channel, server_id: str, *, force: bool = False):
        """単一サーバーのバックアップを実行する"""
        server_instance = self.server_manager.get_server(server_id)
        if not server_instance:
            if channel:
                await channel.send(f"サーバー '{server_id}' が見つかりません。", silent=True)
            return

        server_name = server_instance.name
        mc_dir = server_instance.cwd
        maintenance_started = False
        was_running = False
        zip_path = None

        try:
            # 1. バックアップ対象の確認
            backup_dirs = self.get_backup_dirs(server_id)
            existing_dirs = []
            for d in backup_dirs:
                full_path = os.path.join(mc_dir, d)
                if os.path.exists(full_path):
                    existing_dirs.append(d)

            if not existing_dirs:
                if channel:
                    await channel.send(f"[{server_name}] バックアップ対象のデータが存在しません（{', '.join(backup_dirs)}）", silent=True)
                return

            # 2. 変更検出（定期バックアップのみ）
            loop = asyncio.get_event_loop()
            if not force:
                changed = await loop.run_in_executor(
                    None, self._has_changes, server_id, mc_dir, existing_dirs
                )
                if not changed:
                    if channel:
                        await channel.send(
                            f"[{server_name}] 前回のバックアップから変更なし。スキップ。",
                            silent=True,
                        )
                    return

            maintenance_started = self.server_manager.begin_maintenance(server_id)
            if not maintenance_started:
                if channel:
                    await channel.send(f"[{server_name}] 別のメンテナンス処理を実行中である。", silent=True)
                return

            # 3. サーバー停止と待機
            if self.server_manager.is_running(server_id):
                was_running = True
                if channel:
                    await channel.send(f"[{server_name}] サーバーを停止してデータを保存します...", silent=True)

                async def backup_progress(msg: str) -> None:
                    if channel:
                        try:
                            await channel.send(f"[{server_name}] {msg}", silent=True)
                        except discord.HTTPException as e:
                            logging.warning(f"[Backup] Failed to send progress: {e}")

                stopped = await self.server_manager.stop_server(
                    server_id,
                    progress_callback=backup_progress,
                    preserve_desired=True,
                    maintenance=True,
                )
                if not stopped:
                    raise RuntimeError(server_instance.last_error or "サーバーを停止できない")
                await self.server_manager.wait_for_exit(server_id)

            # 4. 圧縮 (ZIP) - 一時ディレクトリを使用
            if channel:
                await channel.send(f"[{server_name}] ワールドデータを圧縮中...", silent=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            zip_name = f"backup_{server_id}_{timestamp}.zip"
            archive_fd, zip_path = tempfile.mkstemp(
                prefix=f"backup-{server_id}-",
                suffix=".zip",
            )
            os.close(archive_fd)

            def create_zip():
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for d in existing_dirs:
                        full_path = os.path.join(mc_dir, d)
                        if os.path.isdir(full_path):
                            for root, dirs, files in os.walk(full_path):
                                for file in files:
                                    file_path = os.path.join(root, file)
                                    arcname = os.path.relpath(file_path, mc_dir)
                                    zipf.write(file_path, arcname)
                        else:
                            zipf.write(full_path, os.path.basename(full_path))

            await loop.run_in_executor(None, create_zip)

            size_mb = os.path.getsize(zip_path) / (1024 * 1024)

            # 5. Notion Upload
            if channel:
                await channel.send(f"[{server_name}] Notionへアップロード中... ({size_mb:.1f}MB)", silent=True)

            file_id = await loop.run_in_executor(
                None,
                upload_to_notion,
                zip_path,
                zip_name,
            )

            # DB登録（サーバー名を含める）
            await loop.run_in_executor(None, register_to_database, file_id, zip_name, size_mb)

            if channel:
                await channel.send(f"[{server_name}] バックアップ完了", silent=True)

            # 6. フィンガープリント更新（サーバー停止後の安定状態で計算）
            await loop.run_in_executor(
                None, self._update_fingerprint, server_id, mc_dir, existing_dirs
            )

            # 7. 一時ファイル削除
            os.remove(zip_path)

            # 8. 再起動
            if was_running:
                if channel:
                    await channel.send(f"[{server_name}] サーバーを再起動します。", silent=True)
                started = await self.server_manager.start_server(server_id, maintenance=True)
                if not started:
                    raise RuntimeError(server_instance.last_error or "サーバーを起動できない")

        except Exception as e:
            if channel:
                await channel.send(f"[{server_name}] バックアップエラー: {str(e)}", silent=True)
            logging.exception(f"[Backup] {server_id} backup failed")
        finally:
            if zip_path and os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except OSError as e:
                    logging.warning("[Backup] Failed to remove temporary archive: %s", e)
            if maintenance_started:
                if was_running and not self.server_manager.is_running(server_id):
                    restored = await self.server_manager.start_server(server_id, maintenance=True)
                    if not restored:
                        logging.error(
                            "[Backup] Failed to restore server '%s': %s",
                            server_id,
                            server_instance.last_error,
                        )
                self.server_manager.end_maintenance(server_id)

    @app_commands.command(name="backup", description="手動バックアップを実行します")
    @app_commands.describe(server="バックアップするサーバー（省略時は全サーバー）")
    @app_commands.choices(server=SERVER_CHOICES)
    async def backup(self, interaction: discord.Interaction, server: str | None = None):
        if not check_role(interaction, 'backup'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /backup server={server}")

        if server:
            server_instance = self.server_manager.get_server(server)
            msg = f"[{server_instance.name}] バックアップ処理を開始します..."
        else:
            msg = "全サーバーのバックアップ処理を開始します..."

        await interaction.response.send_message(msg, silent=True)
        await self.perform_backup(interaction.channel, server, force=True)

    @app_commands.command(name="backups", description="Notionにある最新のバックアップリストを表示します")
    async def backups(self, interaction: discord.Interaction):
        """Notionにある最新のバックアップリストを表示"""
        if not check_role(interaction, 'backup'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /backups")

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
            logging.exception(f"[Backup] User {interaction.user} ({interaction.user.id}) /backups error")
            await msg.edit(content=f"エラーが発生しました: {e}")

    @app_commands.command(name="rollback", description="指定したバックアップにロールバックします")
    @app_commands.describe(index="バックアップのインデックス番号")
    async def rollback(self, interaction: discord.Interaction, index: int):
        """指定した番号のバックアップにロールバックする"""
        if not check_role(interaction, 'backup'):
            return await interaction.response.send_message("この操作は管理者（Admin）のみ可能です。", ephemeral=True)

        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /rollback index={index}")

        await interaction.response.send_message("🔄 ロールバック準備中... Notion情報を取得しています。", silent=True)
        status_msg = await interaction.original_response()

        log_content = "🔄 ロールバック準備中... Notion情報を取得しています。\n"

        async def update_status(text):
            nonlocal log_content
            timestamp = datetime.now().strftime("%H:%M:%S")
            safe_text = escape_discord_code_block(str(text))
            new_line = f"[{timestamp}] {safe_text}\n"
            log_content += new_line
            if len(log_content) > 1900:
                log_content = "..." + log_content[-1900:]
            await status_msg.edit(content=f"```\n{log_content}\n```")

        maintenance_started = False
        was_running = False
        detected_server_id = None
        save_path = None

        try:
            loop = asyncio.get_event_loop()
            backup_list = await loop.run_in_executor(None, get_backups_list, 10)

            if index < 0 or index >= len(backup_list):
                await update_status("❌ 指定された番号のバックアップは存在しません。")
                return

            target_backup = backup_list[index]
            filename = target_backup['filename']
            display_filename = escape_discord_code_block(
                str(filename).replace("\r", " ").replace("\n", " ")[:200]
            )
            download_url = target_backup.get('url')
            if not download_url:
                await update_status("❌ バックアップのダウンロード URL が見つかりません。")
                return

            # ファイル名からサーバーIDを推測
            # 形式: backup_paper_20240105_1200.zip or backup_forge_20240105_1200.zip
            detected_server_id = DEFAULT_SERVER
            for srv_id in SERVER_IDS:
                if f"_{srv_id}_" in filename:
                    detected_server_id = srv_id
                    break

            server_instance = self.server_manager.get_server(detected_server_id)
            mc_dir = server_instance.cwd if server_instance else "/opt/minecraft"
            server_name = server_instance.name if server_instance else detected_server_id

            await update_status(f"対象サーバー: {server_name}")

            maintenance_started = self.server_manager.begin_maintenance(detected_server_id)
            if not maintenance_started:
                await update_status("別のメンテナンス処理を実行中である。")
                return

            # --- 1. サーバー停止 ---
            was_running = self.server_manager.is_running(detected_server_id)
            if was_running:
                await update_status(f"⏹️ {server_name} を停止しています...")
                stopped = await self.server_manager.stop_server(
                    detected_server_id,
                    progress_callback=update_status,
                    preserve_desired=True,
                    maintenance=True,
                )
                if not stopped:
                    raise RuntimeError(server_instance.last_error or "サーバーを停止できない")
                await self.server_manager.wait_for_exit(detected_server_id)
                await update_status("サーバー停止を確認しました。")

            # --- 2. バックアップのダウンロード ---
            await update_status(f"⬇️ ダウンロード中: {display_filename} ...")

            archive_fd, save_path = tempfile.mkstemp(
                prefix=f"rollback-{detected_server_id}-",
                suffix=".archive",
            )
            os.close(archive_fd)
            logging.info("[Rollback] Downloading selected backup to temporary storage")

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
                backup_dirs = self.get_backup_dirs(detected_server_id)
                allowed_roots = set(backup_dirs)
                is_zip = zipfile.is_zipfile(save_path)
                is_tar = tarfile.is_tarfile(save_path)
                if is_zip:
                    with zipfile.ZipFile(save_path, "r") as archive:
                        members = archive.infolist()
                        _ensure_archive_fits(
                            mc_dir,
                            len(members),
                            sum(member.file_size for member in members),
                        )
                        for member in members:
                            _validate_archive_path(mc_dir, member.filename, allowed_roots)
                            if ((member.external_attr >> 16) & 0o170000) == stat.S_IFLNK:
                                raise ValueError("シンボリックリンクを含むZIPは拒否する")
                elif is_tar:
                    with tarfile.open(save_path, "r") as archive:
                        members = archive.getmembers()
                        _ensure_archive_fits(
                            mc_dir,
                            len(members),
                            sum(member.size for member in members),
                        )
                        for member in members:
                            _validate_archive_path(mc_dir, member.name, allowed_roots)
                            if not (member.isfile() or member.isdir()):
                                raise ValueError("リンクまたは特殊ファイルを含むTARは拒否する")
                else:
                    raise ValueError(f"未知のファイル形式である: {filename}")

                # --- 3. 隔離ディレクトリへ展開 ---
                await update_status(f"📦 バックアップを展開中... (File: {display_filename})")
                stage_dir = tempfile.mkdtemp(prefix=".rollback-stage-", dir=mc_dir)
                try:
                    if is_zip:
                        def unzip_safe():
                            count = _extract_zip_safely(save_path, stage_dir, allowed_roots)
                            logging.info(f"[Rollback] Unzipped {count} files.")
                        await loop.run_in_executor(None, unzip_safe)
                    elif is_tar:
                        def untar_safe():
                            count = _extract_tar_safely(save_path, stage_dir, allowed_roots)
                            logging.info(f"[Rollback] Untarred {count} files.")
                        await loop.run_in_executor(None, untar_safe)

                    # --- 4. 検証済みデータへ置換 ---
                    await update_status("検証済みワールドデータへ切り替え中...")
                    replaced = await loop.run_in_executor(
                        None,
                        _replace_backup_roots,
                        stage_dir,
                        mc_dir,
                        backup_dirs,
                    )
                    logging.info("[Rollback] Replaced %d backup roots", replaced)
                finally:
                    if os.path.exists(stage_dir):
                        shutil.rmtree(stage_dir)

                logging.info("[Rollback] Extraction complete. Cleaning up archive file.")
                if os.path.exists(save_path):
                    os.remove(save_path)

                logging.info("[Rollback] Rollback process finished successfully.")
                await update_status("展開処理終了。")

            except Exception as e:
                logging.error(f"Rollback file operation failed: {e}")
                raise

            # --- 5. サーバー再起動 ---
            if was_running:
                await update_status(f"✅ ロールバック完了。{server_name} を起動します。")
                started = await self.server_manager.start_server(
                    detected_server_id,
                    maintenance=True,
                )
                if not started:
                    raise RuntimeError(server_instance.last_error or "サーバーを起動できない")
            else:
                await update_status("ロールバック完了。停止状態を維持する。")

        except Exception as e:
            logging.exception("[Rollback] Critical error")
            await status_msg.edit(content=f"重大なエラーが発生しました: {e}")
        finally:
            if save_path and os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except OSError as e:
                    logging.warning("[Rollback] Failed to remove temporary archive: %s", e)
            if maintenance_started and detected_server_id:
                if was_running and not self.server_manager.is_running(detected_server_id):
                    restored = await self.server_manager.start_server(
                        detected_server_id,
                        maintenance=True,
                    )
                    if not restored:
                        logging.error(
                            "[Rollback] Failed to restore server '%s': %s",
                            detected_server_id,
                            server_instance.last_error,
                        )
                self.server_manager.end_maintenance(detected_server_id)

    @tasks.loop(minutes=1)
    async def scheduler(self):
        now = datetime.now().strftime("%H:%M")
        if 'backup' in CONFIG and 'schedule_time' in CONFIG['backup']:
            if now == CONFIG['backup']['schedule_time']:
                channel = self.bot.get_channel(CHANNEL_ID)
                try:
                    await self.perform_backup(channel, None)
                except Exception as e:
                    logging.exception(f"[Backup] Scheduled backup failed: {e}")

    @scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(BackupSystem(bot, bot.server_manager))
