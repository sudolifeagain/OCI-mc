import asyncio
import subprocess
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from settings import CONFIG, DISCORD_OWNER_ID, CHANNEL_ID, check_role, check_whitelist_add_permission

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
        await self.server_manager.start_server()

    @app_commands.command(name="stop", description="Minecraftサーバーを停止します")
    async def stop(self, interaction: discord.Interaction):
        if not check_role(interaction, 'stop'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)
        
        if self.server_manager.is_running():
            await self.server_manager.stop_server()
            await interaction.response.send_message("停止コマンドを送信しました。", silent=True)
        else:
            await interaction.response.send_message("サーバーは起動していません。", ephemeral=True)

    @app_commands.command(name="cmd", description="サーバーコンソールにコマンドを送信します")
    @app_commands.describe(command_str="送信するコマンド")
    async def cmd(self, interaction: discord.Interaction, command_str: str):
        # 権限チェック (check_role および check_whitelist_add_permission)
        if not check_role(interaction, 'command'):
            # 特例: userロール かつ whitelist add
            if not check_whitelist_add_permission(interaction, command_str):
                return await interaction.response.send_message("権限がありません。", ephemeral=True)

        if self.server_manager.is_running():
            await self.server_manager.write_stdin(command_str)
            await interaction.response.send_message(f"コマンド送信: `{command_str}`", silent=True)
        else:
             await interaction.response.send_message("サーバーは起動していません。", ephemeral=True)

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
