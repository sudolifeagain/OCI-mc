"""Components V2によるカスタムマップの共同プレイ管理。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Callable, Awaitable

import discord
from discord import app_commands
from discord.ext import commands, tasks

from settings import CONFIG, SERVER_RUNTIME_DIR
from utils.custom_maps.catalog import summary
from utils.custom_maps.service import CustomMapService
from utils.discord_security import is_allowed_command_context, is_custom_map_channel, custom_map_channel_notice
from utils.permissions import check_role

log = logging.getLogger(__name__)
LEVELS = ["指定なし", "入門", "やさしい", "標準", "難しい"]
SORTS = {"popular": "人気順", "short": "所要時間が短い順", "long": "所要時間が長い順", "easy": "やさしい順", "hard": "難しい順", "many": "対応人数が多い順", "few": "対応人数が少ない順", "recent": "最近遊んだ順"}
STATES = {"OPEN": "参加者募集中", "PREPARING": "ワールド作成中", "STARTING": "起動中", "STARTED": "開始済み", "CANCELLED": "募集終了", "RUNNING": "プレイ中", "SAVING": "保存して停止中", "PAUSED": "中断中", "COMPLETED": "クリア済み", "FAILED": "要確認", "DELETING": "削除中", "DELETED": "削除済み"}
UNSPECIFIED_OPTION = "__unspecified__"


def safe(text: object) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


def filter_summary(filters: dict) -> str:
    parts = []
    if filters.get("players"):
        parts.append(f"{filters['players']}人")
    if filters.get("difficulty_max"):
        parts.append(f"難易度は{LEVELS[int(filters['difficulty_max'])]}まで")
    if filters.get("max_minutes"):
        parts.append(f"{filters['max_minutes']}分以内")
    if filters.get("keyword"):
        parts.append(f"「{safe(filters['keyword'])}」")
    if filters.get("version"):
        parts.append(f"Java版 {safe(filters['version'])}")
    for key, labels in (
        ("source", {"colony": "クラフターズコロニー", "plum": "プラム太郎の群れ"}),
        ("pack", {"none": "リソースパック不要", "required": "リソースパックあり"}),
        ("history", {"unplayed": "このチャンネルで未プレイ", "played": "このチャンネルでプレイ済み", "cleared": "このチャンネルでクリア済み"}),
    ):
        if filters.get(key) in labels:
            parts.append(labels[filters[key]])
    for key, label in (("unknown_difficulty", "難易度不明も含む"), ("unknown_duration", "所要時間不明も含む"), ("no_horror", "ホラーなし"), ("no_parkour", "アスレチックなし"), ("no_combat", "戦闘なし")):
        if filters.get(key):
            parts.append(label)
    return " / ".join(parts) or "絞り込みなし"


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
            await interaction.response.send_message("このDiscordサーバーでは利用できません。", ephemeral=True)
            return False
        if not is_custom_map_channel(interaction):
            await interaction.response.send_message(custom_map_channel_notice(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return False
        if self.owner is not None and (interaction.user.id != self.owner or time.time() > self.expires):
            await interaction.response.send_message("この画面は有効期限が切れています。/escape home から開き直してください。", ephemeral=True)
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
            return await interaction.response.send_message("このフォームを送信する権限がありません。", ephemeral=True)
        if not is_custom_map_channel(interaction):
            return await interaction.response.send_message(custom_map_channel_notice(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
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
        text = safe(str(error))[:1500] if isinstance(error, (ValueError, RuntimeError)) else "操作を完了できませんでした。画面を開き直しても同じエラーが出る場合は、管理者に連絡してください。"
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def show(self, interaction: discord.Interaction, view: Screen, *, update: bool = False) -> None:
        if view.content_length() > 4000 or view.total_children_count > 40:
            raise ValueError("表示件数が多すぎます。検索条件を絞ってください。")
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
            raise ValueError("別のDiscordサーバーのワールドは操作できません。")
        channel = self.bot.get_channel(int(record["channel"]))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(record["channel"]))
            except discord.NotFound:
                raise ValueError("ワールドのチャンネルが見つかりません。管理者に復旧を依頼してください。") from None
        if not isinstance(interaction.user, discord.Member) or not channel.permissions_for(interaction.user).view_channel:
            raise ValueError("このワールドのチャンネルを閲覧する権限がありません。")
        checked = getattr(interaction, "extras", {}).setdefault("custom_map_threads", set())
        if isinstance(channel, discord.Thread) and channel.is_private() and channel.id not in checked and not channel.permissions_for(interaction.user).manage_threads:
            try:
                await channel.fetch_member(interaction.user.id)
            except (discord.NotFound, discord.Forbidden):
                raise ValueError("この非公開スレッドには参加していません。") from None
            checked.add(channel.id)
        if control and not (check_role(interaction, "escape_manage") or (str(interaction.user.id) == record["owner"] and check_role(interaction, "escape_play"))):
            raise ValueError("この操作は募集した人か管理者が行ってください。")

    def host_status(self) -> str:
        manager = self.bot.server_manager
        if any(manager.is_running(s) or s in manager.get_desired_servers() for s in ("forge", "forge-alt")):
            return "Forgeを使用中のため、カスタムマップは開始できません。\nマップの検索と参加準備はできます。"
        used = sum(manager.is_running(s) for s in self.service.slots)
        return f"プレイ中のカスタムマップ：{used} / 2"

    def panel_view(self) -> Screen:
        screen = Screen(self)
        screen.text("# カスタムマップ\n脱出マップを選んで、みんなで遊べます。\n中断したワールドは「続きから」で再開できます。\n\n" + self.host_status())
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
                    raise ValueError("所要時間は1～100000分の整数で入力してください。")
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
            form.field("version", "Minecraftのバージョン（Java版）", select([("", "指定なし")] + [(v, v) for v in versions], filters.get("version") or ""))
        else:
            form.field("source", "配布元", select([("", "指定なし"), ("colony", "クラフターズコロニー"), ("plum", "プラム太郎の群れ")], filters.get("source") or ""))
            form.field("pack", "リソースパック", discord.ui.RadioGroup(options=[discord.RadioGroupOption(label=label, value=value, default=value == (filters.get("pack") or "any")) for value, label in [("any", "指定なし"), ("none", "不要なマップ"), ("required", "必要なマップ")]]))
            form.field("history", "このチャンネルのプレイ履歴", select([("", "すべて"), ("unplayed", "未プレイ"), ("played", "プレイ済み"), ("cleared", "クリア済み")], filters.get("history") or ""))
            form.field("minutes", "時間上限（分、空欄なら指定なし）", discord.ui.TextInput(required=False, default=str(filters.get("max_minutes") or ""), max_length=6))
            form.field("extra", "追加条件", discord.ui.CheckboxGroup(required=False, min_values=0, options=[discord.CheckboxGroupOption(label=label, value=key, default=bool(filters.get(key))) for key, label in [("unknown_difficulty", "難易度未記載も含む"), ("unknown_duration", "時間未記載も含む"), ("no_horror", "ホラーなし"), ("no_parkour", "アスレチックなし"), ("no_combat", "戦闘なし")]]))
        await interaction.response.send_modal(form)

    async def search_results(self, interaction: discord.Interaction, filters: dict, page: int = 0, *, update: bool = False) -> None:
        await asyncio.to_thread(self.service.store.put, "search", {"id": f"{interaction.guild_id}-{interaction.user.id}", "guild": str(interaction.guild_id), "owner": str(interaction.user.id), "filters": filters})
        available = await asyncio.to_thread(self.service.available)
        played = await asyncio.to_thread(self.service.store.played, interaction.guild_id, interaction.channel_id)
        items = self.service.catalog.search(filters, available=available, played=played)
        screen = Screen(self, owner=interaction.user.id)
        size = 3 if filters.get("compact") else 5
        page = max(0, min(page, max(0, (len(items) - 1) // size)))
        conditions = filter_summary(filters)
        screen.text(f"# マップを探す\n{len(items)}件 / {page + 1}ページ目\n{conditions[:500]}\n並び順：{SORTS.get(filters.get('sort', 'popular'), SORTS['popular'])}\n{self.host_status()}")
        screen.box.add_item(discord.ui.Separator())
        for item in items[page * size:(page + 1) * size]:
            async def detail(i: discord.Interaction, ident=item["id"]) -> None:
                await self.map_detail(i, ident, filters, page)
            text = f"**{safe(item['title'])[:90]}**\n{safe(summary(item))}\n{('このチャンネルでプレイ済み' if item['id'] in played else 'このチャンネルでは未プレイ')}"
            screen.box.add_item(discord.ui.Section(discord.ui.TextDisplay(text), accessory=screen.button("詳細", detail, "cm:map:" + item["id"])))
        if not items:
            screen.text("条件に合うマップがありません。人数・難易度・所要時間などの条件を減らしてみてください。")
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
        screen.text(f"# {safe(item['title'])}\nMinecraft Java版 {safe(item['mc_version'])}\n{safe(summary(item))}\n[配布ページ]({item['source_url']})")
        if item.get("difficulty_raw"):
            screen.text("作者による難易度：" + safe(item["difficulty_raw"]))
        async def prepare(i: discord.Interaction) -> None:
            await self.room_form(i, item)
        async def setup(i: discord.Interaction) -> None:
            await self.setup_view(i, item)
        async def back(i: discord.Interaction) -> None:
            await self.search_results(i, filters or {}, page)
        screen.row(screen.button("メンバーを募集", prepare, "cm:detail:prepare", style=discord.ButtonStyle.primary), screen.button("参加前の準備", setup, "cm:detail:setup"), screen.button("検索に戻る", back, "cm:detail:back"))
        await self.show(interaction, screen)

    async def room_form(self, interaction: discord.Interaction, item: dict, session: dict | None = None) -> None:
        if not check_role(interaction, "escape_play"):
            raise ValueError("参加者を募集する権限がありません。管理者に確認してください。")
        async def submit(i: discord.Interaction, values: dict) -> None:
            if not check_role(i, "escape_play"):
                raise ValueError("参加者を募集する権限がありません。管理者に確認してください。")
            current_session = None
            if session:
                current_session = await self.service.get("session", session["id"])
                await self.permitted(i, current_session, control=True)
            players = values["players"].strip()
            if not players.isdecimal():
                raise ValueError("参加人数は整数で入力してください。")
            room = await self.service.create_room(i.guild_id, i.channel_id, i.user.id, item["id"], values["name"], int(players), current_session)
            channel = self.bot.get_channel(int(room["channel"])) or await self.bot.fetch_channel(int(room["channel"]))
            await self.bind_card("room", room["id"], channel)
            await self.show(i, await self.object_view(i, "room", room["id"]))
        form = Form("メンバーを募集", self, interaction.user.id, submit)
        form.field("name", "ワールド名", discord.ui.TextInput(default=(session or {}).get("name", item["title"])[:60], max_length=60))
        form.field("players", "参加予定の人数", discord.ui.TextInput(default=str(max(item["min_players"], len((session or {}).get("current_members", [])))), max_length=3))
        await interaction.response.send_modal(form)

    async def setup_view(self, interaction: discord.Interaction, item: dict, room: dict | None = None) -> None:
        screen = Screen(self, owner=interaction.user.id)
        screen.text(f"# 参加前の準備\n**{safe(item['title'])}**\nMinecraft Java版 **{safe(item['mc_version'])}**（Modは不要）\n通常サーバーで接続を許可されている人は、そのまま参加できます。\n\n1. ランチャーで上記のバージョンを選びます。\n2. このマップ用のゲームディレクトリを作ります。\n3. リソースパックが必要な場合は、作者の説明に従って入れます。\n4. 準備ができたら、下の「準備状況を入力」から確認項目にチェックしてください。")
        settings = item.get("client_settings", [])
        preparation = await asyncio.to_thread(self.service.store.get, "preparation", f"{interaction.guild_id}-{interaction.user.id}-{item['requirements_hash']}")
        operating_system = (preparation or {}).get("os")
        directories = {"Windows": r"%APPDATA%\.minecraft\resourcepacks", "macOS": "~/Library/Application Support/minecraft/resourcepacks", "Linux": "~/.minecraft/resourcepacks"}
        screen.text("**ランチャーの設定**\n「Java Edition → 起動構成 → 新規作成」でバージョンとゲームディレクトリを設定できます。")
        if item.get("resource_pack"):
            screen.text("**リソースパックの保存先**\n" + (f"{operating_system}の標準フォルダー：`{directories[operating_system]}`" if operating_system in directories else "「準備状況を入力」でPCの種類を選ぶと、標準の保存先を表示します。") + "\nゲームディレクトリを変更した場合は、その中の `resourcepacks` フォルダーに入れてください。")
        if settings:
            screen.text("**マップの設定・ルール**\n" + "\n".join("- " + safe(v) for v in settings)[:900])
        links = [discord.ui.Button(label="ランチャーをダウンロード", url="https://www.minecraft.net/en-us/download"), discord.ui.Button(label="配布ページ・遊び方", url=item["source_url"])]
        if item.get("resource_pack"):
            pack = item["resource_pack"]
            links.append(discord.ui.Button(label="リソースパックの配布元", url=pack["url"]))
            screen.text(f"**必要なリソースパック**\n{safe(pack.get('filename', 'マップに同梱のリソースパック'))}\n入れ方は配布ページを確認してください。")
            if pack.get("mode") == "server":
                screen.text("このマップのリソースパックは、サーバーへの接続時にダウンロードされます。接続先の設定で「サーバーリソースパック」を有効にし、接続時に適用を許可してください。")
        screen.row(*links)
        async def readiness(i: discord.Interaction) -> None:
            await self.preparation_form(i, item)
        screen.row(screen.button("準備状況を入力", readiness, "cm:setup:ready", style=discord.ButtonStyle.primary), screen.button("困ったとき", self.troubleshooting, "cm:setup:help"))
        if room:
            async def back(i: discord.Interaction) -> None:
                await self.show(i, await self.object_view(i, "room", room["id"]))
            screen.row(screen.button("募集画面に戻る", back, "cm:setup:room"))
        await self.show(interaction, screen)

    async def preparation_form(self, interaction: discord.Interaction, item: dict) -> None:
        async def save(i: discord.Interaction, values: dict) -> None:
            requirements = {"version"} | ({"pack"} if item.get("resource_pack") else set())
            record = {"id": f"{i.guild_id}-{i.user.id}-{item['requirements_hash']}", "guild": str(i.guild_id), "owner": str(i.user.id), "os": values["os"], "ready": requirements.issubset(values["checks"]), "requirements_hash": item["requirements_hash"]}
            await self.service.put("preparation", record)
            await self.setup_view(i, item)
        form = Form("カスタムマップ｜準備状況", self, interaction.user.id, save)
        form.field("os", "PCの種類", discord.ui.RadioGroup(options=[discord.RadioGroupOption(label=v, value=v) for v in ("Windows", "macOS", "Linux")]))
        checks = [discord.CheckboxGroupOption(label=f"Java版 {item['mc_version']}で起動できた", value="version")]
        if item.get("resource_pack"):
            checks.append(discord.CheckboxGroupOption(label="接続時にリソースパックを適用する手順を確認した" if item["resource_pack"].get("mode") == "server" else "リソースパックを入れた", value="pack"))
        form.field("checks", "準備できた項目にチェック", discord.ui.CheckboxGroup(required=False, min_values=0, options=checks))
        await interaction.response.send_modal(form)

    async def troubleshooting(self, interaction: discord.Interaction) -> None:
        screen = Screen(self, owner=interaction.user.id)
        screen.text("# 困ったとき\n**バージョンが違うと言われる**\nランチャーの「起動構成」で、マップに合ったバージョンを選んでください。\n\n**文字・見た目・音がおかしい**\nリソースパックの入れ方と、配布ページの推奨設定を確認してください。\n\n**接続できない**\nワールドがプレイ中になっているか、接続先が正しいかを確認してください。再開後は最新の接続先を使ってください。\n\n**ホワイトリストに入っていないと言われる**\n/whitelist_add でサーバーに「カスタムマップ」を選び、Java版のMinecraft名を追加してください。Discordとの紐付けや管理者の承認は不要です。")
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
            ready = sum(p["ready"] for p in people)
            screen.text(f"参加予定：{record['players']}人（このマップは{item['min_players']}～{item['max_players']}人）\n募集への参加：{len(people)}人 / 準備の確認：{ready}人\n\n" + "\n".join(f"<@{p['user']}>：{'準備完了' if p['ready'] else '準備未確認'}" for p in people)[:1100])
            screen.text("代表者だけで先にサーバーを起動できます。遊ぶ前に全員が「参加前の準備」でバージョンや設定を確認してください。")
            jobs = [j for j in await self.service.rows("job", record["guild"]) if j.get("room") == ident and j["state"] != "SUPERSEDED"]
            if jobs:
                job = jobs[0]
                job_status = {"RUNNING": "準備中", "DONE": "起動完了", "FAILED": "起動できませんでした"}.get(job["state"], "要確認")
                screen.text(f"起動状況：{job_status}\n{safe(job.get('error', ''))[:500]}")
            if record.get("session_id") and record["state"] == "STARTED":
                async def current_session(i: discord.Interaction) -> None:
                    await self.show(i, await self.object_view(i, "session", record["session_id"]))
                screen.row(screen.button("ワールドと接続先を見る", current_session, f"cm:room:{ident}:session"))
        else:
            screen.text(f"接続先：`{safe(self.service.address(record))}`\n最終保存：{f'<t:{int(record["last_saved"])}:R>' if record.get('last_saved') else 'まだ保存されていません'}\nプレイ時間：約{int(record.get('active_seconds', 0) // 60)}分 / 接続中：{len(record.get('online', []))}人\n再開後は、ここに表示される接続先を使ってください。")
            if record.get("error"):
                screen.text("エラー：" + safe(record["error"])[:500])
        async def setup(i: discord.Interaction) -> None:
            current = await self.service.get(kind, ident)
            await self.permitted(i, current)
            await self.setup_view(i, current["map"], current if kind == "room" else None)
        async def refresh(i: discord.Interaction) -> None:
            await self.show(i, await self.object_view(i, kind, ident))
        screen.row(screen.button("参加前の準備", setup, f"cm:{kind}:{ident}:setup"), screen.button("更新", refresh, f"cm:{kind}:{ident}:refresh"))
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
            screen.row(screen.button("参加する", join, f"cm:room:{ident}:join"), screen.button("参加を取り消す", leave, f"cm:room:{ident}:leave"), screen.button("ワールドを起動", start, f"cm:room:{ident}:start", style=discord.ButtonStyle.primary))
            screen.row(screen.button("募集を取り消す", cancel, f"cm:room:{ident}:cancel"))
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
                        raise ValueError("確認欄に「クリア」と入力してください。")
                    await action(submitted, True)
                await self.permitted(i, await self.service.get(kind, ident), control=True)
                form = Form("クリアして終了", self, i.user.id, confirmed)
                form.field("confirm", "確認のため「クリア」と入力", discord.ui.TextInput(max_length=3), "ワールドを保存して終了し、クリア済みにします。後から取り消せます。")
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
                screen.row(screen.button("続きから", resume, f"cm:session:{ident}:resume", style=discord.ButtonStyle.primary), screen.button("クリア済みにする", finish, f"cm:session:{ident}:finish"), screen.button("ワールドを削除", delete, f"cm:session:{ident}:delete"))
            elif record["state"] in ("COMPLETED", "DELETED"):
                screen.row(screen.button("最初から遊ぶ", lambda i: self.room_form(i, item), f"cm:session:{ident}:new"), screen.button("ワールドを削除", delete, f"cm:session:{ident}:delete"))
            if record["state"] in ("COMPLETED", "DELETED") and record.get("data_available") != "none":
                async def undo(i: discord.Interaction) -> None:
                    await i.response.defer(ephemeral=True, thinking=True)
                    await self.permitted(i, await self.service.get("session", ident), control=True)
                    await self.service.undo(ident, i.user.id)
                    await self.update_card(kind, ident)
                    await self.show(i, await self.object_view(i, kind, ident))
                screen.row(screen.button("ごみ箱から戻す" if record["state"] == "DELETED" else "クリアを取り消す", undo, f"cm:session:{ident}:undo"))
            if record["state"] in ("RUNNING", "PAUSED", "COMPLETED"):
                async def backups(i: discord.Interaction) -> None:
                    await self.backup_view(i, ident)
                screen.row(screen.button("バックアップ・復元", backups, f"cm:session:{ident}:backups"), screen.button("別のマップを探す", self.open_search, f"cm:session:{ident}:search"))
        return screen

    async def bind_card(self, kind: str, ident: str, channel) -> None:
        if channel is None:
            raise ValueError("投稿先のチャンネルが見つかりません。")
        self.service.check_channel(channel.id)
        if kind != "panel":
            record = await self.service.get(kind, ident)
            if str(channel.id) != record["channel"] or str(channel.guild.id) != record["guild"]:
                raise ValueError("このワールドは別のチャンネルには投稿できません。")
        view = self.panel_view() if kind == "panel" else await self.shared_view(kind, ident)
        message = await channel.send(view=view, silent=True, allowed_mentions=discord.AllowedMentions.none())
        await self.service.put("message", {"id": f"{kind}-{ident}", "type": kind, "target": ident, "guild": str(channel.guild.id), "channel": str(channel.id), "message_id": str(message.id)})

    async def update_card(self, kind: str, ident: str) -> None:
        binding = await asyncio.to_thread(self.service.store.get, "message", f"{kind}-{ident}")
        if not binding:
            return
        try:
            view = self.panel_view() if kind == "panel" else await self.shared_view(kind, ident)
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
        await interaction.edit_original_response(content="ワールドを準備しています。起動が終わると、このチャンネルに接続先を表示します。")
        async def run() -> None:
            session = None
            try:
                session = await self.service.start(ident, interaction.user.id, generation)
                channel = self.bot.get_channel(int(room["channel"])) or await self.bot.fetch_channel(int(room["channel"]))
                await self.bind_card("session", session["id"], channel)
                job["session"] = session["id"]
                job["state"] = "DONE"
            except Exception as error:
                if isinstance(error, ValueError):
                    log.info("カスタムマップを起動できない: %s", error)
                else:
                    log.exception("カスタムマップの初期化・起動が失敗した")
                job["state"], job["error"] = "FAILED", str(error)[:500]
            finally:
                await self.service.put("job", job)
                if job["state"] == "DONE":
                    result = "ワールドを起動しました。このチャンネルのプレイ画面に接続先を表示しています。"
                elif session:
                    result = "ワールドは起動しましたが、プレイ画面を投稿できませんでした。管理者に連絡してください。\n" + job["error"]
                else:
                    result = "ワールドを起動できませんでした。\n" + job["error"]
                try:
                    await interaction.edit_original_response(content=result, allowed_mentions=discord.AllowedMentions.none())
                except discord.HTTPException:
                    log.warning("起動結果の個別メッセージを更新できない: %s", ident)
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
                raise ValueError("ワールド名が一致しません。表示されている名前を入力してください。")
            await self.service.delete(ident, i.user.id, expected=session.get("control_generation", 0), purge=values["purge"])
            await self.update_card("session", ident)
            await self.show(i, await self.object_view(i, "session", ident))
        form = Form("ワールドを削除", self, interaction.user.id, submit)
        form.field("name", "削除するワールド名を入力", discord.ui.TextInput(max_length=60), session["name"][:100])
        form.field("purge", "ごみ箱を使わず完全に削除する", discord.ui.Checkbox(default=False), "チェックすると元に戻せません。通常はごみ箱に移動し、7日後に削除されます。")
        await interaction.response.send_modal(form)

    async def open_sessions(self, interaction: discord.Interaction) -> None:
        await self.history_view(interaction, paused=True)

    async def open_history(self, interaction: discord.Interaction) -> None:
        await self.history_view(interaction)

    async def history_view(self, interaction: discord.Interaction, *, paused: bool = False, page: int = 0) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        sessions = await self.service.rows("session", interaction.guild_id)
        visible = []
        for item in sessions:
            if paused and (item["state"] not in ("PAUSED", "RUNNING", "FAILED", "COMPLETED", "DELETED") or item.get("data_available") == "none"):
                continue
            if not paused and (item["channel"] != str(interaction.channel_id) or not item.get("actual_players")):
                continue
            try:
                await self.permitted(interaction, item)
                visible.append(item)
            except ValueError:
                continue
        screen = Screen(self, owner=interaction.user.id)
        screen.text("# " + ("保存したワールド" if paused else "このチャンネルのプレイ履歴") + f"\n{len(visible)}件" + ("\nまだワールドがありません。マップを選んでプレイを始めてください。" if paused and not visible else "\nまだプレイ履歴がありません。" if not visible else ""))
        for item in visible[page * 5:page * 5 + 5]:
            async def detail(i: discord.Interaction, ident=item["id"]) -> None:
                await self.show(i, await self.object_view(i, "session", ident))
            screen.box.add_item(discord.ui.Section(discord.ui.TextDisplay(f"**{safe(item['name'])}**\n{safe(item['map']['title'])} / {STATES.get(item['state'], item['state'])}"), accessory=screen.button("開く", detail, "cm:history:" + item["id"])))
        async def prev(i: discord.Interaction) -> None:
            await self.history_view(i, paused=paused, page=max(0, page - 1))
        async def next_page(i: discord.Interaction) -> None:
            await self.history_view(i, paused=paused, page=page + 1)
        screen.row(screen.button("前へ", prev, "cm:history:prev", disabled=page == 0), screen.button("次へ", next_page, "cm:history:next", disabled=(page + 1) * 5 >= len(visible)), screen.button("ホーム", self.open_home, "cm:history:home"))
        await self.show(interaction, screen)

    async def backup_view(self, interaction: discord.Interaction, ident: str) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        session = await self.service.get("session", ident)
        await self.permitted(interaction, session, control=True)
        screen = Screen(self, owner=interaction.user.id)
        screen.text(f"# バックアップ\n**{safe(session['name'])}**\nプレイ中に作成すると、保存のためサーバーが一時停止し、完了後に再開します。\n復元すると別のワールドを作成します。元のワールドは上書きしません。")
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
            screen.box.add_item(discord.ui.Section(discord.ui.TextDisplay(f"<t:{int(record['created'])}:f> / {'Notionに保存済み' if record.get('uploaded') else 'サーバーに保存済み'}"), accessory=screen.button("新しいワールドとして復元", restore, "cm:backup:" + record["id"])))
        screen.text("最新5件を表示しています。過去のバックアップは /escape restore から復元できます。" if records else "まだバックアップがありません。")
        await self.show(interaction, screen)

    @app_commands.command(name="backups", description="ワールドのバックアップを作成・復元する")
    async def backups_command(self, interaction: discord.Interaction, session_id: str) -> None:
        await self.backup_view(interaction, session_id)

    @app_commands.command(name="restore", description="バックアップから新しいワールドを復元する")
    async def restore_command(self, interaction: discord.Interaction, backup_id: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        record = await self.service.get("backup", backup_id)
        original = await self.service.get("session", record["session_id"])
        await self.permitted(interaction, original, control=True)
        restored = await self.service.restore_backup(backup_id, interaction.user.id)
        channel = self.bot.get_channel(int(restored["channel"])) or await self.bot.fetch_channel(int(restored["channel"]))
        await self.bind_card("session", restored["id"], channel)
        await self.show(interaction, await self.object_view(interaction, "session", restored["id"]))

    @app_commands.command(name="undo", description="クリアを取り消す・ごみ箱からワールドを戻す")
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
                if binding["type"] in ("panel", "room", "session"):
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

    @app_commands.command(name="maps", description="人数・難易度・所要時間でマップを探す")
    @app_commands.choices(difficulty=[app_commands.Choice(name=label, value=n) for n, label in enumerate(LEVELS)])
    @app_commands.choices(sort=[app_commands.Choice(name=v, value=k) for k, v in SORTS.items()])
    async def maps(self, interaction: discord.Interaction, players: app_commands.Range[int, 1, 100] | None = None, difficulty: int = 0, max_minutes: app_commands.Range[int, 1, 100000] | None = None, keyword: str = "", sort: str = "popular", compact: bool = False) -> None:
        await self.search_results(interaction, {"players": players, "difficulty_max": difficulty or None, "max_minutes": max_minutes, "keyword": keyword[:100], "sort": sort, "compact": compact})

    @app_commands.command(name="history", description="カスタムマップのプレイ履歴を見る")
    async def history(self, interaction: discord.Interaction) -> None:
        await self.history_view(interaction)

    @app_commands.command(name="sessions", description="保存したワールドの一覧を開く")
    async def sessions(self, interaction: discord.Interaction) -> None:
        await self.open_sessions(interaction)

    @app_commands.command(name="create", description="マップを選んで参加者を募集する")
    async def create(self, interaction: discord.Interaction, map_id: str) -> None:
        await self.room_form(interaction, await self.service.map_info(map_id))

    @create.autocomplete("map_id")
    async def map_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        available = await asyncio.to_thread(self.service.available)
        return [app_commands.Choice(name=x["title"][:100], value=x["id"]) for x in self.service.catalog.search({"keyword": current}, available=available)[:25]]

    @app_commands.command(name="resume", description="中断したワールドの再開準備をする")
    async def resume(self, interaction: discord.Interaction, session_id: str) -> None:
        session = await self.service.get("session", session_id)
        await self.permitted(interaction, session, control=True)
        await self.room_form(interaction, session["map"], session)

    @app_commands.command(name="start", description="ワールドを起動して接続先を表示する")
    async def start(self, interaction: discord.Interaction, room_id: str) -> None:
        room = await self.service.get("room", room_id)
        await self.launch(interaction, room_id, room["generation"])

    @app_commands.command(name="setup", description="マップに必要なバージョンやリソースパックを確認する")
    async def setup_command(self, interaction: discord.Interaction, map_id: str) -> None:
        await self.setup_view(interaction, await self.service.map_info(map_id))

    @app_commands.command(name="panel", description="このチャンネルにカスタムマップのメニューを設置する")
    async def panel(self, interaction: discord.Interaction) -> None:
        if not check_role(interaction, "escape_manage"):
            raise app_commands.CheckFailure()
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bind_card("panel", str(interaction.channel_id), interaction.channel)
        await interaction.edit_original_response(content="カスタムマップのメニューを設置しました。")

    @app_commands.command(name="recover", description="停止に失敗したワールドや投稿先を復旧する（管理者用）")
    async def recover(self, interaction: discord.Interaction, session_id: str, channel: discord.TextChannel | None = None) -> None:
        if not check_role(interaction, "escape_manage"):
            raise app_commands.CheckFailure()
        session = await self.service.get("session", session_id)
        if str(interaction.guild_id) != session["guild"]:
            raise ValueError("別のDiscordサーバーのワールドは復旧できません。")
        await interaction.response.defer(ephemeral=True, thinking=True)
        if channel:
            self.service.check_channel(channel.id)
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
                    raise ValueError("ワールドの保存先を確認できません。ファイルの状態を確認してから復旧してください。")
                await asyncio.to_thread(self.service._detach, session)
            self.bot.server_manager._set_desired(session["slot"], False)
            session.update(slot=None, state="PAUSED", save_error_seen=False, control_generation=session.get("control_generation", 0) + 1, error="異常終了後に復旧したワールドです。再開後に進行状況を確認してください。")
            await self.service.put("session", session)
        await interaction.edit_original_response(content="更新しました。/escape sessions でワールドの状態を確認してください。")


async def setup(bot: commands.Bot) -> None:
    if CONFIG.get("custom_maps", {}).get("enabled", False):
        await bot.add_cog(CustomMaps(bot))
