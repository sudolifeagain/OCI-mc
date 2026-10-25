import unittest
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from requests import Request, Response

from utils.notion_api import MAX_RETRIES, RETRY_AFTER_CAP, _request_with_retry, register_to_database, upload_to_notion


def make_response(status_code: int, retry_after: str | None = None) -> Response:
    response = Response()
    response.status_code = status_code
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return response


class NotionRetryTests(unittest.TestCase):
    def check_upload_content_type(self, size: int, parts: int) -> None:
        requests_sent = []

        def send(method, url, **kwargs):
            requests_sent.append((url, kwargs))
            response = make_response(200)
            response._content = b'{"id":"test-upload","upload_url":"https://api.notion.com/v1/file_uploads/test-upload/send"}'
            return response

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.zip"
            with path.open("wb") as stream:
                stream.truncate(size)
            with patch("utils.notion_api._request_with_retry", side_effect=send):
                self.assertEqual(upload_to_notion(str(path)), "test-upload")

        declared_type = requests_sent[0][1]["json"]["content_type"]
        sends = [kwargs for url, kwargs in requests_sent if url.endswith("/send")]
        self.assertEqual(len(sends), parts)
        self.assertEqual(sum(len(kwargs["files"]["file"][1]) for kwargs in sends), size)
        for kwargs in sends:
            request = Request("POST", "https://api.notion.com/test", headers=kwargs["headers"], files=kwargs["files"], data=kwargs.get("data")).prepare()
            self.assertIn(b"Content-Type: " + declared_type.encode() + b"\r\n", request.body)
        if parts > 1:
            self.assertEqual([kwargs["data"]["part_number"] for kwargs in sends], [str(i) for i in range(1, parts + 1)])
            self.assertTrue(requests_sent[-1][0].endswith("/complete"))

    def test_small_zip_send_matches_declared_content_type(self) -> None:
        self.check_upload_content_type(1024, 1)

    def test_multipart_zip_send_matches_declared_content_type(self) -> None:
        self.check_upload_content_type(21 * 1024 * 1024, 3)

    @patch("utils.notion_api.get_data_source_id", return_value="test-data-source")
    @patch("utils.notion_api._request_with_retry", return_value=make_response(200))
    def test_backup_registration_includes_timezone(self, request_mock: Mock, source_mock: Mock) -> None:
        self.assertTrue(register_to_database("test-upload", "backup.zip", 1))
        payload = request_mock.call_args.kwargs["json"]
        date = datetime.fromisoformat(payload["properties"]["Date"]["date"]["start"])
        self.assertIsNotNone(date.utcoffset())

    @patch("utils.notion_api.time.sleep")
    def test_529_respects_retry_after(self, sleep_mock: Mock) -> None:
        method = Mock(
            side_effect=[
                make_response(529, "7"),
                make_response(200),
            ]
        )

        response = _request_with_retry(method, "https://api.notion.com/test")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(method.call_count, 2)
        sleep_mock.assert_called_once_with(7)

    @patch("utils.notion_api.time.sleep")
    def test_529_caps_retry_after(self, sleep_mock: Mock) -> None:
        method = Mock(
            side_effect=[
                make_response(529, str(RETRY_AFTER_CAP + 1)),
                make_response(200),
            ]
        )

        response = _request_with_retry(method, "https://api.notion.com/test")

        self.assertEqual(response.status_code, 200)
        sleep_mock.assert_called_once_with(RETRY_AFTER_CAP)

    @patch("utils.notion_api.time.sleep")
    def test_last_529_response_returns_without_extra_sleep(
        self, sleep_mock: Mock
    ) -> None:
        method = Mock(
            return_value=make_response(529, "1"),
        )

        response = _request_with_retry(method, "https://api.notion.com/test")

        self.assertEqual(response.status_code, 529)
        self.assertEqual(method.call_count, MAX_RETRIES)
        self.assertEqual(sleep_mock.call_count, MAX_RETRIES - 1)


if __name__ == "__main__":
    unittest.main()
