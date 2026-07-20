import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from settings import SERVER_IDS, SERVERS_CONFIG, DEFAULT_SERVER, get_log_channel_id
from utils.permissions import check_role
from utils.rcon import get_rcon_client
from utils.discord_security import (
    escape_discord_code_block,
    is_allowed_command_context,
    is_shell_user,
    validate_player_name,
)
from utils.shell_runner import run_shell_command
from cogs.status_display import get_machine_stats


def get_server_choices():
    """Discord用のサーバー選択肢を生成する"""
    choices = []
    for server_id, config in SERVERS_CONFIG.items():
        choices.append(app_commands.Choice(name=config.get('name', server_id), value=server_id))
    return choices


# サーバー選択肢をグローバルに定義
SERVER_CHOICES = get_server_choices()

TICK_FOOTER = "TPS: 🟢 正常 (<40ms) | 🟡 注意 (40-50ms) | 🔴 危険 (>50ms)"


def format_tick_stats(tick_stats: dict) -> str:
    """TPS/MSPTをインジケーター付きでフォーマットする"""
    tps = tick_stats["tps"]
    mspt = tick_stats["mspt"]
    if mspt < 40:
        indicator = "🟢"
    elif mspt <= 50:
        indicator = "🟡"
    else:
        indicator = "🔴"
    return f"{indicator} {tps} (MSPT: {mspt}ms)"


class BasicControl(commands.Cog):
    def __init__(self, bot, server_manager):
        self.bot = bot
        self.server_manager = server_manager
        self._shell_lock = asyncio.Lock()
        self.discord_log_sender.start()

    def cog_unload(self):
        self.discord_log_sender.cancel()

    @app_commands.command(name="start", description="Minecraftサーバーを起動します")
    @app_commands.guild_only()
    @app_commands.describe(server="起動するサーバー")
    @app_commands.choices(server=SERVER_CHOICES)
    async def start(self, interaction: discord.Interaction, server: str = DEFAULT_SERVER):
        if not is_allowed_command_context(interaction):
            return await interaction.response.send_message(
                "このチャンネルでは管理コマンドを実行できません。", ephemeral=True
            )
        if not check_role(interaction, 'start'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        server_instance = self.server_manager.get_server(server)
        if not server_instance:
            return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

        if self.server_manager.is_running(server):
            return await interaction.response.send_message(f"{server_instance.name} は既に起動しています。", ephemeral=True)

        mem_ok, mem_msg = self.server_manager.check_memory_for_start(server)
        if not mem_ok:
            return await interaction.response.send_message(
                f"⚠️ {server_instance.name} を起動できません:\n```\n{mem_msg}\n```",
                ephemeral=True
            )

        await interaction.response.send_message(f"🚀 {server_instance.name} の起動コマンドを送信しました。", silent=True)
        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /start server={server}")
        result = await self.server_manager.start_server(server)
        if result:
            await interaction.edit_original_response(
                content=f"{server_instance.name} の起動が完了しました。"
            )
        else:
            await interaction.edit_original_response(
                content=f"{server_instance.name} の起動に失敗しました: {server_instance.last_error}"
            )

    @app_commands.command(name="stop", description="Minecraftサーバーを停止します")
    @app_commands.guild_only()
    @app_commands.describe(server="停止するサーバー", force="RCON確認不能時も強制停止する")
    @app_commands.choices(server=SERVER_CHOICES)
    async def stop(
        self,
        interaction: discord.Interaction,
        server: str = DEFAULT_SERVER,
        force: bool = False,
    ):
        if not is_allowed_command_context(interaction):
            return await interaction.response.send_message(
                "このチャンネルでは管理コマンドを実行できません。", ephemeral=True
            )
        if not check_role(interaction, 'stop'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        server_instance = self.server_manager.get_server(server)
        if not server_instance:
            return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

        if not self.server_manager.is_running(server):
            return await interaction.response.send_message(f"{server_instance.name} は起動していません。", ephemeral=True)

        # defer() (type 5) は Discord API 仕様により SUPPRESS_NOTIFICATIONS を無視するため、
        # silent ACK には send_message (type 4) を使う。/cmd /status の silent ACK も同じ理由。
        # ref: https://github.com/discord/discord-api-docs/issues/4784
        await interaction.response.send_message(f"⏳ {server_instance.name} を停止しています...", silent=True)
        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /stop server={server}")

        async def progress_callback(msg: str) -> None:
            await interaction.edit_original_response(content=msg)

        result = await self.server_manager.stop_server(
            server,
            progress_callback=progress_callback,
            force=force,
        )
        if result:
            await interaction.edit_original_response(content=f"🛑 {server_instance.name} を停止しました。")
        else:
            await interaction.edit_original_response(
                content=f"{server_instance.name} の停止に失敗しました: {server_instance.last_error}"
            )

    @app_commands.command(name="restart", description="Minecraftサーバーを再起動します")
    @app_commands.guild_only()
    @app_commands.describe(server="再起動するサーバー", force="RCON確認不能時も強制停止する")
    @app_commands.choices(server=SERVER_CHOICES)
    async def restart(
        self,
        interaction: discord.Interaction,
        server: str = DEFAULT_SERVER,
        force: bool = False,
    ):
        if not is_allowed_command_context(interaction):
            return await interaction.response.send_message(
                "このチャンネルでは管理コマンドを実行できません。", ephemeral=True
            )
        if not check_role(interaction, 'restart'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        server_instance = self.server_manager.get_server(server)
        if not server_instance:
            return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

        await interaction.response.send_message(f"🔄 {server_instance.name} を再起動しています...", silent=True)
        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /restart server={server}")

        async def progress_callback(msg: str) -> None:
            await interaction.followup.send(msg, silent=True)

        result = await self.server_manager.restart_server(
            server,
            progress_callback=progress_callback,
            force=force,
        )
        if result:
            await interaction.edit_original_response(
                content=f"{server_instance.name} の再起動が完了しました。"
            )
        else:
            await interaction.edit_original_response(
                content=f"{server_instance.name} の再起動に失敗しました: {server_instance.last_error}"
            )

    @app_commands.command(name="cmd", description="サーバーにRCONコマンドを送信します")
    @app_commands.guild_only()
    @app_commands.describe(command_str="送信するコマンド", server="対象サーバー")
    @app_commands.choices(server=SERVER_CHOICES)
    async def cmd(self, interaction: discord.Interaction, command_str: str, server: str = DEFAULT_SERVER):
        if not is_allowed_command_context(interaction):
            return await interaction.response.send_message(
                "このチャンネルでは管理コマンドを実行できません。", ephemeral=True
            )
        if not check_role(interaction, 'rcon'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        server_instance = self.server_manager.get_server(server)
        if not server_instance:
            return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

        # RCONクライアントを取得
        server_config = SERVERS_CONFIG.get(server, {})
        rcon_client = get_rcon_client(server_config)

        if not rcon_client:
            return await interaction.response.send_message(
                f"{server_instance.name} のRCONが設定されていません。",
                ephemeral=True
            )

        await interaction.response.send_message(
            f"[{server_instance.name}] RCONコマンドを実行中...",
            ephemeral=True,
        )
        logging.info(
            "User %s (%s) executed /cmd server=%s length=%d",
            interaction.user,
            interaction.user.id,
            server,
            len(command_str),
        )

        # RCONでコマンド実行
        success, result = await rcon_client.execute(command_str)

        if success:
            safe_result = escape_discord_code_block(result)
            # 結果が長い場合は分割
            if len(safe_result) > 1800:
                await interaction.edit_original_response(
                    content=f"[{server_instance.name}] 実行完了。結果を分割表示します。"
                )
                while safe_result:
                    chunk = safe_result[:1800]
                    safe_result = safe_result[1800:]
                    await interaction.followup.send(
                        f"```{chunk}```",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            elif safe_result:
                await interaction.edit_original_response(
                    content=f"[{server_instance.name}] 実行結果\n```{safe_result}```",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await interaction.edit_original_response(
                    content=f"[{server_instance.name}] 実行完了 (応答なし)"
                )
        else:
            await interaction.edit_original_response(
                content=f"[{server_instance.name}] RCONエラー: {escape_discord_code_block(result)}"
            )

    @app_commands.command(name="whitelist_add", description="ホワイトリストにプレイヤーを追加します")
    @app_commands.guild_only()
    @app_commands.describe(player_name="追加するプレイヤー名", server="対象サーバー")
    @app_commands.choices(server=SERVER_CHOICES)
    async def whitelist_add(self, interaction: discord.Interaction, player_name: str, server: str = DEFAULT_SERVER):
        if not is_allowed_command_context(interaction):
            return await interaction.response.send_message(
                "このチャンネルでは管理コマンドを実行できません。", ephemeral=True
            )
        if not check_role(interaction, 'whitelist_add'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        server_instance = self.server_manager.get_server(server)
        if not server_instance:
            return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

        if not self.server_manager.is_running(server):
            return await interaction.response.send_message(f"{server_instance.name} は起動していません。", ephemeral=True)

        safe_player_name = validate_player_name(player_name)
        if not safe_player_name:
            return await interaction.response.send_message(
                "プレイヤー名が不正です。英数字、`_`、`.`、`-`のみを使用できます。",
                ephemeral=True,
            )

        rcon_client = get_rcon_client(SERVERS_CONFIG.get(server, {}))
        if not rcon_client:
            return await interaction.response.send_message(
                f"{server_instance.name} のRCONが設定されていません。",
                ephemeral=True,
            )
        success, result = await rcon_client.execute(f"whitelist add {safe_player_name}")
        logging.info(
            "User %s (%s) executed /whitelist_add server=%s player=%s success=%s",
            interaction.user,
            interaction.user.id,
            server,
            safe_player_name,
            success,
        )
        if success:
            await interaction.response.send_message(
                f"[{server_instance.name}] ホワイトリストへ `{safe_player_name}` を追加しました。",
                silent=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                f"[{server_instance.name}] ホワイトリスト追加に失敗しました: {result}",
                ephemeral=True,
            )

    @app_commands.command(name="status", description="サーバーのステータス(CPU, Memory, Uptime)を表示します")
    @app_commands.guild_only()
    @app_commands.describe(server="対象サーバー（省略時は全サーバー）")
    @app_commands.choices(server=SERVER_CHOICES)
    async def status(self, interaction: discord.Interaction, server: str | None = None):
        if not is_allowed_command_context(interaction):
            return await interaction.response.send_message(
                "このチャンネルでは管理コマンドを実行できません。", ephemeral=True
            )
        if not check_role(interaction, 'status'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        # サーバー指定がない場合は全サーバーのステータスを表示
        if server is None:
            await interaction.response.send_message("⏳ 全サーバーのステータスを取得中...", silent=True)

            embed = discord.Embed(title="Minecraft Server Status", color=0x00ff00)

            # マシン統計
            embed.add_field(
                name="マシン",
                value=get_machine_stats(),
                inline=False
            )

            for server_id in SERVER_IDS:
                server_instance = self.server_manager.get_server(server_id)
                if not server_instance:
                    continue

                stats = self.server_manager.get_server_stats(server_id)
                if stats:
                    seconds = int(stats['uptime_seconds'])
                    m, s = divmod(seconds, 60)
                    h, m = divmod(m, 60)
                    uptime_str = f"{h}h {m}m {s}s"

                    value_lines = [
                        f"CPU: {stats['cpu_percent']}%",
                        f"Memory: {stats['memory_mb']:.1f} MB",
                        f"Uptime: {uptime_str}",
                    ]

                    # TPS/MSPT取得（RCON経由、3秒タイムアウト）
                    server_config = SERVERS_CONFIG.get(server_id, {})
                    rcon_client = get_rcon_client(server_config)
                    try:
                        tick_stats = await asyncio.wait_for(
                            server_instance.get_tick_stats(rcon_client), timeout=3.0
                        )
                    except asyncio.TimeoutError:
                        tick_stats = None
                    if tick_stats is not None:
                        value_lines.append(f"TPS: {format_tick_stats(tick_stats)}")

                    embed.add_field(
                        name=f"🟢 {server_instance.name}",
                        value="\n".join(value_lines),
                        inline=True
                    )
                else:
                    embed.add_field(
                        name=f"🔴 {server_instance.name}",
                        value="Offline",
                        inline=True
                    )

            embed.set_footer(text=TICK_FOOTER)
            await interaction.followup.send(embed=embed, silent=True)
        else:
            # 特定のサーバーのステータスを表示
            server_instance = self.server_manager.get_server(server)
            if not server_instance:
                return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

            await interaction.response.send_message(
                f"⏳ {server_instance.name} のステータスを取得中...", silent=True
            )

            stats = self.server_manager.get_server_stats(server)
            if not stats:
                return await interaction.edit_original_response(
                    content=f"🔴 {server_instance.name} は起動していません。"
                )

            seconds = int(stats['uptime_seconds'])
            m, s = divmod(seconds, 60)
            h, m = divmod(m, 60)
            uptime_str = f"{h}h {m}m {s}s"

            embed = discord.Embed(title=f"{server_instance.name} Status", color=0x00ff00)
            embed.add_field(name="Status", value="🟢 Running", inline=False)
            embed.add_field(name="CPU Usage", value=f"{stats['cpu_percent']}%", inline=True)
            embed.add_field(name="Memory Usage", value=f"{stats['memory_mb']:.1f} MB", inline=True)
            embed.add_field(name="Uptime", value=uptime_str, inline=True)
            embed.add_field(name="Port", value=str(server_instance.port), inline=True)

            # TPS/MSPT取得（RCON経由、3秒タイムアウト）
            server_config = SERVERS_CONFIG.get(server, {})
            rcon_client = get_rcon_client(server_config)
            try:
                tick_stats = await asyncio.wait_for(
                    server_instance.get_tick_stats(rcon_client), timeout=3.0
                )
            except asyncio.TimeoutError:
                tick_stats = None
            if tick_stats is not None:
                embed.add_field(name="TPS", value=format_tick_stats(tick_stats), inline=True)

            embed.set_footer(text=TICK_FOOTER)
            await interaction.followup.send(embed=embed, silent=True)

        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /status server={server}")

    @app_commands.command(name="shell", description="許可ユーザーがホストOSでコマンドを実行します")
    @app_commands.guild_only()
    @app_commands.describe(command_str="実行するシェルコマンド")
    async def shell(self, interaction: discord.Interaction, command_str: str):
        """許可ユーザー向けの任意OSコマンド実行機能。"""
        if not is_allowed_command_context(interaction, shell=True):
            return await interaction.response.send_message(
                "このチャンネルではOSコマンドを実行できません。", ephemeral=True
            )
        if not is_shell_user(interaction):
            return await interaction.response.send_message("OSコマンドの実行権限がありません。", ephemeral=True)

        if not command_str.strip() or len(command_str) > 4000:
            return await interaction.response.send_message(
                "コマンドは1～4000文字で指定してください。", ephemeral=True
            )
        if self._shell_lock.locked():
            return await interaction.response.send_message(
                "別のOSコマンドを実行中です。完了後に再実行してください。", ephemeral=True
            )

        await interaction.response.send_message("OSコマンドを実行中です。", ephemeral=True)
        logging.info(
            "User %s (%s) executed /shell length=%d",
            interaction.user,
            interaction.user.id,
            len(command_str),
        )

        try:
            async with self._shell_lock:
                result = await run_shell_command(command_str)
            out_text = escape_discord_code_block(result.output)
            summary = f"終了コード: {result.returncode}"
            if result.timed_out:
                summary += " / 60秒でタイムアウト"
            if result.output_limited:
                summary += " / 出力上限64KiBに到達"
            if not out_text:
                await interaction.edit_original_response(content=f"{summary}\n(出力なし)")
                return

            await interaction.edit_original_response(content=summary)
            while out_text:
                chunk = out_text[:1800]
                out_text = out_text[1800:]
                await interaction.followup.send(
                    f"```{chunk}```",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

        except Exception as e:
            logging.exception("shell command exec error")
            await interaction.edit_original_response(content=f"実行中にエラーが発生しました: {e}")

    @tasks.loop(seconds=2.0)
    async def discord_log_sender(self):
        """Queueに溜まったログをまとめてDiscordに送信"""
        # 全サーバーのログキューをチェック
        for server_id, server_instance in self.server_manager.servers.items():
            # ログ転送が無効なサーバーはスキップ（キューを空にしてメモリリーク防止）
            if not server_instance.log_forwarding:
                while not server_instance.log_queue.empty():
                    try:
                        server_instance.log_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                continue

            # サーバーごとのチャンネルを取得
            channel = self.bot.get_channel(get_log_channel_id(server_id))
            if not channel:
                continue

            messages = []
            q = server_instance.log_queue
            total_chars = 0
            while not q.empty() and len(messages) < 100 and total_chars < 9000:
                try:
                    item = q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                # ログ形式: (server_id, line) または line のみ
                if isinstance(item, tuple):
                    _, line = item
                else:
                    line = item
                messages.append(line)
                total_chars += len(line)

            if not messages:
                continue

            # サーバー名をプレフィックスとして付加
            server_name = server_instance.name
            full_text = escape_discord_code_block("".join(messages))
            if server_instance.dropped_log_lines:
                full_text = (
                    f"[Bot] 混雑により{server_instance.dropped_log_lines}行を破棄しました。\n"
                    f"{full_text}"
                )
                server_instance.dropped_log_lines = 0

            chunks_sent = 0
            while full_text and chunks_sent < 5:
                chunk = full_text[:1800]
                full_text = full_text[1800:]
                try:
                    await channel.send(
                        f"**[{server_name}]**\n```{chunk}```",
                        silent=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    chunks_sent += 1
                except discord.HTTPException as e:
                    logging.warning(f"Failed to send log to Discord for {server_name}: {e}")
                    server_instance.dropped_log_lines += len(messages)
                    break

    @discord_log_sender.before_loop
    async def before_discord_log_sender(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(BasicControl(bot, bot.server_manager))
