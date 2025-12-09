import asyncio
import subprocess
import logging
import psutil
from settings import CONFIG

class ServerManager:
    def __init__(self):
        self.process = None
        self.log_queue = asyncio.Queue()

    async def start_server(self):
        if self.is_running():
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
        # Force terminate any existing java processes with this jar
        stopped_something = False
        
        if self.process and self.process.returncode is None:
            if self.process.stdin:
                try:
                    self.process.stdin.write(b"stop\n")
                    await self.process.stdin.drain()
                    stopped_something = True
                except Exception:
                    pass
        
        # Also check via psutil for any orphan processes
        jar_name = CONFIG["minecraft_server_jar"]
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info['cmdline']
                if cmdline and 'java' in proc.info['name'] and jar_name in cmdline:
                     # Send SIGTERM (or input 'stop' if possible, but difficult to attach stdin here)
                     # For simplicity, we assume if self.process was None, we might not be able to send 'stop' elegantly.
                     # But most MC servers handle SIGTERM gracefully-ish or we can try to find the one we started.
                     # If we can't write to stdin, we might have to kill it.
                     # Ideally, we should try to attach or just kill.
                     # Given the user wants to ensure it stops:
                     proc.terminate()
                     stopped_something = True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
                
        if stopped_something:
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
            
    def _get_server_process(self):
        """現在実行中のサーバープロセスを取得する"""
        # 1. 自身が起動したプロセスをチェック
        if self.process is not None and self.process.returncode is None:
            # pidからpsutil.Processを取得
            try:
                return psutil.Process(self.process.pid)
            except psutil.NoSuchProcess:
                pass

        # 2. プロセスリストから検索
        jar_name = CONFIG["minecraft_server_jar"]
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time']):
            try:
                cmdline = proc.info['cmdline']
                if cmdline and 'java' in proc.info['name'] and jar_name in cmdline:
                    return proc
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return None

    def is_running(self):
        return self._get_server_process() is not None

    def get_server_stats(self):
        """サーバーのステータス(CPU, Memory, Uptime)を取得"""
        proc = self._get_server_process()
        if not proc:
            return None
        
        try:
            # CPU% (interval=None for non-blocking since last call, but first call allows 0.0)
            cpu_percent = proc.cpu_percent(interval=None)
            
            # Memory (RSS) in MB
            mem_info = proc.memory_info()
            mem_mb = mem_info.rss / (1024 * 1024)
            
            # Uptime
            import time
            create_time = proc.create_time()
            uptime_seconds = time.time() - create_time
            
            return {
                "cpu_percent": cpu_percent,
                "memory_mb": mem_mb,
                "uptime_seconds": uptime_seconds
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        
    async def wait_for_exit(self):
        if self.process:
            await self.process.wait()
        
        # Also wait for psutil processes to clear if necessary
        # (Polling)
        while self.is_running():
            await asyncio.sleep(1)
