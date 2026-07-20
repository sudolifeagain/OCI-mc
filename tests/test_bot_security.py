import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from bot import SecureCommandTree


class SecureCommandTreeTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_command_receives_ephemeral_response(self) -> None:
        response = SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
        )
        interaction = SimpleNamespace(
            command=SimpleNamespace(name="start"),
            guild_id=10,
            channel_id=20,
            user=SimpleNamespace(id=30),
            type=discord.InteractionType.application_command,
            response=response,
        )

        with patch("bot.is_allowed_command_context", return_value=False):
            allowed = await SecureCommandTree.interaction_check(None, interaction)

        self.assertFalse(allowed)
        response.send_message.assert_awaited_once_with(
            "このサーバーまたはチャンネルではコマンドを実行できない。",
            ephemeral=True,
        )

    async def test_allowed_command_continues_without_response(self) -> None:
        response = SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
        )
        interaction = SimpleNamespace(
            command=SimpleNamespace(name="status"),
            guild_id=10,
            channel_id=20,
            user=SimpleNamespace(id=30),
            type=discord.InteractionType.application_command,
            response=response,
        )

        with patch("bot.is_allowed_command_context", return_value=True):
            allowed = await SecureCommandTree.interaction_check(None, interaction)

        self.assertTrue(allowed)
        response.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
