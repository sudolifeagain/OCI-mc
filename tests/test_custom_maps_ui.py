import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from cogs.custom_maps import CustomMaps
from utils.custom_maps.catalog import Catalog


class LaunchResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_launch_updates_initial_reply_after_success_and_validation_failure(self) -> None:
        for failure in (None, ValueError("Forgeを停止してください。")):
            cog = CustomMaps.__new__(CustomMaps)
            room = {"id": "room", "guild": "1", "channel": "2", "owner": "3"}
            job = {"id": "job", "state": "RUNNING"}
            cog.service = SimpleNamespace(get=AsyncMock(return_value=room), store=SimpleNamespace(create=Mock(return_value=job)), start=AsyncMock(return_value={"id": "session"}, side_effect=failure), put=AsyncMock(), jobs=set())
            cog.bot = SimpleNamespace(get_channel=Mock(return_value=object()))
            cog.permitted, cog.bind_card, cog.update_card = AsyncMock(), AsyncMock(), AsyncMock()
            interaction = SimpleNamespace(user=SimpleNamespace(id=3), response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock())
            await cog.launch(interaction, "room", 1)
            await asyncio.gather(*cog.service.jobs)
            self.assertEqual(job["state"], "FAILED" if failure else "DONE")
            message = interaction.edit_original_response.await_args_list[-1].kwargs["content"]
            self.assertIn("Forgeを停止してください。" if failure else "ワールドを起動しました", message)


class SearchModalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.cog = CustomMaps.__new__(CustomMaps)
        self.cog.service = SimpleNamespace(catalog=Catalog(Path(__file__).resolve().parents[1] / "custom-maps.json"))
        self.cog.search_results = AsyncMock()
        self.cog.error = AsyncMock()
        self.interaction = SimpleNamespace(
            guild_id=1,
            user=SimpleNamespace(id=2),
            response=SimpleNamespace(send_modal=AsyncMock(), defer=AsyncMock()),
        )

    async def form(self, mode: str, filters: dict | None = None):
        self.interaction.response.send_modal.reset_mock()
        await self.cog.search_form(self.interaction, filters or {}, mode)
        return self.interaction.response.send_modal.call_args.args[0]

    async def submit(self, form, selections: dict) -> dict:
        components = copy.deepcopy(form.to_dict()["components"])
        keys = {item.custom_id: key for key, item in form.fields.items()}
        for label in components:
            component = label["component"]
            key = keys[component["custom_id"]]
            options = component.get("options", [])
            defaults = [option["value"] for option in options if option.get("default")]
            if component["type"] == 3:
                component["values"] = selections.get(key, defaults)
            elif component["type"] == 4:
                component["value"] = selections.get(key, component.get("value", ""))
            elif component["type"] == 21:
                component["value"] = selections.get(key, defaults[0] if defaults else None)
            elif component["type"] == 22:
                component["values"] = selections.get(key, defaults)
        form._refresh(self.interaction, components, {})
        self.cog.search_results.reset_mock()
        with patch("cogs.custom_maps.is_allowed_command_context", return_value=True):
            await form.on_submit(self.interaction)
        self.cog.error.assert_not_awaited()
        self.cog.search_results.assert_awaited_once()
        return self.cog.search_results.call_args.args[1]

    async def test_every_search_modal_has_valid_option_values(self) -> None:
        for mode in ("basic", "advanced", "environment"):
            with self.subTest(mode=mode):
                form = await self.form(mode)
                payload = form.to_dict()
                self.assertLessEqual(len(payload["components"]), 5)
                for label in payload["components"]:
                    for option in label["component"].get("options", []):
                        self.assertGreaterEqual(len(option["value"]), 1)
                        self.assertLessEqual(len(option["value"]), 100)
                        self.assertGreaterEqual(len(option["label"]), 1)
                        self.assertLessEqual(len(option["label"]), 100)

    async def test_unspecified_search_options_submit_without_restricting_results(self) -> None:
        cases = {
            "basic": {"players": None, "difficulty_max": None, "max_minutes": None, "keyword": "", "sort": "popular"},
            "advanced": {"source": None, "history": None, "pack": "any", "max_minutes": None, "no_horror": False},
            "environment": {"version": None},
        }
        for mode, expected in cases.items():
            with self.subTest(mode=mode):
                changed = await self.submit(await self.form(mode), {})
                for key, value in expected.items():
                    self.assertEqual(changed[key], value)
                self.assertEqual(len(self.cog.service.catalog.search(changed)), len(self.cog.service.catalog.search({})))

    async def test_selected_conditions_round_trip_through_modal_submission(self) -> None:
        cases = [
            ("basic", {"players": ["4"], "difficulty_max": ["3"], "max_minutes": ["120"], "keyword": "島", "sort": ["short"]}, {"players": 4, "difficulty_max": 3, "max_minutes": 120, "keyword": "島", "sort": "short"}),
            ("advanced", {"source": ["colony"], "history": ["unplayed"], "pack": "none", "minutes": "75", "extra": ["unknown_duration", "no_horror"]}, {"source": "colony", "history": "unplayed", "pack": "none", "max_minutes": 75, "unknown_duration": True, "no_horror": True, "no_combat": False}),
            ("environment", {"version": ["1.21.4"]}, {"version": "1.21.4"}),
        ]
        for mode, selections, expected in cases:
            with self.subTest(mode=mode):
                form = await self.form(mode)
                if mode == "environment" and isinstance(form.fields["version"], discord.ui.TextInput):
                    selections["version"] = selections["version"][0]
                changed = await self.submit(form, selections)
                for key, value in expected.items():
                    self.assertEqual(changed[key], value)

    async def test_all_versions_remain_searchable_when_dropdown_capacity_is_exceeded(self) -> None:
        versions = [f"1.21.{index}" for index in range(30)]
        self.cog.service.catalog = SimpleNamespace(items={version: {"mc_version": version} for version in versions})
        form = await self.form("environment", {"version": "1.21.29"})
        self.assertEqual(form.fields["version"].default, "1.21.29")
        self.assertEqual((await self.submit(form, {"version": " 1.21.29 "}))["version"], "1.21.29")
        self.assertIsNone((await self.submit(await self.form("environment"), {"version": ""}))["version"])
        with self.assertRaisesRegex(ValueError, "バージョン"):
            await form.submit(self.interaction, {"version": "1.99"})
