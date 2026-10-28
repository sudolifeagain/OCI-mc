import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs.status_display import StatusDisplay


class StatusMessageSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_status_excludes_custom_map_slots(self) -> None:
        cog = StatusDisplay.__new__(StatusDisplay)
        regular = SimpleNamespace(config={}, name="Paper", is_running=Mock(return_value=False))
        custom = SimpleNamespace(config={"workload_type": "custom_map"}, is_running=Mock(side_effect=AssertionError("専用枠を通常表示へ含めない")))
        cog.bot = SimpleNamespace(get_channel=Mock(return_value=object()), server_manager=SimpleNamespace(servers={"paper": regular, "custom-1": custom}))
        cog.message = SimpleNamespace(edit=AsyncMock())
        with patch("cogs.status_display.get_machine_stats", return_value=""):
            await StatusDisplay.update_loop.coro(cog)
        self.assertEqual([field.name for field in cog.message.edit.call_args.kwargs["embed"].fields], ["Paper (停止中)"])

    async def test_custom_panel_and_notifications_are_not_updated_as_status(self) -> None:
        bot_user = object()
        embed = discord.Embed(title="サーバー参加状況")
        panel = SimpleNamespace(author=bot_user, content="", flags=discord.MessageFlags(components_v2=True), embeds=[], edit=AsyncMock())
        notification = SimpleNamespace(author=bot_user, content="", flags=discord.MessageFlags(), embeds=[], edit=AsyncMock())
        access_message = SimpleNamespace(author=bot_user, content="チャンネルアクセス", flags=discord.MessageFlags(), embeds=[embed], edit=AsyncMock())
        status = SimpleNamespace(author=bot_user, content="", flags=discord.MessageFlags(), embeds=[embed], edit=AsyncMock())
        async def history(**kwargs):
            for message in [panel, notification, access_message, status]:
                yield message
        channel = SimpleNamespace(history=history, send=AsyncMock())
        cog = StatusDisplay.__new__(StatusDisplay)
        cog.bot = SimpleNamespace(user=bot_user)
        result = await cog.find_or_send_message(channel, embed)
        self.assertIs(result, status)
        panel.edit.assert_not_awaited()
        notification.edit.assert_not_awaited()
        access_message.edit.assert_not_awaited()
        status.edit.assert_awaited_once_with(embed=embed)
        channel.send.assert_not_awaited()
