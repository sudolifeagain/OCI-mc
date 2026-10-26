"""Components V2によるカスタムマップの共同プレイ管理。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Callable, Awaitable

import discord
from discord import app_commands
from discord.ext import commands, tasks
import requests

from settings import CONFIG, SERVER_RUNTIME_DIR
from utils.custom_maps.catalog import summary
from utils.custom_maps.service import CustomMapService
from utils.discord_security import is_allowed_command_context
from utils.permissions import check_role

log = logging.getLogger(__name__)
LEVELS = ["指定なし", "入門", "やさしいまで", "標準まで", "難しいまで"]
SORTS = {"popular": "人気順", "short": "短い順", "long": "長い順", "easy": "やさしい順", "hard": "難しい順", "many": "対応人数が多い順", "few": "対応人数が少ない順", "recent": "最近自分が遊んだ順"}
STATES = {"OPEN": "参加準備中", "PREPARING": "ワールド作成中", "STARTING": "起動確認中", "RUNNING": "プレイ可能", "SAVING": "保存停止中", "PAUSED": "中断中", "COMPLETED": "クリア申告済み", "FAILED": "確認が必要", "DELETING": "片づけ中", "DELETED": "消去済み"}
UNSPECIFIED_OPTION = "__unspecified__"


def safe(text: object) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


def select(values: list[tuple[str, str]], current: object = "") -> discord.ui.Select:
    # Discordは空の選択値を受理しない。フォーム送信時に検索条件の空値へ戻す。
    return discord.ui.Select(options=[discord.SelectOption(label=label[:100], value=value or UNSPECIFIED_OPTION, default=value == str(current)) for value, label in values[:25]])


class Screen(discord.ui.LayoutView):
    def __init__(self, cog: CustomMaps, *, owner: int | None = None):
        super().__init__(timeout=None if owner is None else 900)
        self.cog, self.owner = cog, owner
        self.expires = time.time() + 900
        self.box = discord.ui.Container()
        self.add_item(self.box)

    def text(self, content: str) -> None:
        self.box.add_item(discord.ui.TextDisplay(content[:3500]))

    def button(self, label: str, callback: Callable[[discord.Interaction], Awaitable], ident: str, *, style=discord.ButtonStyle.secondary, disabled: bool = False) -> discord.ui.Button:
        button = discord.ui.Button(label=label[:80], custom_id=ident, style=style, disabled=disabled)
        button.callback = callback
        return button

    def row(self, *items: discord.ui.Item) -> None:
        self.box.add_item(discord.ui.ActionRow(*items))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_allowed_command_context(interaction):
            await interaction.response.send_message("許可されたDiscordサーバー内で操作する必要がある。", ephemeral=True)
            return False
        if self.owner is not None and (interaction.user.id != self.owner or time.time() > self.expires):
            await interaction.response.send_message("本人用画面の期限が切れた。/escape home から再表示できる。", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await self.cog.error(interaction, error)


class Form(discord.ui.Modal):
    def __init__(self, title: str, cog: CustomMaps, owner: int, submit: Callable):
        super().__init__(title=title[:45], timeout=900)
        self.cog, self.owner, self.submit = cog, owner, submit
        self.fields: dict[str, discord.ui.Item] = {}

    def field(self, key: str, label: str, item: discord.ui.Item, description: str | None = None) -> None:
        self.fields[key] = item
        self.add_item(discord.ui.Label(text=label[:45], component=item, description=description))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.owner != interaction.user.id or not is_allowed_command_context(interaction):
            return await interaction.response.send_message("このフォームを操作できない。", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            values = {}
            for key, item in self.fields.items():
                values[key] = item.value if hasattr(item, "value") else list(item.values)
                if isinstance(item, discord.ui.Select):
                    values[key] = ["" if value == UNSPECIFIED_OPTION else value for value in values[key]]
            await self.submit(interaction, values)
        except Exception as error:
            await self.cog.error(interaction, error)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await self.cog.error(interaction, error)


class CustomMaps(commands.GroupCog, group_name="escape", group_description="カスタムマップを探す・準備する・途中から再開する"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.service = CustomMapService(CONFIG.get("custom_maps", {}), bot.server_manager, Path(SERVER_RUNTIME_DIR))
        bot.custom_maps = self.service
        self.simple_users: set[int] = set()
        self.rendered: dict[str, str] = {}
        self.refresh.start()

    async def cog_load(self) -> None:
        await self.service.reconcile()
        for binding in await self.service.rows("message"):
            try:
                if binding["type"] == "panel":
                    view = self.panel_view()
                else:
                    view = await self.shared_view(binding["type"], binding["target"])
                self.bot.add_view(view, message_id=int(binding["message_id"]))
            except (ValueError, KeyError):
                log.warning("カスタムマップの旧カードを復元できない: %s", binding["id"])

    def cog_unload(self) -> None:
        self.refresh.cancel()

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await self.error(interaction, getattr(error, "original", error))

    async def error(self, interaction: discord.Interaction, error: Exception) -> None:
        if not isinstance(error, ValueError):
            log.exception("カスタムマップの操作失敗", exc_info=error)
        text = safe(str(error))[:1500] if isinstance(error, (ValueError, RuntimeError)) else "処理に失敗した。保存データを保持して管理者の確認を待つ。"
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def show(self, interaction: discord.Interaction, view: Screen, *, update: bool = False) -> None:
        if view.content_length() > 4000 or view.total_children_count > 40:
            raise ValueError("表示する情報が多すぎる。条件を絞る必要がある")
        if interaction.user.id in self.simple_users:
            texts = [child.content for child in view.walk_children() if isinstance(child, discord.ui.TextDisplay)]
            classic = discord.ui.View(timeout=900)
            for child in view.walk_children():
                if isinstance(child, discord.ui.Button) and len(classic.children) < 25:
                    clone = discord.ui.Button(label=child.label, style=child.style, custom_id=child.custom_id if not child.url else None, url=child.url, disabled=child.disabled)
                    clone.callback = child.callback
                    classic.add_item(clone)
            embed = discord.Embed(title="カスタムマップ", description="\n\n".join(texts)[:4000])
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, view=classic, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, view=classic, ephemeral=True)
            return
        if update and not interaction.response.is_done():
            await interaction.response.edit_message(view=view, allowed_mentions=discord.AllowedMentions.none())
        else:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
            await interaction.edit_original_response(view=view, allowed_mentions=discord.AllowedMentions.none())

    async def permitted(self, interaction: discord.Interaction, record: dict, *, control: bool = False) -> None:
        if str(interaction.guild_id) != str(record["guild"]) or not is_allowed_command_context(interaction):
            raise ValueError("対象のDiscordサーバーが異なる")
        channel = self.bot.get_channel(int(record["channel"]))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(record["channel"]))
            except discord.NotFound:
                raise ValueError("共有先が失われた。運用管理者が /escape recover で確認する必要がある") from None
        if not isinstance(interaction.user, discord.Member) or not channel.permissions_for(interaction.user).view_channel:
            raise ValueError("このプレイの共有先を閲覧できない")
        checked = getattr(interaction, "extras", {}).setdefault("custom_map_threads", set())
        if isinstance(channel, discord.Thread) and channel.is_private() and channel.id not in checked and not channel.permissions_for(interaction.user).manage_threads:
            try:
                await channel.fetch_member(interaction.user.id)
            except (discord.NotFound, discord.Forbidden):
                raise ValueError("この非公開スレッドの参加者ではない") from None
            checked.add(channel.id)
        if control and not (check_role(interaction, "escape_manage") or (str(interaction.user.id) == record["owner"] and check_role(interaction, "escape_play"))):
            raise ValueError("代表者の操作権限が必要である")

    def host_status(self) -> str:
        manager = self.bot.server_manager
        if any(manager.is_running(s) or s in manager.get_desired_servers() for s in ("forge", "forge-alt")):
            return "通常サーバーのForgeが稼働・復帰予約中。カスタムマップの検索と参加準備は利用可能。"
        used = sum(manager.is_running(s) for s in self.service.slots)
        return f"カスタムマップ枠 {used}/2 使用中（開始時に再確認）"

    def panel_view(self) -> Screen:
        screen = Screen(self)
        screen.text("# カスタムマップで遊ぶ\n通常サーバーとは別に、配布マップを選んで共同プレイする。\n" + self.host_status())
        screen.row(screen.button("ホームを開く", self.open_home, "cm:panel:home", style=discord.ButtonStyle.primary), screen.button("マップを探す", self.open_search, "cm:panel:search"), screen.button("続きから", self.open_sessions, "cm:panel:sessions"))
        return screen

    async def open_home(self, interaction: discord.Interaction) -> None:
        await self.show(interaction, await self.home_view(interaction))

    async def home_view(self, interaction: discord.Interaction) -> Screen:
        screen = Screen(self, owner=interaction.user.id)
        screen.text("# カスタムマップ\n" + self.host_status())
        screen.row(screen.button("マップを探す", self.open_search, "cm:home:search", style=discord.ButtonStyle.primary), screen.button("続きから", self.open_sessions, "cm:home:sessions"), screen.button("最近のプレイ", self.open_history, "cm:home:history"))
        previous = await asyncio.to_thread(self.service.store.get, "search", f"{interaction.guild_id}-{interaction.user.id}")
        if previous:
            async def again(i: discord.Interaction) -> None:
                await self.search_results(i, previous["filters"])
            screen.row(screen.button("前回の条件で探す", again, "cm:home:previous"))
        return screen

    async def open_search(self, interaction: discord.Interaction) -> None:
        await self.search_form(interaction, {})

    async def search_form(self, interaction: discord.Interaction, filters: dict, mode: str = "basic") -> None:
        async def apply(i: discord.Interaction, values: dict) -> None:
            changed = dict(filters)
            if mode == "basic":
                for key in ("players", "difficulty_max", "max_minutes"):
                    value = values[key][0]
                    changed[key] = int(value) if value else None
                changed.update(keyword=values["keyword"].strip(), sort=values["sort"][0])
            elif mode == "environment":
                changed["version"] = values["version"][0] or None
            else:
                changed.update(source=values["source"][0] or None, pack=values["pack"], history=values["history"][0] or None)
                minutes = values["minutes"].strip()
                if minutes and (not minutes.isdecimal() or not 1 <= int(minutes) <= 100000):
                    raise ValueError("時間上限は1～100000の整数の分数で指定する必要がある。/escape home から再入力できる")
                changed["max_minutes"] = int(minutes) if minutes else None
                for tag in ("unknown_difficulty", "unknown_duration", "no_horror", "no_parkour", "no_combat"):
                    changed[tag] = tag in values["extra"]
            await self.search_results(i, changed)
        form = Form("カスタムマップの検索条件", self, interaction.user.id, apply)
        if mode == "basic":
            for key, label, numbers in (("players", "遊ぶ人数", list(range(2, 17))), ("difficulty_max", "謎解き難易度の上限", list(range(1, 5))), ("max_minutes", "所要時間の目安", [30, 60, 90, 120, 180, 240])):
                current = filters.get(key)
                if current and current not in numbers:
                    numbers.append(current)
                entries = [("", "指定なし")] + [(str(n), LEVELS[n] if key == "difficulty_max" and n < len(LEVELS) else f"{n}人" if key == "players" else f"{n}分以内") for n in numbers]
                form.field(key, label, select(entries, current or ""))
            form.field("keyword", "作品名・作者名", discord.ui.TextInput(required=False, default=filters.get("keyword", ""), max_length=100))
            form.field("sort", "並び順", select(list(SORTS.items()), filters.get("sort", "popular")))
        elif mode == "environment":
            versions = sorted({v["mc_version"] for v in self.service.catalog.items.values()})
            form.field("version", "Minecraft Java版", select([("", "指定なし")] + [(v, v) for v in versions], filters.get("version") or ""))
        else:
            form.field("source", "配布元", select([("", "指定なし"), ("colony", "クラフターズコロニー"), ("plum", "プラム太郎の群れ")], filters.get("source") or ""))
            form.field("pack", "パックの準備", discord.ui.RadioGroup(options=[discord.RadioGroupOption(label=label, value=value, default=value == (filters.get("pack") or "any")) for value, label in [("any", "指定なし"), ("none", "パックなし"), ("required", "指定パックあり")]]))
            form.field("history", "自分のプレイ状況", select([("", "すべて"), ("unplayed", "記録上未プレイ"), ("played", "プレイ済み"), ("cleared", "クリア済み")], filters.get("history") or ""))
            form.field("minutes", "時間上限（分、空欄なら指定なし）", discord.ui.TextInput(required=False, default=str(filters.get("max_minutes") or ""), max_length=6))
            form.field("extra", "追加条件", discord.ui.CheckboxGroup(required=False, min_values=0, options=[discord.CheckboxGroupOption(label=label, value=key, default=bool(filters.get(key))) for key, label in [("unknown_difficulty", "難易度未記載も含む"), ("unknown_duration", "時間未記載も含む"), ("no_horror", "ホラーなし"), ("no_parkour", "アスレチックなし"), ("no_combat", "戦闘なし")]]))
        await interaction.response.send_modal(form)

    async def search_results(self, interaction: discord.Interaction, filters: dict, page: int = 0, *, update: bool = False) -> None:
        await asyncio.to_thread(self.service.store.put, "search", {"id": f"{interaction.guild_id}-{interaction.user.id}", "guild": str(interaction.guild_id), "owner": str(interaction.user.id), "filters": filters})
        available = await asyncio.to_thread(self.service.available)
        played = await asyncio.to_thread(self.service.store.played, interaction.guild_id, interaction.user.id)
        items = self.service.catalog.search(filters, available=available, played=played)
        screen = Screen(self, owner=interaction.user.id)
        size = 3 if filters.get("compact") else 5
        page = max(0, min(page, max(0, (len(items) - 1) // size)))
        labels = {"players": "人数", "difficulty_max": "難易度上限", "max_minutes": "時間上限（分）", "keyword": "キーワード", "sort": "並び順", "source": "配布元", "version": "Minecraft版", "pack": "指定パック", "history": "履歴", "unknown_difficulty": "難易度未記載を含む", "unknown_duration": "時間未記載を含む", "no_horror": "ホラーなし", "no_parkour": "アスレなし", "no_combat": "戦闘なし", "compact": "短い表示"}
        conditions = " / ".join(f"{labels.get(k, k)}: {safe(SORTS.get(v, v) if isinstance(v, str) else v)}" for k, v in filters.items() if v not in (None, "", False)) or "指定なし"
        screen.text(f"# カスタムマップを探す\n条件: {conditions[:500]}\n{len(items)}件 / {page + 1}ページ目\n{self.host_status()}")
        screen.box.add_item(discord.ui.Separator())
        for item in items[page * size:(page + 1) * size]:
            async def detail(i: discord.Interaction, ident=item["id"]) -> None:
                await self.map_detail(i, ident, filters, page)
            text = f"**{safe(item['title'])[:90]}**\n{safe(summary(item))}\n{('自分はプレイ済み' if item['id'] in played else '記録上未プレイ')}"
            screen.box.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=screen.button("詳細", detail, "cm:map:" + item["id"])))
        if not items:
            screen.text("一致する対応済みマップがない。未記載情報を含めるか、時間・難易度の条件を外して検索できる。導入検証待ちの作品は表示しない。")
        async def change(i: discord.Interaction) -> None:
            await self.search_form(i, filters)
        async def advanced(i: discord.Interaction) -> None:
            await self.search_form(i, filters, "advanced")
        async def environment(i: discord.Interaction) -> None:
            await self.search_form(i, filters, "environment")
        async def reset(i: discord.Interaction) -> None:
            await self.search_results(i, {}, update=True)
        async def previous(i: discord.Interaction) -> None:
            await self.search_results(i, filters, page - 1, update=True)
        async def next_page(i: discord.Interaction) -> None:
            await self.search_results(i, filters, page + 1, update=True)
        screen.row(screen.button("条件変更・並び順", change, "cm:search:filter"), screen.button("詳細条件", advanced, "cm:search:advanced"))
        screen.row(screen.button("環境条件", environment, "cm:search:environment"), screen.button("条件リセット", reset, "cm:search:reset"))
        screen.row(screen.button("前へ", previous, "cm:search:prev", disabled=page == 0), screen.button("次へ", next_page, "cm:search:next", disabled=(page + 1) * size >= len(items)), screen.button("ホーム", self.open_home, "cm:search:home"))
        await self.show(interaction, screen, update=update)

    async def map_detail(self, interaction: discord.Interaction, ident: str, filters: dict | None = None, page: int = 0) -> None:
        item = await self.service.map_info(ident)
        screen = Screen(self, owner=interaction.user.id)
        screen.text(f"# {safe(item['title'])}\nカスタムマップ / Java版 {safe(item['mc_version'])}\n{safe(summary(item))}\n[作者の配布ページ]({item['source_url']})\n難易度・時間は目安である。未評価の項目は推測で埋めない。")
        if item.get("difficulty_raw"):
            screen.text("作者の難易度表記: " + safe(item["difficulty_raw"]) + "\n" + safe(item.get("difficulty_note", "")))
        async def prepare(i: discord.Interaction) -> None:
            await self.room_form(i, item)
        async def setup(i: discord.Interaction) -> None:
            await self.setup_view(i, item)
        async def back(i: discord.Interaction) -> None:
            await self.search_results(i, filters or {}, page)
        screen.row(screen.button("このマップで準備", prepare, "cm:detail:prepare", style=discord.ButtonStyle.primary), screen.button("参加準備の手順", setup, "cm:detail:setup"), screen.button("検索へ戻る", back, "cm:detail:back"))
        await self.show(interaction, screen)

    async def room_form(self, interaction: discord.Interaction, item: dict, session: dict | None = None) -> None:
        if not check_role(interaction, "escape_play"):
            raise ValueError("カスタムマップの代表者権限が必要である")
        async def submit(i: discord.Interaction, values: dict) -> None:
            if not check_role(i, "escape_play"):
                raise ValueError("カスタムマップの代表者権限が必要である")
            current_session = None
            if session:
                current_session = await self.service.get("session", session["id"])
                await self.permitted(i, current_session, control=True)
            players = values["players"].strip()
            if not players.isdecimal():
                raise ValueError("予定人数は整数で指定する必要がある")
            room = await self.service.create_room(i.guild_id, i.channel_id, i.user.id, item["id"], values["name"], int(players), current_session)
            channel = self.bot.get_channel(int(room["channel"])) or await self.bot.fetch_channel(int(room["channel"]))
            await self.bind_card("room", room["id"], channel)
            await self.show(i, await self.object_view(i, "room", room["id"]))
        form = Form("カスタムマップ｜参加準備を作成", self, interaction.user.id, submit)
        form.field("name", "今回のプレイ名", discord.ui.TextInput(default=(session or {}).get("name", item["title"])[:60], max_length=60))
        form.field("players", "予定人数", discord.ui.TextInput(default=str(max(item["min_players"], len((session or {}).get("current_members", [])))), max_length=3))
        await interaction.response.send_modal(form)

    async def setup_view(self, interaction: discord.Interaction, item: dict, room: dict | None = None) -> None:
        identity = await self.service.identity(interaction.guild_id, interaction.user.id)
        screen = Screen(self, owner=interaction.user.id)
        status = "承認済み" if identity and identity.get("approved") else "承認待ち" if identity else "未登録"
        screen.text(f"# カスタムマップ｜参加準備\n**{safe(item['title'])}**\n必要: Minecraft Java版 **{safe(item['mc_version'])}** / Mod追加不要\nMinecraft ID: **{status}**\n準備状況は自己申告であり、PCの自動検査ではない。\n\n1. Launcherの起動構成で指定版を選択する。\n2. 専用ゲームディレクトリを用意する。\n3. パックが必要なら作者指定のファイルを適用する。\n4. プレイ可能になった回の接続先を確認する。")
        settings = item.get("client_settings", [])
        preparation = await asyncio.to_thread(self.service.store.get, "preparation", f"{interaction.guild_id}-{interaction.user.id}-{item['requirements_hash']}")
        operating_system = (preparation or {}).get("os")
        directories = {"Windows": r"%APPDATA%\.minecraft\resourcepacks", "macOS": "~/Library/Application Support/minecraft/resourcepacks", "Linux": "~/.minecraft/resourcepacks"}
        screen.text("PC別の準備\n" + (f"{operating_system}: パックの標準フォルダーは `{directories[operating_system]}`。" if operating_system in directories else "「準備状況を設定」でPCを選ぶと、そのPCの保存先を表示する。") + "\n専用ゲームディレクトリを指定した場合は、その中の resourcepacks を使う。Launcher → Java Edition → 起動構成 → 新規作成で指定版・専用ディレクトリを設定する。")
        if settings:
            screen.text("今回の設定\n" + "\n".join("- " + safe(v) for v in settings)[:900])
        links = [discord.ui.Button(label="公式Launcher", url="https://www.minecraft.net/en-us/download"), discord.ui.Button(label="作者の説明", url=item["source_url"])]
        if item.get("resource_pack"):
            pack = item["resource_pack"]
            links.append(discord.ui.Button(label="今回のパック", url=pack["url"]))
            screen.text(f"指定パック: {safe(pack.get('filename', '作者の同梱パック'))}\nZIPのまま適用するか等、作者の説明を確認する。ファイルを再配布せず配布元へ案内する。")
            if pack.get("mode") == "server":
                screen.text("この作品は接続時にパックが配信される。今回の接続先を追加するときに「サーバーリソースパック」を有効にし、接続時の適用を許可する。準備の確認は、この手順を理解したことの申告である。")
        screen.row(*links)
        async def readiness(i: discord.Interaction) -> None:
            await self.preparation_form(i, item)
        async def register(i: discord.Interaction) -> None:
            await self.identity_form(i)
        screen.row(screen.button("準備状況を設定", readiness, "cm:setup:ready", style=discord.ButtonStyle.primary), screen.button("Minecraft名を登録", register, "cm:setup:identity"), screen.button("困ったとき", self.troubleshooting, "cm:setup:help"))
        if room:
            async def back(i: discord.Interaction) -> None:
                await self.show(i, await self.object_view(i, "room", room["id"]))
            screen.row(screen.button("準備ルームへ", back, "cm:setup:room"))
        await self.show(interaction, screen)

    async def preparation_form(self, interaction: discord.Interaction, item: dict) -> None:
        async def save(i: discord.Interaction, values: dict) -> None:
            requirements = {"version"} | ({"pack"} if item.get("resource_pack") else set())
            record = {"id": f"{i.guild_id}-{i.user.id}-{item['requirements_hash']}", "guild": str(i.guild_id), "owner": str(i.user.id), "os": values["os"], "ready": requirements.issubset(values["checks"]), "requirements_hash": item["requirements_hash"]}
            await self.service.put("preparation", record)
            await self.setup_view(i, item)
        form = Form("カスタムマップ｜準備状況", self, interaction.user.id, save)
        form.field("os", "利用するPC", discord.ui.RadioGroup(options=[discord.RadioGroupOption(label=v, value=v) for v in ("Windows", "macOS", "Linux")]))
        checks = [discord.CheckboxGroupOption(label=f"Java版 {item['mc_version']}で起動できた", value="version")]
        if item.get("resource_pack"):
            checks.append(discord.CheckboxGroupOption(label="接続時のパック適用手順を確認した" if item["resource_pack"].get("mode") == "server" else "今回の指定パックを適用した", value="pack"))
        form.field("checks", "確認できた項目（自己申告）", discord.ui.CheckboxGroup(required=False, min_values=0, options=checks))
        await interaction.response.send_modal(form)

    async def identity_form(self, interaction: discord.Interaction) -> None:
        async def submit(i: discord.Interaction, values: dict) -> None:
            name = values["name"].strip()
            if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", name):
                raise ValueError("Minecraft Java版のプレイヤー名を指定する必要がある")
            def resolve() -> dict:
                response = requests.get(f"https://api.mojang.com/users/profiles/minecraft/{name}", timeout=15)
                response.raise_for_status()
                return response.json()
            profile = await asyncio.to_thread(resolve)
            record = {"id": f"{i.guild_id}-{i.user.id}", "guild": str(i.guild_id), "owner": str(i.user.id), "name": profile["name"], "uuid": str(uuid.UUID(profile["id"])), "approved": False}
            existing = await self.service.identity(i.guild_id, i.user.id)
            if existing and existing.get("approved") and existing["uuid"] == record["uuid"]:
                await i.edit_original_response(content="このMinecraft IDは承認済みである。参加準備から利用できる。")
                return
            await self.service.put("identity", record)
            await i.edit_original_response(content="Minecraft IDを申請した。運用管理者の /escape approvals による本人確認待ちである。自己申告だけではOPやホワイトリストに追加しない。")
        form = Form("Minecraft名を登録", self, interaction.user.id, submit)
        form.field("name", "Minecraft Java版のプレイヤー名", discord.ui.TextInput(max_length=16, min_length=3))
        await interaction.response.send_modal(form)

    async def troubleshooting(self, interaction: discord.Interaction) -> None:
        screen = Screen(self, owner=interaction.user.id)
        screen.text("# カスタムマップ｜困ったとき\n**版が違う**: Launcherの起動構成を今回指定の版に合わせる。\n**文字・絵・音がおかしい**: 指定パックの版と適用順、作者指定の設定を確認する。\n**接続できない**: 今回のカードのホストとポート、プレイ可能状態、ID承認を確認する。再開時は以前とポートが変わる場合がある。\n**承認待ち**: 運用管理者が /escape approvals で本人確認する。Microsoftのパスワードや認証トークンをBotへ送らない。")
        await self.show(interaction, screen)

    async def object_view(self, interaction: discord.Interaction, kind: str, ident: str) -> Screen:
        record = await self.service.get(kind, ident)
        await self.permitted(interaction, record)
        return await self.shared_view(kind, ident, owner=interaction.user.id)

    async def shared_view(self, kind: str, ident: str, *, owner: int | None = None) -> Screen:
        record = await self.service.get(kind, ident)
        item = record["map"]
        screen = Screen(self, owner=owner)
        screen.text(f"# カスタムマップ｜{safe(record['name'])}\n**{safe(item['title'])}** / Java版 {safe(item['mc_version'])}\n状態: **{STATES.get(record['state'], record['state'])}**\n{safe(summary(item))}")
        if kind == "room":
            people = await self.service.readiness(record)
            approved = sum(bool(p["identity"] and p["identity"].get("approved")) for p in people)
            ready = sum(p["ready"] for p in people)
            screen.text(f"今回の参加登録 {len(people)}人 / 予定{record['players']}人\n準備の自己申告 {ready}/{len(people)}、ID承認 {approved}/{len(people)}\n" + "\n".join(f"{safe(p['identity']['name']) if p['identity'] else '参加者 ' + p['user']}: {'ID承認済み' if p['identity'] and p['identity'].get('approved') else '本人のID登録・管理者承認待ち'} / {'準備OK（自己申告）' if p['ready'] else '本人の準備未確認'}" for p in people)[:1100])
            jobs = [j for j in await self.service.rows("job", record["guild"]) if j.get("room") == ident]
            if jobs:
                job = jobs[0]
                screen.text(f"直近の開始処理: {safe(job['state'])}\n{safe(job.get('error', ''))[:500]}")
            if record.get("session_id") and record["state"] == "STARTED":
                async def current_session(i: discord.Interaction) -> None:
                    await self.show(i, await self.object_view(i, "session", record["session_id"]))
                screen.row(screen.button("今回のプレイ・接続案内", current_session, f"cm:room:{ident}:session"))
        else:
            screen.text(f"接続: `{safe(self.service.address(record))}`\n最終正常保存: {f'<t:{int(record["last_saved"])}:R>' if record.get('last_saved') else '未保存'}\n接続時間の目安: {int(record.get('active_seconds', 0) // 60)}分 / 参加中 {len(record.get('online', []))}人\n再開時は今回の接続先を確認する必要がある。")
            if record.get("error"):
                screen.text("確認事項: " + safe(record["error"])[:500])
        async def setup(i: discord.Interaction) -> None:
            current = await self.service.get(kind, ident)
            await self.permitted(i, current)
            await self.setup_view(i, current["map"], current if kind == "room" else None)
        async def refresh(i: discord.Interaction) -> None:
            await self.show(i, await self.object_view(i, kind, ident))
        screen.row(screen.button("自分の参加準備", setup, f"cm:{kind}:{ident}:setup"), screen.button("状態を更新", refresh, f"cm:{kind}:{ident}:refresh"))
        if kind == "room" and record["state"] == "OPEN":
            async def membership(i: discord.Interaction, join: bool) -> None:
                await i.response.defer(ephemeral=True, thinking=True)
                current = await self.service.get(kind, ident)
                await self.permitted(i, current)
                await self.service.membership(ident, i.user.id, join, record["generation"])
                await self.update_card(kind, ident)
                await self.show(i, await self.object_view(i, kind, ident))
            async def join(i: discord.Interaction) -> None:
                await membership(i, True)
            async def leave(i: discord.Interaction) -> None:
                await membership(i, False)
            async def start(i: discord.Interaction) -> None:
                await self.launch(i, ident, record["generation"])
            async def cancel(i: discord.Interaction) -> None:
                await i.response.defer(ephemeral=True, thinking=True)
                await self.permitted(i, await self.service.get("room", ident), control=True)
                await self.service.cancel_room(ident, i.user.id)
                await self.update_card(kind, ident)
                await self.show(i, await self.object_view(i, kind, ident))
            screen.row(screen.button("参加する", join, f"cm:room:{ident}:join"), screen.button("今回の参加を取り消す", leave, f"cm:room:{ident}:leave"), screen.button("この内容で開始（代表者）", start, f"cm:room:{ident}:start", style=discord.ButtonStyle.primary))
            screen.row(screen.button("準備ルームを閉じる（代表者）", cancel, f"cm:room:{ident}:cancel"))
        if kind == "session":
            async def action(i: discord.Interaction, finish: bool) -> None:
                current = await self.service.get(kind, ident)
                await self.permitted(i, current, control=True)
                if not i.response.is_done():
                    await i.response.defer(ephemeral=True, thinking=True)
                await self.service.pause(ident, i.user.id, finish=finish, expected=record.get("control_generation", 0))
                await self.update_card(kind, ident)
                await self.show(i, await self.object_view(i, kind, ident))
            async def pause(i: discord.Interaction) -> None:
                await action(i, False)
            async def finish(i: discord.Interaction) -> None:
                async def confirmed(submitted: discord.Interaction, values: dict) -> None:
                    if values["confirm"] != "クリア":
                        raise ValueError("確認欄に「クリア」と入力する必要がある")
                    await action(submitted, True)
                await self.permitted(i, await self.service.get(kind, ident), control=True)
                form = Form("クリアして終了", self, i.user.id, confirmed)
                form.field("confirm", "クリアしたら「クリア」と入力", discord.ui.TextInput(max_length=3), "保存して停止し、クリア申告として記録する。終了は後から取り消せる。")
                await i.response.send_modal(form)
            async def resume(i: discord.Interaction) -> None:
                current = await self.service.get(kind, ident)
                await self.permitted(i, current, control=True)
                await self.room_form(i, current["map"], current)
            async def delete(i: discord.Interaction) -> None:
                await self.delete_form(i, ident)
            if record["state"] == "RUNNING":
                screen.row(screen.button("保存して中断", pause, f"cm:session:{ident}:pause"), screen.button("クリアして終了", finish, f"cm:session:{ident}:finish"))
            elif record["state"] == "PAUSED":
                screen.row(screen.button("続きから", resume, f"cm:session:{ident}:resume", style=discord.ButtonStyle.primary), screen.button("クリアして終了", finish, f"cm:session:{ident}:finish"), screen.button("データを片づける", delete, f"cm:session:{ident}:delete"))
            elif record["state"] in ("COMPLETED", "DELETED"):
                screen.row(screen.button("同じマップで新しく", lambda i: self.room_form(i, item), f"cm:session:{ident}:new"), screen.button("データを片づける", delete, f"cm:session:{ident}:delete"))
            if record["state"] in ("COMPLETED", "DELETED") and record.get("data_available") != "none":
                async def undo(i: discord.Interaction) -> None:
                    await i.response.defer(ephemeral=True, thinking=True)
                    await self.permitted(i, await self.service.get("session", ident), control=True)
                    await self.service.undo(ident, i.user.id)
                    await self.update_card(kind, ident)
                    await self.show(i, await self.object_view(i, kind, ident))
                screen.row(screen.button("終了・消去を取り消す", undo, f"cm:session:{ident}:undo"))
            if record["state"] in ("RUNNING", "PAUSED", "COMPLETED"):
                async def backups(i: discord.Interaction) -> None:
                    await self.backup_view(i, ident)
                screen.row(screen.button("バックアップ・復元", backups, f"cm:session:{ident}:backups"), screen.button("別のマップを探す", self.open_search, f"cm:session:{ident}:search"))
        return screen

    async def bind_card(self, kind: str, ident: str, channel) -> None:
        if channel is None:
            raise ValueError("共有先が見つからない")
        if kind != "panel":
            record = await self.service.get(kind, ident)
            if str(channel.id) != record["channel"] or str(channel.guild.id) != record["guild"]:
                raise ValueError("共有先の公開範囲が一致しない")
        view = self.panel_view() if kind == "panel" else await self.shared_view(kind, ident)
        message = await channel.send(view=view, silent=True, allowed_mentions=discord.AllowedMentions.none())
        await self.service.put("message", {"id": f"{kind}-{ident}", "type": kind, "target": ident, "guild": str(channel.guild.id), "channel": str(channel.id), "message_id": str(message.id)})

    async def update_card(self, kind: str, ident: str) -> None:
        binding = await asyncio.to_thread(self.service.store.get, "message", f"{kind}-{ident}")
        if not binding:
            return
        try:
            view = await self.shared_view(kind, ident)
            rendered = json.dumps(view.to_components(), ensure_ascii=False, sort_keys=True)
            if self.rendered.get(binding["id"]) == rendered:
                return
            channel = self.bot.get_channel(int(binding["channel"])) or await self.bot.fetch_channel(int(binding["channel"]))
            await channel.get_partial_message(int(binding["message_id"])).edit(view=view, allowed_mentions=discord.AllowedMentions.none())
            self.rendered[binding["id"]] = rendered
        except (discord.NotFound, discord.Forbidden):
            log.warning("共有カードを更新できない。/escape recoverで共有先を復旧する必要がある: %s", ident)

    async def launch(self, interaction: discord.Interaction, ident: str, generation: int) -> None:
        room = await self.service.get("room", ident)
        await self.permitted(interaction, room, control=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        job = await asyncio.to_thread(self.service.store.create, "job", guild=room["guild"], channel=room["channel"], owner=room["owner"], room=ident, state="RUNNING")
        await interaction.edit_original_response(content=f"初期化・起動を開始した。ジョブ {job['id']}。共有カードまたは /escape sessions で状態を確認できる。")
        async def run() -> None:
            try:
                session = await self.service.start(ident, interaction.user.id, generation)
                channel = self.bot.get_channel(int(room["channel"])) or await self.bot.fetch_channel(int(room["channel"]))
                await self.bind_card("session", session["id"], channel)
                job["session"] = session["id"]
                job["state"] = "DONE"
            except Exception as error:
                log.exception("カスタムマップの初期化・起動が失敗した")
                job["state"], job["error"] = "FAILED", str(error)[:500]
            finally:
                await self.service.put("job", job)
                await self.update_card("room", ident)
        task = asyncio.create_task(run())
        self.service.jobs.add(task)
        task.add_done_callback(self.service.jobs.discard)

    async def delete_form(self, interaction: discord.Interaction, ident: str) -> None:
        session = await self.service.get("session", ident)
        await self.permitted(interaction, session, control=True)
        async def submit(i: discord.Interaction, values: dict) -> None:
            current = await self.service.get("session", ident)
            await self.permitted(i, current, control=True)
            if values["name"] != current["name"]:
                raise ValueError("確認用のプレイ名が一致しない")
            await self.service.delete(ident, i.user.id, expected=session.get("control_generation", 0), purge=values["purge"])
            await self.update_card("session", ident)
            await self.show(i, await self.object_view(i, "session", ident))
        form = Form("保存データの消去確認", self, interaction.user.id, submit)
        form.field("name", "確認のためプレイ名を入力", discord.ui.TextInput(max_length=60), session["name"][:100])
        form.field("purge", "直ちに完全消去する（復元不能）", discord.ui.Checkbox(default=False), "未選択なら7日保持。ごみ箱への移動だけでは空き容量は増えない。")
        await interaction.response.send_modal(form)

    async def open_sessions(self, interaction: discord.Interaction) -> None:
        await self.history_view(interaction, paused=True)

    async def open_history(self, interaction: discord.Interaction) -> None:
        await self.history_view(interaction)

    async def history_view(self, interaction: discord.Interaction, *, paused: bool = False, page: int = 0, channel_history: bool = False) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        sessions = await self.service.rows("session", interaction.guild_id)
        visible = []
        for item in sessions:
            if paused and (item["state"] not in ("PAUSED", "RUNNING", "FAILED", "COMPLETED", "DELETED") or item.get("data_available") == "none"):
                continue
            if not paused:
                if channel_history:
                    if item["channel"] != str(interaction.channel_id) or not item.get("actual_players"):
                        continue
                elif str(interaction.user.id) not in item.get("actual_players", {}):
                    continue
            try:
                await self.permitted(interaction, item)
                visible.append(item)
            except ValueError:
                continue
        screen = Screen(self, owner=interaction.user.id)
        screen.text("# カスタムマップ｜" + ("中断・保存データ" if paused else "このチャンネルのプレイ履歴" if channel_history else "自分の最近のプレイ") + f"\n{len(visible)}件。消去済みでも履歴は残る。")
        for item in visible[page * 5:page * 5 + 5]:
            async def detail(i: discord.Interaction, ident=item["id"]) -> None:
                await self.show(i, await self.object_view(i, "session", ident))
            screen.box.add_item(discord.ui.Section(discord.ui.TextDisplay(f"**{safe(item['name'])}**\n{safe(item['map']['title'])} / {STATES.get(item['state'], item['state'])}"), accessory=screen.button("詳細・次の操作", detail, "cm:history:" + item["id"])))
        async def prev(i: discord.Interaction) -> None:
            await self.history_view(i, paused=paused, page=max(0, page - 1), channel_history=channel_history)
        async def next_page(i: discord.Interaction) -> None:
            await self.history_view(i, paused=paused, page=page + 1, channel_history=channel_history)
        screen.row(screen.button("前へ", prev, "cm:history:prev", disabled=page == 0), screen.button("次へ", next_page, "cm:history:next", disabled=(page + 1) * 5 >= len(visible)), screen.button("ホーム", self.open_home, "cm:history:home"))
        if not paused:
            async def toggle(i: discord.Interaction) -> None:
                await self.history_view(i, channel_history=not channel_history)
            screen.row(screen.button("自分の履歴" if channel_history else "このチャンネルの履歴", toggle, "cm:history:scope"))
        await self.show(interaction, screen)

    async def backup_view(self, interaction: discord.Interaction, ident: str) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        session = await self.service.get("session", ident)
        await self.permitted(interaction, session, control=True)
        screen = Screen(self, owner=interaction.user.id)
        screen.text(f"# カスタムマップ｜バックアップ\n{safe(session['name'])}\nプレイ中の作成は一度保存停止してから同じ接続先へ復帰する。復元すると元のプレイを残して別の中断データを作る。")
        async def create(i: discord.Interaction) -> None:
            await i.response.defer(ephemeral=True, thinking=True)
            await self.permitted(i, await self.service.get("session", ident), control=True)
            await self.service.backup(ident, i.user.id)
            await self.backup_view(i, ident)
        screen.row(screen.button("今の状態をバックアップ", create, "cm:backup:create", style=discord.ButtonStyle.primary))
        records = [r for r in await self.service.rows("backup", session["guild"]) if r["session_id"] == ident]
        for record in records[:5]:
            async def restore(i: discord.Interaction, backup_id=record["id"]) -> None:
                await self.restore_command.callback(self, i, backup_id)
            screen.box.add_item(discord.ui.Section(discord.ui.TextDisplay(f"<t:{int(record['created'])}:f> / {'遠隔保存済み' if record.get('uploaded') else '本体に保存'}"), accessory=screen.button("別データに復元", restore, "cm:backup:" + record["id"])))
        screen.text("直近5件を表示。古い記録は /escape restore backup_id: で復元できる。" if records else "バックアップはまだない。")
        await self.show(interaction, screen)

    @app_commands.command(name="backups", description="プレイごとのバックアップ作成・復元を開く")
    async def backups_command(self, interaction: discord.Interaction, session_id: str) -> None:
        await self.backup_view(interaction, session_id)

    @app_commands.command(name="restore", description="バックアップを別の中断データとして復元する")
    async def restore_command(self, interaction: discord.Interaction, backup_id: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        record = await self.service.get("backup", backup_id)
        original = await self.service.get("session", record["session_id"])
        await self.permitted(interaction, original, control=True)
        restored = await self.service.restore_backup(backup_id, interaction.user.id)
        channel = self.bot.get_channel(int(restored["channel"])) or await self.bot.fetch_channel(int(restored["channel"]))
        await self.bind_card("session", restored["id"], channel)
        await self.show(interaction, await self.object_view(interaction, "session", restored["id"]))

    @app_commands.command(name="undo", description="クリア申告・保持中の消去を取り消して中断状態へ戻す")
    async def undo_command(self, interaction: discord.Interaction, session_id: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.permitted(interaction, await self.service.get("session", session_id), control=True)
        await self.service.undo(session_id, interaction.user.id)
        await self.update_card("session", session_id)
        await self.show(interaction, await self.object_view(interaction, "session", session_id))

    @tasks.loop(seconds=20)
    async def refresh(self) -> None:
        try:
            await self.service.poll()
            await self.service.expire_trash()
            for binding in await self.service.rows("message"):
                if binding["type"] in ("room", "session"):
                    await self.update_card(binding["type"], binding["target"])
        except Exception:
            log.exception("カスタムマップの定期更新が失敗した")

    @refresh.before_loop
    async def before_refresh(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="home", description="カスタムマップのホームを開く")
    async def home(self, interaction: discord.Interaction, simple: bool = False) -> None:
        if simple:
            self.simple_users.add(interaction.user.id)
        else:
            self.simple_users.discard(interaction.user.id)
        await self.open_home(interaction)

    @app_commands.command(name="maps", description="人数・謎解き難易度・時間でカスタムマップを探す")
    @app_commands.choices(difficulty=[app_commands.Choice(name=label, value=n) for n, label in enumerate(LEVELS)])
    @app_commands.choices(sort=[app_commands.Choice(name=v, value=k) for k, v in SORTS.items()])
    async def maps(self, interaction: discord.Interaction, players: app_commands.Range[int, 1, 100] | None = None, difficulty: int = 0, max_minutes: app_commands.Range[int, 1, 100000] | None = None, keyword: str = "", sort: str = "popular", compact: bool = False) -> None:
        await self.search_results(interaction, {"players": players, "difficulty_max": difficulty or None, "max_minutes": max_minutes, "keyword": keyword[:100], "sort": sort, "compact": compact})

    @app_commands.command(name="history", description="最近のカスタムマップのプレイを見る")
    async def history(self, interaction: discord.Interaction, channel_history: bool = False) -> None:
        await self.history_view(interaction, channel_history=channel_history)

    @app_commands.command(name="sessions", description="中断・稼働中のカスタムマップを見る")
    async def sessions(self, interaction: discord.Interaction) -> None:
        await self.open_sessions(interaction)

    @app_commands.command(name="create", description="カスタムマップの参加準備ルームを作る")
    async def create(self, interaction: discord.Interaction, map_id: str) -> None:
        await self.room_form(interaction, await self.service.map_info(map_id))

    @create.autocomplete("map_id")
    async def map_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        available = await asyncio.to_thread(self.service.available)
        return [app_commands.Choice(name=x["title"][:100], value=x["id"]) for x in self.service.catalog.search({"keyword": current}, available=available)[:25]]

    @app_commands.command(name="resume", description="カスタムマップの再開準備を開く")
    async def resume(self, interaction: discord.Interaction, session_id: str) -> None:
        session = await self.service.get("session", session_id)
        await self.permitted(interaction, session, control=True)
        await self.room_form(interaction, session["map"], session)

    @app_commands.command(name="start", description="参加者を確定してカスタムマップを開始する")
    async def start(self, interaction: discord.Interaction, room_id: str) -> None:
        room = await self.service.get("room", room_id)
        await self.launch(interaction, room_id, room["generation"])

    @app_commands.command(name="setup", description="今回の版に対応した参加準備を見る")
    async def setup_command(self, interaction: discord.Interaction, map_id: str) -> None:
        await self.setup_view(interaction, await self.service.map_info(map_id))

    @app_commands.command(name="panel", description="カスタムマップの常設入口をこのチャンネルに設置する")
    async def panel(self, interaction: discord.Interaction) -> None:
        if not check_role(interaction, "escape_manage"):
            raise app_commands.CheckFailure()
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bind_card("panel", str(interaction.channel_id), interaction.channel)
        await interaction.edit_original_response(content="カスタムマップの常設入口を設置した。")

    @app_commands.command(name="approvals", description="Minecraft ID申請を確認・承認する（運用管理者）")
    async def approvals(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        if not check_role(interaction, "escape_manage"):
            raise app_commands.CheckFailure()
        if user:
            record = await self.service.identity(interaction.guild_id, user.id)
            if not record:
                raise ValueError("この利用者のMinecraft ID申請がない")
            await asyncio.to_thread(self.service.store.approve_identity, interaction.guild_id, user.id, record["name"], record["uuid"], interaction.user.id)
            await interaction.response.send_message("本人確認済みのMinecraft IDを承認した。", ephemeral=True)
        else:
            records = [r for r in await self.service.rows("identity", interaction.guild_id) if not r.get("approved")]
            await interaction.response.send_message("承認待ち（本人確認後に user を指定して再実行）\n" + ("\n".join(f"Discord {r['owner']}: {safe(r['name'])}" for r in records[:30]) or "なし"), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="recover", description="共有先・失敗した保存データを復旧する（運用管理者）")
    async def recover(self, interaction: discord.Interaction, session_id: str, channel: discord.TextChannel | None = None) -> None:
        if not check_role(interaction, "escape_manage"):
            raise app_commands.CheckFailure()
        session = await self.service.get("session", session_id)
        if str(interaction.guild_id) != session["guild"]:
            raise ValueError("別guildのデータを復旧できない")
        await interaction.response.defer(ephemeral=True, thinking=True)
        if channel:
            session["channel"] = str(channel.id)
            session = await self.service.put("session", session)
            await self.bind_card("session", session_id, channel)
        if session["state"] == "FAILED" and session.get("slot") and not self.bot.server_manager.is_running(session["slot"]):
            source = Path(self.bot.server_manager.get_server(session["slot"]).cwd)
            saved = self.service._session_path(session_id)
            if (saved / "world/level.dat").is_file() and not (source.exists() and any(source.iterdir())):
                source.mkdir(parents=True, exist_ok=True)
            else:
                marker = source / "launch.json"
                if not (source / "world/level.dat").is_file() or not marker.is_file() or json.loads(marker.read_text())["session_id"] != session_id:
                    raise ValueError("対象の保存データを安全に特定できない。起動枠の内容を保持して管理者による確認を待つ")
                await asyncio.to_thread(self.service._detach, session)
            self.bot.server_manager._set_desired(session["slot"], False)
            session.update(slot=None, state="PAUSED", save_error_seen=False, control_generation=session.get("control_generation", 0) + 1, error="異常終了後の保存データ。再開前に進行を確認する必要がある")
            await self.service.put("session", session)
        await interaction.edit_original_response(content="保存データを保持して復旧状態を更新した。/escape sessions で確認できる。")


async def setup(bot: commands.Bot) -> None:
    if CONFIG.get("custom_maps", {}).get("enabled", False):
        await bot.add_cog(CustomMaps(bot))
