import os
import unittest
from unittest.mock import AsyncMock, patch

from utils.shell_runner import run_shell_command


class FakeStdout:
    def __init__(self, chunks: list[bytes]):
        self.chunks = iter(chunks)

    async def read(self, _size: int) -> bytes:
        return next(self.chunks, b"")


class FakeProcess:
    def __init__(self, chunks: list[bytes], returncode: int = 0):
        self.stdout = FakeStdout(chunks)
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class ShellRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_shell_is_wrapped_by_os_timeout_without_shell_true(self) -> None:
        process = FakeProcess([b"ok\n"])
        create = AsyncMock(return_value=process)

        with (
            patch("utils.shell_runner.asyncio.create_subprocess_exec", create),
            patch.dict(
                os.environ,
                {"PATH": "/usr/bin", "DISCORD_TOKEN": "secret"},
                clear=True,
            ),
        ):
            result = await run_shell_command("uname -a")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.output, "ok\n")
        args = create.await_args.args
        self.assertEqual(args[:6], (
            "/usr/bin/timeout",
            "--signal=TERM",
            "--kill-after=5s",
            "60s",
            "/bin/bash",
            "-lc",
        ))
        self.assertEqual(args[6], "uname -a")
        self.assertNotIn("shell", create.await_args.kwargs)
        self.assertEqual(create.await_args.kwargs["env"], {"PATH": "/usr/bin"})

    async def test_output_limit_terminates_process(self) -> None:
        process = FakeProcess([b"x" * (64 * 1024 + 1)])
        create = AsyncMock(return_value=process)

        with patch("utils.shell_runner.asyncio.create_subprocess_exec", create):
            result = await run_shell_command("yes")

        self.assertTrue(result.output_limited)
        self.assertTrue(process.terminated)
        self.assertEqual(len(result.output), 64 * 1024)


if __name__ == "__main__":
    unittest.main()
