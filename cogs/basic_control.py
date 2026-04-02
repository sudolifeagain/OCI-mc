import asyncio
import subprocess
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from settings import DISCORD_OWNER_ID, SERVER_IDS, SERVERS_CONFIG, DEFAULT_SERVER, get_log_channel_id
from utils.permissions import check_role
from utils.rcon import get_rcon_client
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
        self.discord_log_sender.start()

    def cog_unload(self):
        self.discord_log_sender.cancel()

    @app_commands.command(name="start", description="Minecraftサーバーを起動します")
    @app_commands.describe(server="起動するサーバー")
    @app_commands.choices(server=SERVER_CHOICES)
    async def start(self, interaction: discord.Interaction, server: str = DEFAULT_SERVER):
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
        await self.server_manager.start_server(server)

    @app_commands.command(name="stop", description="Minecraftサーバーを停止します")
    @app_commands.describe(server="停止するサーバー")
    @app_commands.choices(server=SERVER_CHOICES)
    async def stop(self, interaction: discord.Interaction, server: str = DEFAULT_SERVER):
        if not check_role(interaction, 'stop'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        server_instance = self.server_manager.get_server(server)
        if not server_instance:
            return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

        if not self.server_manager.is_running(server):
            return await interaction.response.send_message(f"{server_instance.name} は起動していません。", ephemeral=True)

        await interaction.response.defer(thinking=False)
        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /stop server={server}")

        async def progress_callback(msg: str) -> None:
            await interaction.followup.send(msg, silent=True)

        result = await self.server_manager.stop_server(server, progress_callback=progress_callback)
        if result:
            await interaction.followup.send(f"🛑 {server_instance.name} を停止しました。", silent=True)
        else:
            await interaction.followup.send(f"{server_instance.name} の停止に失敗しました。", silent=True)

    @app_commands.command(name="restart", description="Minecraftサーバーを再起動します")
    @app_commands.describe(server="再起動するサーバー")
    @app_commands.choices(server=SERVER_CHOICES)
    async def restart(self, interaction: discord.Interaction, server: str = DEFAULT_SERVER):
        if not check_role(interaction, 'restart'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        server_instance = self.server_manager.get_server(server)
        if not server_instance:
            return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

        await interaction.response.send_message(f"🔄 {server_instance.name} を再起動しています...", silent=True)
        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /restart server={server}")

        async def progress_callback(msg: str) -> None:
            await interaction.followup.send(msg, silent=True)

        if self.server_manager.is_running(server):
            await self.server_manager.stop_server(server, progress_callback=progress_callback)
            await self.server_manager.wait_for_exit(server)

        mem_ok, mem_msg = self.server_manager.check_memory_for_start(server)
        if not mem_ok:
            return await interaction.followup.send(
                f"⚠️ {server_instance.name} の再起動を中止:\n```\n{mem_msg}\n```",
                silent=True
            )

        await self.server_manager.start_server(server)
        await interaction.followup.send(f"✅ {server_instance.name} の再起動を開始しました。", silent=True)

    @app_commands.command(name="cmd", description="サーバーにRCONコマンドを送信します")
    @app_commands.describe(command_str="送信するコマンド", server="対象サーバー")
    @app_commands.choices(server=SERVER_CHOICES)
    async def cmd(self, interaction: discord.Interaction, command_str: str, server: str = DEFAULT_SERVER):
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

        await interaction.response.defer(thinking=False)
        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /cmd server={server} {command_str}")

        # RCONでコマンド実行
        success, result = await rcon_client.execute(command_str)

        if success:
            # 結果が長い場合は分割
            if len(result) > 1900:
                await interaction.followup.send(f"📝 [{server_instance.name}] `{command_str}`", silent=True)
                while result:
                    chunk = result[:1900]
                    result = result[1900:]
                    await interaction.followup.send(f"```{chunk}```", silent=True)
            elif result:
                await interaction.followup.send(
                    f"📝 [{server_instance.name}] `{command_str}`\n```{result}```",
                    silent=True
                )
            else:
                await interaction.followup.send(
                    f"📝 [{server_instance.name}] `{command_str}` (応答なし)",
                    silent=True
                )
        else:
            await interaction.followup.send(f"❌ [{server_instance.name}] エラー: {result}", silent=True)

    @app_commands.command(name="whitelist_add", description="ホワイトリストにプレイヤーを追加します")
    @app_commands.describe(player_name="追加するプレイヤー名", server="対象サーバー")
    @app_commands.choices(server=SERVER_CHOICES)
    async def whitelist_add(self, interaction: discord.Interaction, player_name: str, server: str = DEFAULT_SERVER):
        if not check_role(interaction, 'whitelist_add'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        server_instance = self.server_manager.get_server(server)
        if not server_instance:
            return await interaction.response.send_message(f"サーバー '{server}' が見つかりません。", ephemeral=True)

        if not self.server_manager.is_running(server):
            return await interaction.response.send_message(f"{server_instance.name} は起動していません。", ephemeral=True)

        cmd = f"whitelist add {player_name.strip()}"
        await self.server_manager.write_stdin(server, cmd)
        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /whitelist_add server={server} {player_name}")
        await interaction.response.send_message(f"✅ [{server_instance.name}] ホワイトリスト追加: `{cmd}`", silent=True)

    @app_commands.command(name="status", description="サーバーのステータス(CPU, Memory, Uptime)を表示します")
    @app_commands.describe(server="対象サーバー（省略時は全サーバー）")
    @app_commands.choices(server=SERVER_CHOICES)
    async def status(self, interaction: discord.Interaction, server: str | None = None):
        if not check_role(interaction, 'status'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        # サーバー指定がない場合は全サーバーのステータスを表示
        if server is None:
            await interaction.response.defer(thinking=False)

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

            await interaction.response.defer(thinking=False)

            stats = self.server_manager.get_server_stats(server)
            if not stats:
                return await interaction.followup.send(f"{server_instance.name} は起動していません。", ephemeral=True)

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

    @app_commands.command(name="shell", description="ホストOSでシェルコマンドを実行します(Ownerのみ)")
    @app_commands.describe(command_str="実行するシェルコマンド")
    async def shell(self, interaction: discord.Interaction, command_str: str):
        """Execute an arbitrary shell command on the local instance. Owner-only."""
        if not DISCORD_OWNER_ID or interaction.user.id != DISCORD_OWNER_ID:
            return await interaction.response.send_message("権限がありません。Ownerのみが使用できます。", ephemeral=True)

        logging.info(f"Owner {interaction.user} ({interaction.user.id}) executed shell: {command_str}")
        await interaction.response.send_message(f"実行中: `{command_str}`", silent=True)

        try:
            proc = await asyncio.create_subprocess_shell(
                command_str,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd="/opt/minecraft"
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            except asyncio.TimeoutError:
                proc.kill()
                await interaction.followup.send("コマンドがタイムアウトしました（60秒）", silent=True)
                return
            out_text = stdout.decode('utf-8', errors='ignore')
            if not out_text:
                await interaction.followup.send("(出力なし)", silent=True)
                return

            while out_text:
                chunk = out_text[:1900]
                out_text = out_text[1900:]
                await interaction.followup.send(f"```{chunk}```", silent=True)

        except Exception as e:
            logging.error(f"shell command exec error: {e}")
            await interaction.followup.send(f"実行中にエラーが発生しました: {e}", silent=True)

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
            while not q.empty():
                item = await q.get()
                # ログ形式: (server_id, line) または line のみ
                if isinstance(item, tuple):
                    _, line = item
                else:
                    line = item
                messages.append(line)

            if not messages:
                continue

            # サーバー名をプレフィックスとして付加
            server_name = server_instance.name
            full_text = "".join(messages)

            while len(full_text) > 0:
                chunk = full_text[:1850]
                full_text = full_text[1850:]
                try:
                    await channel.send(f"**[{server_name}]**\n```{chunk}```", silent=True)
                except discord.HTTPException as e:
                    logging.warning(f"Failed to send log to Discord for {server_name}: {e}")
                    break

    @discord_log_sender.before_loop
    async def before_discord_log_sender(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(BasicControl(bot, bot.server_manager))
