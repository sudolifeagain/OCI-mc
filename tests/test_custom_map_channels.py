from types import SimpleNamespace
import unittest
from unittest.mock import patch

import settings
from utils.discord_security import is_custom_map_channel


class CustomMapChannelTests(unittest.TestCase):
    def test_configured_channel_rejects_other_channels(self) -> None:
        with patch("utils.discord_security.CUSTOM_MAP_CHANNEL_ID", 123):
            self.assertTrue(is_custom_map_channel(SimpleNamespace(channel_id=123)))
            self.assertFalse(is_custom_map_channel(SimpleNamespace(channel_id=456)))

    def test_unconfigured_custom_logs_never_fall_back_to_regular_management_channel(self) -> None:
        with patch.dict(settings.LOG_CHANNEL_IDS, {"custom-1": 0, "custom-2": 0}), patch("settings.CHANNEL_ID", 123):
            self.assertEqual(settings.get_log_channel_id("custom-1"), 0)
            self.assertEqual(settings.get_log_channel_id("custom-2"), 0)

    def test_custom_logs_use_their_dedicated_destination(self) -> None:
        with patch.dict(settings.LOG_CHANNEL_IDS, {"custom-1": 456, "custom-2": 456, "paper": 789}):
            self.assertEqual(settings.get_log_channel_id("custom-1"), 456)
            self.assertEqual(settings.get_log_channel_id("custom-2"), 456)
            self.assertEqual(settings.get_log_channel_id("paper"), 789)
