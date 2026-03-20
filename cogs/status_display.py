import logging
import os
import discord
import psutil
from discord.ext import commands, tasks
from settings import STATUS_CHANNEL_ID
import time
from datetime import datetime
import zoneinfo

JST = zoneinfo.ZoneInfo("Asia/Tokyo")


def get_machine_stats() -> str:
    """マシン全体のCPU/メモリ/ロードアベレージを1行で返す"""
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    mem_used_gb = mem.used / (1024 ** 3)
    mem_total_gb = mem.total / (1024 ** 3)
    try:
        load = os.getloadavg()[0]
        load_str = f" | Load: {load:.2f}"
    except (AttributeError, OSError):
        load_str = ""
    return f"CPU: {cpu}% | Mem: {mem_used_gb:.1f}/{mem_total_gb:.1f} GB ({mem.percent}%){load_str}"


def format_process_stats(stats: dict) -> str:
    """プロセス統計を1行で返す"""
    mem_gb = stats['memory_mb'] / 1024
    seconds = int(stats['uptime_seconds'])
    m, _ = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"CPU: {stats['cpu_percent']}% | Mem: {mem_gb:.1f} GB | Up: {h}h {m}m"


class StatusDisplay(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.message = None
        # Start loop only if channel ID is set
        if STATUS_CHANNEL_ID:
            self.update_loop.start()

    def cog_unload(self):
        self.update_loop.cancel()

    @tasks.loop(minutes=3)
    async def update_loop(self):
        channel = self.bot.get_channel(STATUS_CHANNEL_ID)
        if not channel:
            return

        now = datetime.now(JST)
        machine = get_machine_stats()
        description = f"最終更新: {now.strftime('%Y/%m/%d %H:%M:%S')} (JST)\n{machine}"

        embed = discord.Embed(
            title="サーバー参加状況",
            description=description,
            color=0x00ff00
        )

        for server_id, server in self.bot.server_manager.servers.items():
            if not server.is_running():
                embed.add_field(name=f"{server.name} (停止中)", value="Offline", inline=False)
                continue

            lines = []

            # プロセス統計
            stats = server.get_stats()
            if stats:
                lines.append(format_process_stats(stats))

            players = server.online_players
            if players:
                sorted_players = sorted(players.items(), key=lambda item: item[1])
                for name, join_time in sorted_players:
                    elapsed_seconds = int(time.time() - join_time)
                    lines.append(f"👤 **{name}** ({self.format_duration(elapsed_seconds)})")
            else:
                lines.append("参加者なし")

            embed.add_field(
                name=f"{server.name} (稼働中・{len(players)}人)",
                value="\n".join(lines),
                inline=False
            )

        # メッセージの送信または編集
        if self.message:
            try:
                await self.message.edit(embed=embed)
            except discord.NotFound:
                self.message = await self.find_or_send_message(channel, embed)
        else:
            self.message = await self.find_or_send_message(channel, embed)

    async def find_or_send_message(self, channel, embed):
        """チャンネル内の最新の自分のメッセージを探すか、新規送信する"""
        try:
            async for msg in channel.history(limit=20):
                if msg.author == self.bot.user:
                    await msg.edit(embed=embed)
                    return msg
        except Exception as e:
            logging.warning(f"Failed to fetch channel history: {e}")

        return await channel.send(embed=embed, silent=True)

    def format_duration(self, seconds):
        """秒数を 1h 23m 形式にフォーマット"""
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}時間 {m}分"
        return f"{m}分"

    @update_loop.before_loop
    async def before_update_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(StatusDisplay(bot))
