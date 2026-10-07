import unittest
from unittest.mock import AsyncMock, Mock, patch

from scripts.verify_runtime import server_ready


class RuntimeVerificationTests(unittest.IsolatedAsyncioTestCase):
    @patch("scripts.verify_runtime.socket.create_connection")
    @patch("scripts.verify_runtime.get_rcon_client")
    async def test_game_port_open_without_rcon_is_not_ready(self, get_client: Mock, connect: Mock) -> None:
        get_client.return_value.execute = AsyncMock(return_value=(False, "Connection refused"))
        self.assertFalse(await server_ready({"port": 25566, "rcon_port": 25576}))

    @patch("scripts.verify_runtime.socket.create_connection")
    @patch("scripts.verify_runtime.get_rcon_client")
    async def test_authenticated_list_response_is_ready(self, get_client: Mock, connect: Mock) -> None:
        get_client.return_value.execute = AsyncMock(return_value=(True, "There are 0 players online"))
        self.assertTrue(await server_ready({"port": 25566, "rcon_port": 25576}))

    @patch("scripts.verify_runtime.socket.create_connection")
    @patch("scripts.verify_runtime.get_rcon_client", return_value=None)
    async def test_missing_credentials_are_not_ready(self, get_client: Mock, connect: Mock) -> None:
        self.assertFalse(await server_ready({"port": 25566, "rcon_port": 25576}))


if __name__ == "__main__":
    unittest.main()
