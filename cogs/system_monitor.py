"""
System Monitor Cog
OOM Kill などのシステムイベントを監視し、Discord に通知する
"""
import asyncio
import subprocess
import logging
import re
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks
from settings import CHANNEL_ID, DISCORD_OWNER_ID


class SystemMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.last_oom_kernel_time = None  # 最後に検知した OOM イベントのカーネルタイムスタンプ
        self.oom_checker.start()

    def cog_unload(self):
        self.oom_checker.cancel()

    async def get_boot_time(self) -> datetime:
        """システム起動時刻を取得"""
        try:
            proc = await asyncio.create_subprocess_exec(
                'ssh', '-i', '/home/ubuntu/.ssh/id_ed25519', 'ubuntu@localhost',
                'cat', '/proc/uptime',
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            uptime_seconds = float(stdout.decode().split()[0])
            boot_time = datetime.now() - timedelta(seconds=uptime_seconds)
            return boot_time
        except Exception as e:
            logging.warning(f"Failed to get boot time: {e}")
            return datetime.now()

    async def check_dmesg_for_oom(self) -> list[dict]:
        """dmesg から OOM Kill イベントを取得"""
        try:
            proc = await asyncio.create_subprocess_exec(
                'sudo', 'dmesg',
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logging.warning(f"dmesg failed: {stderr.decode()}")
                return []

            output = stdout.decode()
            oom_events = []

            # OOM Kill ログを検索
            # パターン: [タイムスタンプ] Out of memory: Killed process PID (プロセス名) ...
            pattern = r'\[\s*(\d+\.\d+)\].*Out of memory: Killed process (\d+) \(([^)]+)\).*total-vm:(\d+)kB.*anon-rss:(\d+)kB'

            for match in re.finditer(pattern, output, re.IGNORECASE):
                kernel_time = float(match.group(1))
                pid = match.group(2)
                process_name = match.group(3)
                total_vm_kb = int(match.group(4))
                rss_kb = int(match.group(5))

                oom_events.append({
                    'kernel_time': kernel_time,
                    'pid': pid,
                    'process': process_name,
                    'total_vm_mb': total_vm_kb // 1024,
                    'rss_mb': rss_kb // 1024,
                })

            return oom_events

        except Exception as e:
            logging.error(f"Error checking dmesg: {e}")
            return []

    def kernel_time_to_datetime(self, kernel_time: float, boot_time: datetime) -> datetime:
        """カーネルタイムスタンプを実際の日時に変換"""
        return boot_time + timedelta(seconds=kernel_time)

    @tasks.loop(seconds=60)
    async def oom_checker(self):
        """OOM Kill イベントを定期的にチェック"""
        channel = self.bot.get_channel(CHANNEL_ID)
        if not channel:
            return

        oom_events = await self.check_dmesg_for_oom()
        if not oom_events:
            return

        # 初回実行時は現在の最新タイムスタンプを記録するだけ
        if self.last_oom_kernel_time is None:
            self.last_oom_kernel_time = max(e['kernel_time'] for e in oom_events)
            logging.info(f"OOM checker initialized. Latest kernel time: {self.last_oom_kernel_time}")
            return

        # 新しいイベントのみ抽出
        new_events = [e for e in oom_events if e['kernel_time'] > self.last_oom_kernel_time]
        if not new_events:
            return

        # 最新のタイムスタンプを更新
        self.last_oom_kernel_time = max(e['kernel_time'] for e in new_events)

        # 起動時刻を取得して実際の時刻を計算
        try:
            proc = await asyncio.create_subprocess_exec(
                'cat', '/proc/uptime',
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            uptime_seconds = float(stdout.decode().split()[0])
            boot_time = datetime.now() - timedelta(seconds=uptime_seconds)
        except Exception:
            boot_time = datetime.now() - timedelta(seconds=self.last_oom_kernel_time)

        # Discord に通知
        for event in new_events:
            event_time = self.kernel_time_to_datetime(event['kernel_time'], boot_time)

            embed = discord.Embed(
                title="⚠️ OOM Kill 検知",
                description="メモリ不足によりプロセスが強制終了されました",
                color=discord.Color.red(),
                timestamp=event_time
            )
            embed.add_field(name="プロセス", value=f"`{event['process']}`", inline=True)
            embed.add_field(name="PID", value=event['pid'], inline=True)
            embed.add_field(name="使用メモリ (RSS)", value=f"{event['rss_mb']:,} MB", inline=True)
            embed.add_field(name="仮想メモリ", value=f"{event['total_vm_mb']:,} MB", inline=True)
            embed.set_footer(text="OOM Killer")

            try:
                mention = f"<@{DISCORD_OWNER_ID}>" if DISCORD_OWNER_ID else ""
                await channel.send(content=mention, embed=embed, silent=True)
                logging.info(f"Sent OOM alert for process {event['process']} (PID: {event['pid']})")
            except Exception as e:
                logging.error(f"Failed to send OOM alert: {e}")

    @oom_checker.before_loop
    async def before_oom_checker(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(SystemMonitor(bot))
