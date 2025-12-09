import asyncio
import subprocess
import logging
from settings import CONFIG

class ServerManager:
    def __init__(self):
        self.process = None
        self.log_queue = asyncio.Queue()

    async def start_server(self):
        if self.process and self.process.returncode is None:
            return False # Already running

        mem = CONFIG["java_memory"]
        jar = CONFIG["minecraft_server_jar"]
        cmd = ['java', f'-Xmx{mem}', f'-Xms{mem}', '-jar', jar, 'nogui']

        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd="/opt/minecraft",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        
        # Start reading stdout
        asyncio.create_task(self._read_stdout())
        return True

    async def stop_server(self):
        if self.process and self.process.returncode is None:
            if self.process.stdin:
                self.process.stdin.write(b"stop\n")
                await self.process.stdin.drain()
            return True
        return False

    async def write_stdin(self, command_str):
        if self.process and self.process.returncode is None:
            if self.process.stdin:
                self.process.stdin.write(f"{command_str}\n".encode())
                await self.process.stdin.drain()
                return True
        return False

    async def _read_stdout(self):
        """標準出力を非同期で読み取り、Queueに入れる"""
        if not self.process or not self.process.stdout:
            return

        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            await self.log_queue.put(line.decode('utf-8', errors='ignore'))
            
    def is_running(self):
        return self.process is not None and self.process.returncode is None
        
    async def wait_for_exit(self):
        if self.process:
            await self.process.wait()
