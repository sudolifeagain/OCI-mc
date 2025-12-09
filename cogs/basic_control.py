import asyncio
import subprocess
import logging
from discord.ext import commands, tasks
from settings import CONFIG, DISCORD_OWNER_ID, CHANNEL_ID, check_role, check_whitelist_add_permission

class BasicControl(commands.Cog):
    def __init__(self, bot, server_manager):
        self.bot = bot
        self.server_manager = server_manager
        self.discord_log_sender.start()

    def cog_unload(self):
        self.discord_log_sender.cancel()

    @commands.command()
    async def start(self, ctx):
        if not check_role(ctx, 'start'): return await ctx.send("権限がありません。")
        
        if self.server_manager.is_running():
            return await ctx.send("サーバーは既に起動しています。")
            
        await ctx.send("起動コマンドを送信しました。")
        await self.server_manager.start_server()

    @commands.command()
    async def stop(self, ctx):
        if not check_role(ctx, 'stop'): return await ctx.send("権限がありません。")
        
        if self.server_manager.is_running():
            await self.server_manager.stop_server()
            await ctx.send("停止コマンドを送信しました。")
        else:
            await ctx.send("サーバーは起動していません。")

    @commands.command()
    async def cmd(self, ctx, *, command_str):
        # 権限チェック (check_role および check_whitelist_add_permission)
        if not check_role(ctx, 'command'):
            # 特例: userロール かつ whitelist add
            if not check_whitelist_add_permission(ctx, command_str):
                return await ctx.send("権限がありません。")

        if self.server_manager.is_running():
            await self.server_manager.write_stdin(command_str)
            await ctx.send(f"コマンド送信: `{command_str}`")
        else:
             await ctx.send("サーバーは起動していません。")

    @commands.command(name='shell')
    async def shell(self, ctx, *, command_str):
        """Execute an arbitrary shell command on the local instance. Owner-only."""
        if not DISCORD_OWNER_ID or ctx.author.id != DISCORD_OWNER_ID:
            return await ctx.send("権限がありません。Ownerのみが使用できます。")

        logging.info(f"Owner {ctx.author} ({ctx.author.id}) executed shell: {command_str}")
        await ctx.send(f"実行中: `{command_str}`")

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
                await ctx.send("コマンドがタイムアウトしました（60秒）")
                return
            out_text = stdout.decode('utf-8', errors='ignore')
            if not out_text:
                await ctx.send("(出力なし)")
                return

            while out_text:
                chunk = out_text[:1900]
                out_text = out_text[1900:]
                await ctx.send(f"```{chunk}```")

        except Exception as e:
            logging.error(f"shell command exec error: {e}")
            await ctx.send(f"実行中にエラーが発生しました: {e}")

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
                    await channel.send(f"```{chunk}```")
                except Exception as e:
                    print(f"Log send error: {e}")
            
    @discord_log_sender.before_loop
    async def before_discord_log_sender(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    # ServerManager is expected to be passed via bot instance or global
    # For now, let's assume it's attached to bot
    await bot.add_cog(BasicControl(bot, bot.server_manager))
