import asyncio
import logging
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands
from utils.permissions import check_role

TMUX_SESSION = "claude"
# .bashrc は非対話シェルで読み込まれないため、claude / bun の PATH を明示的に設定する
_PATH_SETUP = 'export PATH="$HOME/.bun/bin:$HOME/.local/bin:$PATH"'
# tmux 用: 外側の二重引用符（line 53）に包まれるため内側をエスケープ
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

    async def _patch_discord_plugin(self) -> None:
        """Discord プラグインにカスタムパッチを適用する（冪等）

        プラグイン更新でキャッシュが上書きされるため、起動前に毎回適用する。
        パッチ内容は scripts/patch_discord_plugin.sh を参照。
        """
        script = Path(__file__).resolve().parent.parent / "scripts" / "patch_discord_plugin.sh"
        patch = f"bash {script}"
        rc, output = await self._run(patch)
        if rc != 0:
            logging.warning(f"Discord plugin patch failed: {output}")

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
        await interaction.response.send_message("Claude Code を再起動します...", silent=True)

        try:
            # 停止
            stopped = await self._stop_claude()
            if not stopped:
                await interaction.followup.send("停止に失敗しました。", silent=True)
                return

            # 更新チェック
            rc, output = await self._run(f'{_PATH_SETUP} && claude update 2>&1', timeout=60)
            update_msg = output[:1500] if output else "更新なし"
            await interaction.followup.send(f"更新チェック: {update_msg}", silent=True)

            # プラグインパッチ適用 → 起動
            await self._patch_discord_plugin()
            started = await self._start_claude()
            if started:
                await interaction.followup.send("Claude Code を再起動しました。コンテキストはリセットされています。", silent=True)
            else:
                await interaction.followup.send("起動に失敗しました。サーバーを確認してください。", silent=True)
        except Exception as e:
            logging.error(f"Claude restart failed: {e}")
            await interaction.followup.send(f"エラー: {e}", silent=True)
        finally:
            self._restarting = False

    @app_commands.command(name="claude-status", description="Claude Codeセッションの状態を確認する")
    async def claude_status(self, interaction: discord.Interaction) -> None:
        if not check_role(interaction, 'claude'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        await interaction.response.defer()

        running = await self._is_running()
        rc, version = await self._run(f'{_PATH_SETUP} && claude --version 2>/dev/null')

        if running:
            _, pane = await self._run(f"tmux capture-pane -t {TMUX_SESSION} -p 2>/dev/null | tail -5")
            status = f"**状態**: オンライン\n**バージョン**: {version}\n```\n{pane}\n```"
        else:
            status = f"**状態**: オフライン\n**バージョン**: {version}"

        await interaction.followup.send(status, silent=True)

    @app_commands.command(name="claude-stop", description="Claude Codeセッションを停止する")
    async def claude_stop(self, interaction: discord.Interaction) -> None:
        if not check_role(interaction, 'claude'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        await interaction.response.defer()
        stopped = await self._stop_claude()
        if stopped:
            await interaction.followup.send("Claude Code を停止しました。", silent=True)
        else:
            await interaction.followup.send("停止に失敗しました。", silent=True)

    @app_commands.command(name="claude-start", description="Claude Codeセッションを開始する")
    async def claude_start(self, interaction: discord.Interaction) -> None:
        if not check_role(interaction, 'claude'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        if await self._is_running():
            return await interaction.response.send_message("既に起動しています。", silent=True)

        await interaction.response.send_message("Claude Code を起動します...", silent=True)
        await self._patch_discord_plugin()
        started = await self._start_claude()
        if started:
            await interaction.followup.send("Claude Code を起動しました。", silent=True)
        else:
            await interaction.followup.send("起動に失敗しました。", silent=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ClaudeManager(bot))
