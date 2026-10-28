from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from cogs.basic_control import BasicControl


class CustomMapWhitelistCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.service = SimpleNamespace(add_whitelist_player=AsyncMock(return_value=("Player", [])))
        self.cog = BasicControl.__new__(BasicControl)
        self.cog.bot = SimpleNamespace(custom_maps=self.service)
        self.interaction = SimpleNamespace(
            user=SimpleNamespace(id=1),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def test_existing_whitelist_permission_is_required(self) -> None:
        with patch("cogs.basic_control.is_allowed_command_context", return_value=True), patch("cogs.basic_control.check_role", return_value=False):
            await BasicControl.whitelist_add.callback(self.cog, self.interaction, "Player", "custom-maps")
        self.service.add_whitelist_player.assert_not_awaited()
        self.interaction.response.send_message.assert_awaited_once()

    async def test_custom_map_addition_does_not_require_a_running_server(self) -> None:
        with patch("cogs.basic_control.is_allowed_command_context", return_value=True), patch("cogs.basic_control.check_role", return_value=True):
            await BasicControl.whitelist_add.callback(self.cog, self.interaction, " Player ", "custom-maps")
        self.service.add_whitelist_player.assert_awaited_once_with("Player")
        self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        self.interaction.edit_original_response.assert_awaited_once()
