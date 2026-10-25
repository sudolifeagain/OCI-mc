import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from cogs.status_display import StatusDisplay


class StatusMessageSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_panel_and_notifications_are_not_updated_as_status(self) -> None:
        bot_user = object()
        embed = discord.Embed(title="サーバー参加状況")
        panel = SimpleNamespace(author=bot_user, flags=discord.MessageFlags(is_components_v2=True), embeds=[], edit=AsyncMock())
        notification = SimpleNamespace(author=bot_user, flags=discord.MessageFlags(), embeds=[], edit=AsyncMock())
        status = SimpleNamespace(author=bot_user, flags=discord.MessageFlags(), embeds=[embed], edit=AsyncMock())
        async def history(**kwargs):
            for message in [panel, notification, status]:
                yield message
        channel = SimpleNamespace(history=history, send=AsyncMock())
        cog = StatusDisplay.__new__(StatusDisplay)
        cog.bot = SimpleNamespace(user=bot_user)
        result = await cog.find_or_send_message(channel, embed)
        self.assertIs(result, status)
        panel.edit.assert_not_awaited()
        notification.edit.assert_not_awaited()
        status.edit.assert_awaited_once_with(embed=embed)
        channel.send.assert_not_awaited()
