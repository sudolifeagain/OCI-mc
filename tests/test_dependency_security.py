"""依存更新がHTTP応答の既知不具合を修復していることを確認する。"""

import asyncio
import subprocess
import sys
import unittest

from aiohttp import http_exceptions
from aiohttp.client_proto import ResponseHandler
from aiohttp.http_parser import HttpResponseParserC


class DependencySecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_chunked_response_has_bounded_error_snippet(self) -> None:
        loop = asyncio.get_running_loop()
        header = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        for body, snippet in ((b"0\rX", b"0\rX"), (b"0_", b"0_")):
            with self.subTest(body=body):
                protocol = ResponseHandler(loop)
                parser = HttpResponseParserC(protocol, loop, 2**16)
                protocol._parser = parser
                with self.assertRaises(http_exceptions.BadHttpMessage) as raised:
                    parser.feed_data(header + body)
                self.assertIn(repr(snippet), str(raised.exception))

    async def test_fragmented_malformed_chunked_response_is_rejected(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = ResponseHandler(loop)
        parser = HttpResponseParserC(protocol, loop, 2**16)
        protocol._parser = parser
        parser.feed_data(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
        parser.feed_data(b"5\r\nhell")
        with self.assertRaises(http_exceptions.BadHttpMessage):
            parser.feed_data(b"o\rX")

    async def test_valid_chunked_response_is_accepted(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = ResponseHandler(loop)
        parser = HttpResponseParserC(protocol, loop, 2**16)
        protocol._parser = parser
        messages, upgrade, tail = parser.feed_data(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n0\r\n\r\n"
        )
        self.assertEqual(await messages[0][1].read(), b"hello")
        self.assertFalse(upgrade)
        self.assertEqual(tail, b"")


class DeflateStreamingTests(unittest.TestCase):
    def test_chunked_deflate_with_trailing_bytes_finishes(self) -> None:
        # 不具合のある版ではループするため、別プロセスに時間上限を設定する。
        code = '''
import http.client, io, zlib
from urllib3.response import HTTPResponse
class Socket:
    def makefile(self, *args, **kwargs):
        return io.BytesIO()
original = b"A" * 100
for trailer in (b"", b"tail"):
    encoded = zlib.compress(original) + trailer
    body = http.client.HTTPResponse(Socket())
    body.fp = io.BytesIO(f"{len(encoded):x}\\r\\n".encode() + encoded + b"\\r\\n0\\r\\n\\r\\n")
    response = HTTPResponse(body, preload_content=False, headers={
        "transfer-encoding": "chunked", "content-encoding": "deflate"})
    assert b"".join(response.stream(50, decode_content=True)) == original
'''
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
