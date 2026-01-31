"""
RCON client for Minecraft servers.
Simple socket-based implementation to avoid threading issues with mcrcon.
"""
import asyncio
import logging
import os
import struct
from typing import Optional, Tuple


class RconClient:
    """Async RCON client for Minecraft servers."""

    SERVERDATA_AUTH = 3
    SERVERDATA_AUTH_RESPONSE = 2
    SERVERDATA_EXECCOMMAND = 2
    SERVERDATA_RESPONSE_VALUE = 0

    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password
        self._request_id = 0

    async def execute(self, command: str) -> Tuple[bool, str]:
        """
        Execute a command via RCON and return the result.

        Returns:
            Tuple of (success, response_or_error)
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=10.0
            )

            try:
                # Authenticate
                auth_response = await self._send_packet(
                    reader, writer,
                    self.SERVERDATA_AUTH,
                    self.password
                )

                if auth_response is None:
                    return False, "Authentication failed"

                # Execute command
                response = await self._send_packet(
                    reader, writer,
                    self.SERVERDATA_EXECCOMMAND,
                    command
                )

                return True, response or ""

            finally:
                writer.close()
                await writer.wait_closed()

        except asyncio.TimeoutError:
            return False, "Connection timed out"
        except ConnectionRefusedError:
            return False, "Connection refused. Is RCON enabled on the server?"
        except Exception as e:
            logging.error(f"RCON error: {e}")
            return False, f"Error: {e}"

    async def _send_packet(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        packet_type: int,
        payload: str
    ) -> Optional[str]:
        """Send RCON packet and receive response."""
        self._request_id += 1
        request_id = self._request_id

        # Build packet: length + request_id + type + payload + padding
        payload_bytes = payload.encode('utf-8') + b'\x00\x00'
        packet = struct.pack('<ii', request_id, packet_type) + payload_bytes
        length = len(packet)

        # Send packet
        writer.write(struct.pack('<i', length) + packet)
        await writer.drain()

        # Read response
        try:
            length_data = await asyncio.wait_for(reader.read(4), timeout=10.0)
            if len(length_data) < 4:
                return None

            length = struct.unpack('<i', length_data)[0]
            data = await asyncio.wait_for(reader.read(length), timeout=10.0)

            if len(data) < 8:
                return None

            response_id = struct.unpack('<i', data[0:4])[0]
            _response_type = struct.unpack('<i', data[4:8])[0]  # noqa: F841

            # Auth response check
            if packet_type == self.SERVERDATA_AUTH:
                if response_id == -1:
                    return None  # Auth failed

            # Extract payload (remove null terminators)
            response_payload = data[8:-2].decode('utf-8', errors='replace')
            return response_payload

        except asyncio.TimeoutError:
            return None


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
