import json
import logging
import discord
from discord import app_commands
from discord.ext import commands
from settings import CONFIG
from utils.permissions import check_role

STATE_FILE = "reaction_role_state.json"


def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent="\t")


class ReactionRoles(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config = CONFIG.get("reaction_roles", {})
        self.mappings: dict[str, dict] = self.config.get("mappings", {})
        state = _load_state()
        self.message_id: int | None = state.get("message_id")

    def _is_target(self, payload: discord.RawReactionActionEvent) -> bool:
        """監視対象のメッセージへのリアクションか判定する"""
        if not self.message_id:
            return False
        if payload.message_id != self.message_id:
            return False
        if payload.user_id == self.bot.user.id:
            return False
        return str(payload.emoji) in self.mappings

    async def _update_permission(
        self, guild: discord.Guild, user_id: int, channel_id: int, allow: bool
    ) -> bool:
        """チャンネルの閲覧権限を付与または削除する"""
        channel = guild.get_channel(channel_id)
        if not channel:
            return False
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        if not member:
            return False

        if allow:
            overwrite = discord.PermissionOverwrite(view_channel=True)
        else:
            overwrite = None  # オーバーライドを削除して既定に戻す

        await channel.set_permissions(member, overwrite=overwrite)
        return True

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if not self._is_target(payload):
            return
        mapping = self.mappings[str(payload.emoji)]
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        ok = await self._update_permission(guild, payload.user_id, mapping["channel_id"], allow=True)
        if ok:
            logging.info(f"Reaction role: granted access to {mapping['label']} for user {payload.user_id}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if not self._is_target(payload):
            return
        mapping = self.mappings[str(payload.emoji)]
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        ok = await self._update_permission(guild, payload.user_id, mapping["channel_id"], allow=False)
        if ok:
            logging.info(f"Reaction role: revoked access to {mapping['label']} for user {payload.user_id}")

    @app_commands.command(name="reaction-role-setup", description="リアクションロール用メッセージを投稿する")
    async def reaction_role_setup(self, interaction: discord.Interaction) -> None:
        if not check_role(interaction, 'admin'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        channel_id = self.config.get("channel_id")
        if not channel_id:
            return await interaction.response.send_message("reaction_roles.channel_id が未設定です。", ephemeral=True)

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return await interaction.response.send_message(f"チャンネル {channel_id} が見つかりません。", ephemeral=True)

        # メッセージ本文を構築
        lines = ["**チャンネルアクセス**", "", "以下のリアクションで対応チャンネルの閲覧権限を取得できます。リアクションを外すとアクセスが解除されます。", ""]
        for emoji, info in self.mappings.items():
            lines.append(f"{emoji} → <#{info['channel_id']}> ({info['label']})")

        await interaction.response.defer(ephemeral=True)
        msg = await channel.send("\n".join(lines))

        # リアクションを追加
        for emoji in self.mappings:
            await msg.add_reaction(emoji)

        # message_id を保存
        self.message_id = msg.id
        _save_state({"message_id": msg.id})

        await interaction.followup.send(f"投稿完了 (message_id: {msg.id})", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))
