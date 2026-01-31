"""
RCON client for Minecraft servers.
Simple socket-based implementation using asyncio.
"""
import asyncio
import logging
import os
import struct
from typing import Optional, Tuple


class RconClient:
    """Async RCON client for Minecraft servers."""

    SERVERDATA_AUTH = 3
    SERVERDATA_EXECCOMMAND = 2
    MAX_PACKET_SIZE = 4096

    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password

    async def execute(self, command: str) -> Tuple[bool, str]:
        """
        Execute a command via RCON and return the result.

        Args:
            command: Raw Minecraft server console command to execute via RCON
                (for example: "say Hello", "list", "whitelist add <player>",
                "op <player>"). The command must be supported by the
                connected server's RCON interface and is sent as-is.

        Returns:
            Tuple[bool, str]: A pair ``(success, message)`` where:

                * ``success`` is ``True`` if the TCP connection, RCON
                  authentication, and command execution all completed without
                  detected errors; otherwise ``False``.
                * ``message`` is the raw textual response from the Minecraft
                  server when ``success`` is ``True`` (may be an empty string
                  if the command produced no output), or a human-readable
                  error description when ``success`` is ``False``.

        Error conditions:
            * Authentication failure: returns ``(False, "Authentication failed")``.
            * Connection timeout: returns ``(False, "Connection timed out")``.
            * Connection refused (e.g. RCON disabled or wrong port):
              returns ``(False, "Connection refused. Is RCON enabled on the server?")``.
            * Other unexpected exceptions: logged as an error and returned as
              ``(False, f"Error: {e}")``.
        """
        # 接続確立
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=10.0
            )
        except asyncio.TimeoutError:
            return False, "Connection timed out"
        except ConnectionRefusedError:
            return False, "Connection refused. Is RCON enabled on the server?"
        except Exception as e:
            logging.error(f"RCON connection error: {e}")
            return False, f"Error: {e}"

        # 認証とコマンド実行
        try:
            # 認証
            auth_result = await self._authenticate(reader, writer)
            if not auth_result:
                return False, "Authentication failed"

            # コマンド実行
            response = await self._send_command(reader, writer, command)
            return True, response or ""

        except asyncio.TimeoutError:
            return False, "Command timed out"
        except asyncio.IncompleteReadError:
            return False, "Connection closed unexpectedly"
        except Exception as e:
            logging.error(f"RCON error: {e}")
            return False, f"Error: {e}"
        finally:
            writer.close()
            await writer.wait_closed()

    async def _authenticate(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ) -> bool:
        """Authenticate with the RCON server."""
        request_id = 1

        # 認証パケット送信
        await self._write_packet(writer, request_id, self.SERVERDATA_AUTH, self.password)

        # 認証レスポンス読み取り
        response_id, _, _ = await self._read_packet(reader)

        if response_id == -1:
            return False

        # Source RCON仕様: 認証後にダミーパケットが送信される場合があるので読み捨て
        # Minecraftはこれを送信しないサーバーもあるため、タイムアウトを短くして試行
        try:
            await asyncio.wait_for(self._read_packet(reader), timeout=0.5)
        except asyncio.TimeoutError:
            pass  # ダミーパケットがない場合は無視

        return response_id == request_id

    async def _send_command(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        command: str
    ) -> str:
        """Send a command and return the response."""
        request_id = 2

        # コマンドパケット送信
        await self._write_packet(writer, request_id, self.SERVERDATA_EXECCOMMAND, command)

        # レスポンス読み取り
        response_id, _, payload = await self._read_packet(reader)

        # request_id照合
        if response_id != request_id:
            logging.warning(f"Request ID mismatch: expected {request_id}, got {response_id}")

        return payload

    async def _write_packet(
        self,
        writer: asyncio.StreamWriter,
        request_id: int,
        packet_type: int,
        payload: str
    ) -> None:
        """Write an RCON packet."""
        payload_bytes = payload.encode('utf-8') + b'\x00\x00'
        packet = struct.pack('<ii', request_id, packet_type) + payload_bytes
        length = len(packet)

        writer.write(struct.pack('<i', length) + packet)
        await writer.drain()

    async def _read_packet(
        self,
        reader: asyncio.StreamReader
    ) -> Tuple[int, int, str]:
        """
        Read an RCON packet.

        Returns:
            Tuple of (response_id, response_type, payload)
        """
        # パケット長読み取り
        length_data = await asyncio.wait_for(reader.readexactly(4), timeout=10.0)
        length = struct.unpack('<i', length_data)[0]

        # 最大パケットサイズ検証
        if length > self.MAX_PACKET_SIZE:
            raise ValueError(f"Packet too large: {length} bytes")

        if length < 10:  # 最小サイズ: ID(4) + Type(4) + Padding(2)
            raise ValueError(f"Packet too small: {length} bytes")

        # パケットデータ読み取り
        data = await asyncio.wait_for(reader.readexactly(length), timeout=10.0)

        # パケット解析
        response_id = struct.unpack('<i', data[0:4])[0]
        response_type = struct.unpack('<i', data[4:8])[0]

        # ペイロード抽出（末尾の2バイトはnullパディング）
        payload = data[8:-2].decode('utf-8', errors='replace')

        logging.debug(f"RCON response: id={response_id}, type={response_type}, len={len(payload)}")

        return response_id, response_type, payload


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
