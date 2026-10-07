import asyncio
import struct
import unittest

from utils.rcon import RconClient


def packet(identifier: int, payload: str) -> bytes:
    data = struct.pack("<ii", identifier, 0) + payload.encode() + b"\x00\x00"
    return struct.pack("<i", len(data)) + data


class RconResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_multipart_utf8_response_is_complete(self) -> None:
        first = "あ" * 4096
        writes = []
        testcase = self
        first_response_read = False

        class Reader(asyncio.StreamReader):
            async def readexactly(self, length: int) -> bytes:
                nonlocal first_response_read
                data = await super().readexactly(length)
                if length > 4:
                    first_response_read = True
                return data

        reader = Reader()
        reader.feed_data(packet(2, first) + packet(2, "remaining") + packet(3, ""))

        class Writer:
            def write(self, data: bytes) -> None:
                if struct.unpack_from("<i", data, 4)[0] == 3:
                    testcase.assertTrue(first_response_read)
                writes.append(data)

            async def drain(self) -> None:
                return None

        client = RconClient("localhost", 1, "password")
        response = await client._send_command(reader, Writer(), "datapack list")
        self.assertEqual(response, first + "remaining")
        self.assertEqual(len(writes), 2)

    async def test_unexpected_response_id_is_rejected(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(packet(99, "unexpected"))

        class Writer:
            def write(self, data: bytes) -> None:
                return None

            async def drain(self) -> None:
                return None

        with self.assertRaises(ValueError):
            await RconClient("localhost", 1, "password")._send_command(reader, Writer(), "list")

    async def test_stop_acknowledgement_survives_server_disconnect(self) -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(packet(2, "Stopping server"))
        reader.feed_eof()

        class Writer:
            def write(self, data: bytes) -> None:
                return None

            async def drain(self) -> None:
                return None

        result = await RconClient("localhost", 1, "password")._send_command(reader, Writer(), "stop")
        self.assertEqual(result, "Stopping server")


if __name__ == "__main__":
    unittest.main()
