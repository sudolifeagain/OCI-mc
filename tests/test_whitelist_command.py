import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from cogs.basic_control import BasicControl


class WhitelistCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = SimpleNamespace(name='azb', cwd='/tmp/azb')
        self.cog = BasicControl.__new__(BasicControl)
        self.cog._whitelist_lock = asyncio.Lock()
        self.cog.server_manager = SimpleNamespace(
            get_server=Mock(return_value=self.server),
            is_running=Mock(return_value=True),
        )
        self.interaction = SimpleNamespace(
            user=SimpleNamespace(id=1),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        self.client = SimpleNamespace(execute=AsyncMock())

    async def invoke(self) -> None:
        with patch('cogs.basic_control.is_allowed_command_context', return_value=True), \
             patch('cogs.basic_control.check_role', return_value=True), \
             patch('cogs.basic_control.get_rcon_client', return_value=self.client):
            await BasicControl.whitelist_add.callback(self.cog, self.interaction, 'Player', 'paper-new')

    async def test_defers_before_waiting_and_reports_server_result(self) -> None:
        async def execute(command: str) -> tuple[bool, str]:
            self.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            self.assertEqual(command, 'whitelist add Player')
            return True, 'Added Player to the whitelist'
        self.client.execute.side_effect = execute
        await self.invoke()
        self.assertIn('Added Player to the whitelist', self.interaction.edit_original_response.await_args.kwargs['content'])
        self.interaction.response.send_message.assert_not_awaited()

    async def test_rcon_rejection_uses_verified_profile_and_checks_reload(self) -> None:
        self.client.execute.side_effect = [
            (True, 'That player does not exist'),
            (True, 'Reloaded the whitelist'),
            (True, 'There are 1 whitelisted player(s): Player'),
        ]
        profile = {'name': 'Player', 'uuid': '11111111-1111-1111-1111-111111111111'}
        with patch('cogs.basic_control.resolve_java_profile', return_value=profile),              patch('cogs.basic_control.add_verified_profile') as add:
            await self.invoke()
        add.assert_called_once_with('/tmp/azb', profile)
        self.assertIn('公式プロフィールを照合', self.interaction.edit_original_response.await_args.kwargs['content'])

    async def test_fallback_lookup_failure_is_not_reported_as_success(self) -> None:
        self.client.execute.return_value = (True, 'That player does not exist')
        with patch('cogs.basic_control.resolve_java_profile', side_effect=ValueError('名前を確認してください')):
            await self.invoke()
        content = self.interaction.edit_original_response.await_args.kwargs['content']
        self.assertIn('失敗', content)
        self.assertNotIn('追加しました', content)

    async def test_failed_reload_is_not_reported_as_success(self) -> None:
        self.client.execute.side_effect = [(True, 'That player does not exist'), (False, 'timeout')]
        with patch('cogs.basic_control.resolve_java_profile', return_value={'name': 'Player', 'uuid': '11111111-1111-1111-1111-111111111111'}),              patch('cogs.basic_control.add_verified_profile'):
            await self.invoke()
        self.assertIn('再読込を確認できません', self.interaction.edit_original_response.await_args.kwargs['content'])

    async def test_both_registers_each_edition_and_reports_both(self) -> None:
        self.client.execute.side_effect = [
            (True, 'Added Player to the whitelist'),
            (True, 'Reloaded the whitelist'),
            (True, 'There are 2 whitelisted players: Player, .Player'),
        ]
        profile = {'name': '.Player', 'uuid': '00000000-0000-0000-0009-123456789abc'}
        with patch('cogs.basic_control.is_allowed_command_context', return_value=True), \
             patch('cogs.basic_control.check_role', return_value=True), \
             patch('cogs.basic_control.get_rcon_client', return_value=self.client), \
             patch('cogs.basic_control.resolve_bedrock_profile', return_value=profile), \
             patch('cogs.basic_control.add_verified_profile') as add:
            await BasicControl.whitelist_add.callback(self.cog, self.interaction, 'Player', 'paper-new', 'both')
        add.assert_called_once_with('/tmp/azb', profile)
        content = self.interaction.edit_original_response.await_args.kwargs['content']
        self.assertIn('Java版: Added Player', content)
        self.assertIn('統合版: .Player を追加', content)

    async def test_both_reports_partial_failure_separately(self) -> None:
        self.client.execute.return_value = (True, 'Added Player to the whitelist')
        with patch('cogs.basic_control.is_allowed_command_context', return_value=True), \
             patch('cogs.basic_control.check_role', return_value=True), \
             patch('cogs.basic_control.get_rcon_client', return_value=self.client), \
             patch('cogs.basic_control.resolve_bedrock_profile', side_effect=ValueError('公式APIで照合できません')):
            await BasicControl.whitelist_add.callback(self.cog, self.interaction, 'Player', 'paper-new', 'both')
        content = self.interaction.edit_original_response.await_args.kwargs['content']
        self.assertIn('Java版: Added Player', content)
        self.assertIn('統合版: 登録に失敗', content)

    async def test_bedrock_only_never_adds_java_account(self) -> None:
        self.client.execute.side_effect = [(True, 'Reloaded the whitelist'), (True, 'Whitelist: .Player')]
        profile = {'name': '.Player', 'uuid': '00000000-0000-0000-0009-123456789abc'}
        with patch('cogs.basic_control.is_allowed_command_context', return_value=True), \
             patch('cogs.basic_control.check_role', return_value=True), \
             patch('cogs.basic_control.get_rcon_client', return_value=self.client), \
             patch('cogs.basic_control.resolve_bedrock_profile', return_value=profile), \
             patch('cogs.basic_control.add_verified_profile'):
            await BasicControl.whitelist_add.callback(self.cog, self.interaction, 'Player', 'paper-new', 'bedrock')
        commands = [c.args[0] for c in self.client.execute.await_args_list]
        self.assertEqual(commands, ['whitelist reload', 'whitelist list'])

    async def test_rcon_failure_is_reported(self) -> None:
        self.client.execute.return_value = (False, 'Connection timed out')
        await self.invoke()
        content = self.interaction.edit_original_response.await_args.kwargs['content']
        self.assertIn('失敗', content)
        self.assertIn('Connection timed out', content)

    async def test_empty_rcon_response_is_not_a_success_claim(self) -> None:
        self.client.execute.return_value = (True, '')
        await self.invoke()
        self.assertIn('登録結果を確認できません', self.interaction.edit_original_response.await_args.kwargs['content'])
