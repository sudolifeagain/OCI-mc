"""
RCON client for Minecraft servers.
Provides async wrapper around mcrcon library.
"""
import asyncio
import logging
import os
from typing import Optional, Tuple

from mcrcon import MCRcon, MCRconException


class RconClient:
    """Async RCON client for Minecraft servers."""

    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password

    async def execute(self, command: str) -> Tuple[bool, str]:
        """
        Execute a command via RCON and return the result.

        Args:
            command: The command to execute

        Returns:
            Tuple of (success, response_or_error)
        """
        try:
            result = await asyncio.to_thread(self._execute_sync, command)
            return True, result
        except MCRconException as e:
            logging.error(f"RCON error: {e}")
            return False, f"RCON error: {e}"
        except ConnectionRefusedError:
            return False, "Connection refused. Is RCON enabled on the server?"
        except TimeoutError:
            return False, "Connection timed out."
        except Exception as e:
            logging.error(f"Unexpected RCON error: {e}")
            return False, f"Unexpected error: {e}"

    def _execute_sync(self, command: str) -> str:
        """Synchronous RCON execution."""
        with MCRcon(self.host, self.password, port=self.port) as mcr:
            return mcr.command(command)


def get_rcon_client(server_config: dict) -> Optional[RconClient]:
    """
    Create an RconClient from server configuration.

    Args:
        server_config: Server configuration dict from config.json

    Returns:
        RconClient instance or None if RCON is not configured
    """
    rcon_port = server_config.get("rcon_port")
    password_env = server_config.get("rcon_password_env")

    if not rcon_port or not password_env:
        return None

    password = os.getenv(password_env)
    if not password:
        logging.warning(f"RCON password not found in env: {password_env}")
        return None

    # RCON connects to localhost since bot runs on same server
    return RconClient("localhost", rcon_port, password)
