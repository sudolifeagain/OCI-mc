import discord
from discord.ext import commands, tasks
from settings import STATUS_CHANNEL_ID
import time
from datetime import datetime
import zoneinfo

JST = zoneinfo.ZoneInfo("Asia/Tokyo")

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
        embed = discord.Embed(
            title="🔴 サーバー参加状況", 
            description=f"最終更新: {now.strftime('%Y/%m/%d %H:%M:%S')} (JST)",
            color=0x00ff00
        )
        
        for server_id, server in self.bot.server_manager.servers.items():
            if not server.is_running():
                embed.add_field(name=f"❌ {server.name}", value="Offline", inline=False)
                continue

            lines = []
            players = server.online_players
            if players:
                # 参加時間の古い順（長く居る順）にソート
                sorted_players = sorted(players.items(), key=lambda item: item[1])
                
                for name, join_time in sorted_players:
                    elapsed_seconds = int(time.time() - join_time)
                    lines.append(f"👤 **{name}** ({self.format_duration(elapsed_seconds)})")
                value = "\n".join(lines)
            else:
                value = "参加者なし"

            embed.add_field(name=f"✅ {server.name} ({len(players)}人)", value=value, inline=False)

        # メッセージの送信または編集
        if self.message:
            try:
                await self.message.edit(embed=embed)
            except discord.NotFound:
                # メッセージが消されていた場合は再検索または新規送信
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
        except Exception:
            pass # 履歴取得エラー時は新規送信
            
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
