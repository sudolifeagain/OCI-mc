import asyncio
import logging
import discord
from discord import app_commands
from discord.ext import commands
from utils.permissions import check_role

TMUX_SESSION = "claude"
# .bashrc は tmux の非対話起動で読み込まれないため、bun の PATH を明示的に設定する
CLAUDE_CMD = 'export PATH=\\"$HOME/.bun/bin:$HOME/.local/bin:$PATH\\" && claude --channels plugin:discord@claude-plugins-official --dangerously-skip-permissions'
CMD_TIMEOUT = 30


class ClaudeManager(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._restarting = False

    async def _run(self, cmd: str, timeout: int = CMD_TIMEOUT) -> tuple[int, str]:
        """シェルコマンドを実行し (returncode, stdout) を返す"""
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "コマンドがタイムアウトしました"
        return proc.returncode, stdout.decode().strip()

    async def _is_running(self) -> bool:
        """Claude Code の tmux セッションが存在するか"""
        rc, _ = await self._run(f"tmux has-session -t {TMUX_SESSION} 2>/dev/null")
        return rc == 0

    async def _stop_claude(self) -> bool:
        """Claude Code セッションを停止する"""
        if not await self._is_running():
            return True
        await self._run(f"tmux kill-session -t {TMUX_SESSION}")
        await asyncio.sleep(2)
        return not await self._is_running()

    async def _start_claude(self) -> bool:
        """Claude Code セッションを開始する

        起動後、Claude の対話プロンプト（信頼確認 → 権限バイパス確認）を
        キーシーケンスで自動承認する。プロンプト構成変更時は要修正。
        """
        if await self._is_running():
            return True
        await self._run(f'tmux new-session -d -s {TMUX_SESSION} "{CLAUDE_CMD}"')
        await asyncio.sleep(5)
        # 信頼確認の自動承認
        await self._run(f"tmux send-keys -t {TMUX_SESSION} Enter")
        await asyncio.sleep(3)
        # バイパス権限の自動承認（Down → Enter）
        await self._run(f"tmux send-keys -t {TMUX_SESSION} Down")
        await asyncio.sleep(0.5)
        await self._run(f"tmux send-keys -t {TMUX_SESSION} Enter")
        await asyncio.sleep(5)
        return await self._is_running()

    @app_commands.command(name="claude-restart", description="Claude Codeセッションを再起動する（コンテキストリセット＋自動更新適用）")
    async def claude_restart(self, interaction: discord.Interaction) -> None:
        if not check_role(interaction, 'claude'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        if self._restarting:
            return await interaction.response.send_message("再起動処理中です。", ephemeral=True)

        self._restarting = True
        await interaction.response.send_message("Claude Code を再起動します...")

        try:
            # 停止
            stopped = await self._stop_claude()
            if not stopped:
                await interaction.followup.send("停止に失敗しました。")
                return

            # 更新チェック
            rc, output = await self._run("claude update 2>&1", timeout=60)
            update_msg = output[:1500] if output else "更新なし"
            await interaction.followup.send(f"更新チェック: {update_msg}")

            # 起動
            started = await self._start_claude()
            if started:
                await interaction.followup.send("Claude Code を再起動しました。コンテキストはリセットされています。")
            else:
                await interaction.followup.send("起動に失敗しました。サーバーを確認してください。")
        except Exception as e:
            logging.error(f"Claude restart failed: {e}")
            await interaction.followup.send(f"エラー: {e}")
        finally:
            self._restarting = False

    @app_commands.command(name="claude-status", description="Claude Codeセッションの状態を確認する")
    async def claude_status(self, interaction: discord.Interaction) -> None:
        if not check_role(interaction, 'claude'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        await interaction.response.defer()

        running = await self._is_running()
        rc, version = await self._run("claude --version 2>/dev/null")

        if running:
            _, pane = await self._run(f"tmux capture-pane -t {TMUX_SESSION} -p 2>/dev/null | tail -5")
            status = f"**状態**: オンライン\n**バージョン**: {version}\n```\n{pane}\n```"
        else:
            status = f"**状態**: オフライン\n**バージョン**: {version}"

        await interaction.followup.send(status)

    @app_commands.command(name="claude-stop", description="Claude Codeセッションを停止する")
    async def claude_stop(self, interaction: discord.Interaction) -> None:
        if not check_role(interaction, 'claude'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        await interaction.response.defer()
        stopped = await self._stop_claude()
        if stopped:
            await interaction.followup.send("Claude Code を停止しました。")
        else:
            await interaction.followup.send("停止に失敗しました。")

    @app_commands.command(name="claude-start", description="Claude Codeセッションを開始する")
    async def claude_start(self, interaction: discord.Interaction) -> None:
        if not check_role(interaction, 'claude'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        if await self._is_running():
            return await interaction.response.send_message("既に起動しています。")

        await interaction.response.send_message("Claude Code を起動します...")
        started = await self._start_claude()
        if started:
            await interaction.followup.send("Claude Code を起動しました。")
        else:
            await interaction.followup.send("起動に失敗しました。")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ClaudeManager(bot))
