import logging
import discord
from discord import app_commands
from discord.ext import commands
from settings import DISCORD_OWNER_ID, CONFIG
from utils.permissions import (
    check_role,
    add_user_permission,
    remove_user_permission,
    add_role_permission,
    remove_role_permission,
    get_all_actions,
)


class PermissionSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    permission_group = app_commands.Group(
        name="permission",
        description="権限管理コマンド"
    )

    @permission_group.command(name="list", description="現在の権限設定を表示します")
    async def permission_list(self, interaction: discord.Interaction):
        if not check_role(interaction, 'rcon'):
            return await interaction.response.send_message("権限がありません。", ephemeral=True)

        embed = discord.Embed(title="権限設定一覧", color=0x00ff00)

        # ロール権限
        role_text = ""
        for action, roles in CONFIG['permissions'].items():
            role_text += f"**{action}**: {', '.join(roles) if roles else '(なし)'}\n"
        embed.add_field(name="ロール権限", value=role_text or "(なし)", inline=False)

        # ユーザー権限
        user_permissions = CONFIG.get('user_permissions', {})
        if user_permissions:
            user_text = ""
            for user_id, actions in user_permissions.items():
                user_text += f"<@{user_id}>: {', '.join(actions)}\n"
            embed.add_field(name="ユーザー権限", value=user_text, inline=False)
        else:
            embed.add_field(name="ユーザー権限", value="(なし)", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)
        logging.info(f"User {interaction.user} ({interaction.user.id}) executed /permission list")

    @permission_group.command(name="user", description="ユーザーの権限を設定します (Ownerのみ)")
    @app_commands.describe(
        user="対象ユーザー",
        action="権限アクション",
        mode="許可/拒否"
    )
    async def permission_user(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        action: str,
        mode: str
    ):
        if not DISCORD_OWNER_ID or interaction.user.id != DISCORD_OWNER_ID:
            return await interaction.response.send_message(
                "権限がありません。Ownerのみが使用できます。",
                ephemeral=True
            )

        # アクションの検証
        valid_actions = get_all_actions()
        if action not in valid_actions:
            return await interaction.response.send_message(
                f"無効なアクションです。有効なアクション: {', '.join(valid_actions)}",
                ephemeral=True
            )

        if mode.lower() in ['allow', '許可', 'add']:
            success = add_user_permission(user.id, action)
            if success:
                await interaction.response.send_message(
                    f"✅ {user.mention} に `{action}` 権限を付与しました。",
                    ephemeral=True
                )
                logging.info(
                    f"Owner {interaction.user} ({interaction.user.id}) "
                    f"granted {action} to user {user} ({user.id})"
                )
            else:
                await interaction.response.send_message(
                    "❌ 設定の保存に失敗しました。",
                    ephemeral=True
                )
        elif mode.lower() in ['deny', '拒否', 'remove']:
            success = remove_user_permission(user.id, action)
            if success:
                await interaction.response.send_message(
                    f"✅ {user.mention} から `{action}` 権限を削除しました。",
                    ephemeral=True
                )
                logging.info(
                    f"Owner {interaction.user} ({interaction.user.id}) "
                    f"revoked {action} from user {user} ({user.id})"
                )
            else:
                await interaction.response.send_message(
                    "❌ 設定の保存に失敗しました。",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "無効なモードです。`allow` または `deny` を指定してください。",
                ephemeral=True
            )

    @permission_user.autocomplete('action')
    async def action_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> list[app_commands.Choice[str]]:
        actions = get_all_actions()
        return [
            app_commands.Choice(name=action, value=action)
            for action in actions
            if current.lower() in action.lower()
        ][:25]

    @permission_user.autocomplete('mode')
    async def mode_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> list[app_commands.Choice[str]]:
        modes = [
            app_commands.Choice(name="許可 (allow)", value="allow"),
            app_commands.Choice(name="拒否 (deny)", value="deny"),
        ]
        return [m for m in modes if current.lower() in m.name.lower()]

    @permission_group.command(name="role", description="ロールの権限を設定します (Ownerのみ)")
    @app_commands.describe(
        role="対象ロール (admin/mod/user)",
        action="権限アクション",
        mode="許可/拒否"
    )
    async def permission_role(
        self,
        interaction: discord.Interaction,
        role: str,
        action: str,
        mode: str
    ):
        if not DISCORD_OWNER_ID or interaction.user.id != DISCORD_OWNER_ID:
            return await interaction.response.send_message(
                "権限がありません。Ownerのみが使用できます。",
                ephemeral=True
            )

        # ロールの検証
        valid_roles = ['admin', 'mod', 'user']
        if role not in valid_roles:
            return await interaction.response.send_message(
                f"無効なロールです。有効なロール: {', '.join(valid_roles)}",
                ephemeral=True
            )

        # アクションの検証
        valid_actions = get_all_actions()
        if action not in valid_actions:
            return await interaction.response.send_message(
                f"無効なアクションです。有効なアクション: {', '.join(valid_actions)}",
                ephemeral=True
            )

        if mode.lower() in ['allow', '許可', 'add']:
            add_role_permission(role, action)
            await interaction.response.send_message(
                f"✅ ロール `{role}` に `{action}` 権限を付与しました。\n"
                f"⚠️ この変更は一時的です（Bot再起動で消失）。永続化するには`config.json`を編集してください。",
                ephemeral=True
            )
            logging.info(
                f"Owner {interaction.user} ({interaction.user.id}) "
                f"granted {action} to role {role} (temporary)"
            )
        elif mode.lower() in ['deny', '拒否', 'remove']:
            remove_role_permission(role, action)
            await interaction.response.send_message(
                f"✅ ロール `{role}` から `{action}` 権限を削除しました。\n"
                f"⚠️ この変更は一時的です（Bot再起動で消失）。永続化するには`config.json`を編集してください。",
                ephemeral=True
            )
            logging.info(
                f"Owner {interaction.user} ({interaction.user.id}) "
                f"revoked {action} from role {role} (temporary)"
            )
        else:
            await interaction.response.send_message(
                "無効なモードです。`allow` または `deny` を指定してください。",
                ephemeral=True
            )

    @permission_role.autocomplete('role')
    async def role_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> list[app_commands.Choice[str]]:
        roles = ['admin', 'mod', 'user']
        return [
            app_commands.Choice(name=r, value=r)
            for r in roles
            if current.lower() in r.lower()
        ]

    @permission_role.autocomplete('action')
    async def role_action_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> list[app_commands.Choice[str]]:
        actions = get_all_actions()
        return [
            app_commands.Choice(name=action, value=action)
            for action in actions
            if current.lower() in action.lower()
        ][:25]

    @permission_role.autocomplete('mode')
    async def role_mode_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str
    ) -> list[app_commands.Choice[str]]:
        modes = [
            app_commands.Choice(name="許可 (allow)", value="allow"),
            app_commands.Choice(name="拒否 (deny)", value="deny"),
        ]
        return [m for m in modes if current.lower() in m.name.lower()]


async def setup(bot):
    await bot.add_cog(PermissionSystem(bot))
