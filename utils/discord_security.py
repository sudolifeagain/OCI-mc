import re

import discord

from settings import (
    DISCORD_GUILD_IDS,
    DISCORD_SHELL_CHANNEL_IDS,
    DISCORD_SHELL_USER_IDS,
)


PLAYER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


def _interaction_channel_ids(interaction: discord.Interaction) -> set[int]:
    """スレッドの親を含む実行チャンネルIDを返す。"""
    ids = {interaction.channel_id} if interaction.channel_id else set()
    parent_id = getattr(interaction.channel, "parent_id", None)
    if parent_id:
        ids.add(parent_id)
    return ids


def is_allowed_command_context(
    interaction: discord.Interaction,
    *,
    shell: bool = False,
) -> bool:
    """コマンド種別に応じたguild/channelからの実行か確認する。"""
    if interaction.guild_id is None:
        return False
    if DISCORD_GUILD_IDS and interaction.guild_id not in DISCORD_GUILD_IDS:
        return False

    if not shell:
        return True

    if not DISCORD_SHELL_CHANNEL_IDS:
        return False
    return bool(_interaction_channel_ids(interaction) & DISCORD_SHELL_CHANNEL_IDS)


def is_shell_user(interaction: discord.Interaction) -> bool:
    """任意OSコマンドを許可されたDiscordユーザーか確認する。"""
    return interaction.user.id in DISCORD_SHELL_USER_IDS


def validate_player_name(player_name: str) -> str | None:
    """コンソールコマンドへ安全に渡せるプレイヤー名を返す。"""
    normalized = player_name.strip()
    if not PLAYER_NAME_PATTERN.fullmatch(normalized):
        return None
    return normalized


def escape_discord_code_block(value: str) -> str:
    """外部出力がDiscordのコードブロックを閉じないようにする。"""
    return value.replace("`", "\u02cb")
