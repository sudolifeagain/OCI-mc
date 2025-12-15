import asyncio
import subprocess
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from settings import CONFIG, DISCORD_OWNER_ID, CHANNEL_ID
from utils.permissions import check_role

class BasicControl(commands.Cog):
    def __init__(self, bot, server_manager):
        self.bot = bot
        self.server_manager = server_manager
        self.discord_log_sender.start()

    def cog_unload(self):
        self.discord_log_sender.cancel()

    @app_commands.command(name="start", description="Minecraftサーバーを起動します")
    async def start(self, interaction: discord.Interaction):
        if not check_role(interaction, 'start'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)
        
        if self.server_manager.is_running():
            return await interaction.response.send_message("サーバーは既に起動しています。", ephemeral=True)
            
        await interaction.response.send_message("起動コマンドを送信しました。", silent=True)
        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /start")
        await self.server_manager.start_server()

    @app_commands.command(name="stop", description="Minecraftサーバーを停止します")
    async def stop(self, interaction: discord.Interaction):
        if not check_role(interaction, 'stop'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)
        
        if self.server_manager.is_running():
            await self.server_manager.stop_server()
            logging.info(f"User {interaction.user} ({interaction.user.id}) executed /stop")
            await interaction.response.send_message("停止コマンドを送信しました。", silent=True)
        else:
            await interaction.response.send_message("サーバーは起動していません。", ephemeral=True)

    @app_commands.command(name="cmd", description="サーバーコンソールにコマンドを送信します")
    @app_commands.describe(command_str="送信するコマンド")
    async def cmd(self, interaction: discord.Interaction, command_str: str):
        # 権限チェック (check_role) - Adminのみ
        if not check_role(interaction, 'command'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        if self.server_manager.is_running():
            await self.server_manager.write_stdin(command_str)
            logging.info(f"User {interaction.user} ({interaction.user.id}) executed /cmd {command_str}")
            await interaction.response.send_message(f"コマンド送信: `{command_str}`", silent=True)
        else:
             await interaction.response.send_message("サーバーは起動していません。", ephemeral=True)

    @app_commands.command(name="whitelist_add", description="ホワイトリストにプレイヤーを追加します")
    @app_commands.describe(player_name="追加するプレイヤー名")
    async def whitelist_add(self, interaction: discord.Interaction, player_name: str):
        if not check_role(interaction, 'whitelist_add'):
             return await interaction.response.send_message("権限がありません。", ephemeral=True)

        if not self.server_manager.is_running():
             return await interaction.response.send_message("サーバーは起動していません。", ephemeral=True)
        
        cmd = f"whitelist add {player_name.strip()}"
        await self.server_manager.write_stdin(cmd)
        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /whitelist_add {player_name}")
        await interaction.response.send_message(f"ホワイトリスト追加コマンドを送信しました: `{cmd}`", silent=True)

    @app_commands.command(name="status", description="サーバーのステータス(CPU, Memory, Uptime)を表示します")
    async def status(self, interaction: discord.Interaction):
        if not check_role(interaction, 'status'):
             return await interaction.response.send_message("権限がありません。", ephemeral=True)

        stats = self.server_manager.get_server_stats()
        if not stats:
             return await interaction.response.send_message("サーバーは起動していません/情報を取得できません。", ephemeral=True)
        
        # Uptime formatting
        seconds = int(stats['uptime_seconds'])
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        uptime_str = f"{h}h {m}m {s}s"
        
        embed = discord.Embed(title="Minecraft Server Status", color=0x00ff00)
        embed.add_field(name="Status", value="Running", inline=False)
        embed.add_field(name="CPU Usage", value=f"{stats['cpu_percent']}%", inline=True)
        embed.add_field(name="Memory Usage", value=f"{stats['memory_mb']:.1f} MB", inline=True)
        embed.add_field(name="Uptime", value=uptime_str, inline=True)
        
        await interaction.response.send_message(embed=embed, silent=True)
        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /status")

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
                # Here we use silent=True for logs/output too
                await interaction.followup.send(f"```{chunk}```", silent=True)

        except Exception as e:
            logging.error(f"shell command exec error: {e}")
            await interaction.followup.send(f"実行中にエラーが発生しました: {e}", silent=True)

    @tasks.loop(seconds=2.0)
    async def discord_log_sender(self):
        """Queueに溜まったログをまとめてDiscordに送信"""
        messages = []
        q = self.server_manager.log_queue
        while not q.empty():
            messages.append(await q.get())

        if not messages:
            return

        # 1900文字ごとに分割して送信
        full_text = "".join(messages)
        channel = self.bot.get_channel(CHANNEL_ID)
        if channel:
            while len(full_text) > 0:
                chunk = full_text[:1900]
                full_text = full_text[1900:]
                try:
                    await channel.send(f"```{chunk}```", silent=True)
                except Exception as e:
                    print(f"Log send error: {e}")
            
    @discord_log_sender.before_loop
    async def before_discord_log_sender(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    # ServerManager is expected to be passed via bot instance or global
    # For now, let's assume it's attached to bot
    await bot.add_cog(BasicControl(bot, bot.server_manager))
