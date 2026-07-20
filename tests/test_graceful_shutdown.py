import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.graceful_shutdown import stop_server


class GracefulShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_server_needs_no_rcon(self) -> None:
        with patch("scripts.graceful_shutdown.port_is_open", return_value=False):
            self.assertTrue(await stop_server("paper", {"port": 25565}))

    async def test_online_server_stops_through_rcon(self) -> None:
        client = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    (True, "There are 0 of a max of 20 players online"),
                    (False, "Connection closed unexpectedly"),
                ]
            )
        )
        config = {
            "port": 25565,
            "rcon_port": 25575,
            "rcon_password_env": "PAPER_RCON_PASSWORD",
        }
        with (
            patch("scripts.graceful_shutdown.port_is_open", return_value=True),
            patch("scripts.graceful_shutdown.wait_for_port_close", AsyncMock(return_value=True)),
            patch("utils.rcon.get_rcon_client", return_value=client),
        ):
            self.assertTrue(await stop_server("paper", config))

        self.assertEqual(client.execute.await_args_list[0].args, ("list",))
        self.assertEqual(client.execute.await_args_list[1].args, ("stop",))


if __name__ == "__main__":
    unittest.main()
