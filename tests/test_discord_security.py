import unittest
from types import SimpleNamespace
from unittest.mock import patch

from utils.discord_security import (
    escape_discord_code_block,
    is_allowed_command_context,
    is_shell_user,
    validate_player_name,
)


class DiscordSecurityTests(unittest.TestCase):
    def test_command_context_accepts_guild_channel(self) -> None:
        interaction = SimpleNamespace(
            guild_id=10,
            channel_id=31,
            channel=SimpleNamespace(parent_id=30),
            user=SimpleNamespace(id=40),
        )
        with patch("utils.discord_security.DISCORD_GUILD_IDS", {10}):
            self.assertTrue(is_allowed_command_context(interaction))

    def test_command_context_rejects_dm_and_other_guild(self) -> None:
        dm = SimpleNamespace(
            guild_id=None,
            channel_id=30,
            channel=SimpleNamespace(parent_id=None),
        )
        other_guild = SimpleNamespace(
            guild_id=11,
            channel_id=30,
            channel=SimpleNamespace(parent_id=None),
        )
        with patch("utils.discord_security.DISCORD_GUILD_IDS", {10}):
            self.assertFalse(is_allowed_command_context(dm))
            self.assertFalse(is_allowed_command_context(other_guild))

    def test_shell_context_accepts_thread_parent(self) -> None:
        interaction = SimpleNamespace(
            guild_id=10,
            channel_id=31,
            channel=SimpleNamespace(parent_id=30),
        )
        with (
            patch("utils.discord_security.DISCORD_GUILD_IDS", {10}),
            patch("utils.discord_security.DISCORD_SHELL_CHANNEL_IDS", {30}),
        ):
            self.assertTrue(is_allowed_command_context(interaction, shell=True))

    def test_shell_context_rejects_other_channel(self) -> None:
        interaction = SimpleNamespace(
            guild_id=10,
            channel_id=31,
            channel=SimpleNamespace(parent_id=None),
        )
        with (
            patch("utils.discord_security.DISCORD_GUILD_IDS", {10}),
            patch("utils.discord_security.DISCORD_SHELL_CHANNEL_IDS", {30}),
        ):
            self.assertFalse(is_allowed_command_context(interaction, shell=True))

    def test_shell_context_fails_closed_without_channel_configuration(self) -> None:
        interaction = SimpleNamespace(
            guild_id=10,
            channel_id=30,
            channel=SimpleNamespace(parent_id=None),
        )
        with patch("utils.discord_security.DISCORD_SHELL_CHANNEL_IDS", set()):
            self.assertFalse(is_allowed_command_context(interaction, shell=True))

    def test_shell_requires_exact_user(self) -> None:
        allowed = SimpleNamespace(user=SimpleNamespace(id=40))
        denied = SimpleNamespace(user=SimpleNamespace(id=41))
        with patch("utils.discord_security.DISCORD_SHELL_USER_IDS", {40}):
            self.assertTrue(is_shell_user(allowed))
            self.assertFalse(is_shell_user(denied))

    def test_player_name_rejects_command_injection(self) -> None:
        self.assertEqual(validate_player_name("Valid_Player-1"), "Valid_Player-1")
        self.assertIsNone(validate_player_name("player\nop attacker"))
        self.assertIsNone(validate_player_name("player name"))

    def test_code_fence_is_neutralized(self) -> None:
        self.assertNotIn("```", escape_discord_code_block("before```@everyone"))


if __name__ == "__main__":
    unittest.main()
