import asyncio
import subprocess
import logging
import time
import psutil
import re


class ServerInstance:
    """個別のMinecraftサーバーインスタンスを管理するクラス"""
    
    def __init__(self, server_id: str, config: dict):
        self.server_id = server_id
        self.name = config.get("name", server_id)
        self.jar = config.get("jar")
        self.use_script = config.get("use_script")  # run.sh などのスクリプトを使う場合
        self.cwd = config["cwd"]
        self.memory = config.get("memory", "4G")
        self.port = config.get("port", 25565)
        self.process = None
        self.log_queue = asyncio.Queue()
        self.online_players = {}  # {name: join_timestamp}
        # Regex patterns for join/leave
        self.join_pattern = re.compile(r': (.+) joined the game')
        self.leave_pattern = re.compile(r': (.+) left the game')
    
    async def start(self) -> bool:
        """サーバーを起動する"""
        if self.is_running():
            return False
        
        # スクリプトを使う場合（Forge等）
        if self.use_script:
            cmd = [self.use_script, 'nogui']
        else:
            # jar ファイルを直接起動（Paper等）
            cmd = ['java', f'-Xmx{self.memory}', f'-Xms{self.memory}', '-jar', self.jar, 'nogui']
        
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        
        asyncio.create_task(self._read_stdout())
        self.online_players.clear() # Reset on start
        logging.info(f"Server '{self.server_id}' started with PID {self.process.pid}")
        return True
    
    async def stop(self) -> bool:
        """サーバーを停止する"""
        stopped_something = False
        
        # 自分が起動したプロセスにstopコマンドを送信
        if self.process and self.process.returncode is None:
            if self.process.stdin:
                try:
                    self.process.stdin.write(b"stop\n")
                    await self.process.stdin.drain()
                    stopped_something = True
                    logging.info(f"Sent 'stop' command to server '{self.server_id}'")
                except Exception as e:
                    logging.warning(f"Failed to send stop command: {e}")
        
        # psutilでプロセスを検索して終了
        proc = self._get_process()
        if proc:
            try:
                proc.terminate()
                stopped_something = True
                logging.info(f"Terminated process for server '{self.server_id}'")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        return stopped_something
    
    async def write_stdin(self, command_str: str) -> bool:
        """サーバーコンソールにコマンドを送信する"""
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
            text = line.decode('utf-8', errors='ignore')
            
            # プレイヤー参加/退出の検知
            if "joined the game" in text:
                match = self.join_pattern.search(text)
                if match:
                    player_name = match.group(1)
                    self.online_players[player_name] = time.time()
            elif "left the game" in text:
                match = self.leave_pattern.search(text)
                if match:
                    player_name = match.group(1)
                    self.online_players.pop(player_name, None)
            
            await self.log_queue.put((self.server_id, text))
    
    def _get_process(self) -> psutil.Process | None:
        """現在実行中のサーバープロセスを取得する"""
        # 1. 自身が起動したプロセスをチェック
        if self.process is not None and self.process.returncode is None:
            try:
                return psutil.Process(self.process.pid)
            except psutil.NoSuchProcess:
                pass
        
        # 2. プロセスリストから検索
        for proc in psutil.process_iter(['pid', 'name', 'cmdline', 'create_time', 'cwd']):
            try:
                cmdline = proc.info['cmdline']
                if not cmdline or 'java' not in proc.info['name']:
                    continue
                
                # cwdで識別（最も確実）
                try:
                    proc_cwd = proc.cwd()
                    if proc_cwd == self.cwd:
                        return proc
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass
                
                # jarファイル名で識別（Paperなど）
                if self.jar and self.jar in cmdline:
                    return proc
                
                # Forgeの場合: コマンドラインにcwdパスが含まれているか確認
                cmdline_str = ' '.join(cmdline)
                if self.cwd in cmdline_str:
                    return proc
                    
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return None
    
    def is_running(self) -> bool:
        """サーバーが起動中かどうかを返す"""
        return self._get_process() is not None
    
    def get_stats(self) -> dict | None:
        """サーバーのステータス(CPU, Memory, Uptime)を取得"""
        proc = self._get_process()
        if not proc:
            return None
        
        try:
            cpu_percent = proc.cpu_percent(interval=None)
            mem_info = proc.memory_info()
            mem_mb = mem_info.rss / (1024 * 1024)
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
        """サーバーの終了を待機する"""
        if self.process:
            await self.process.wait()
        
        while self.is_running():
            await asyncio.sleep(1)


class MultiServerManager:
    """複数のMinecraftサーバーを管理するクラス"""
    
    def __init__(self, servers_config: dict):
        self.servers: dict[str, ServerInstance] = {}
        for server_id, config in servers_config.items():
            self.servers[server_id] = ServerInstance(server_id, config)
        logging.info(f"MultiServerManager initialized with servers: {list(self.servers.keys())}")
    
    def get_server(self, server_id: str) -> ServerInstance | None:
        """指定されたサーバーインスタンスを取得する"""
        return self.servers.get(server_id)
    
    def get_server_ids(self) -> list[str]:
        """登録されている全サーバーIDのリストを返す"""
        return list(self.servers.keys())
    
    def get_server_choices(self) -> list[tuple[str, str]]:
        """Discord用の選択肢リストを返す [(name, value), ...]"""
        return [(server.name, server_id) for server_id, server in self.servers.items()]
    
    async def start_server(self, server_id: str) -> bool:
        """指定されたサーバーを起動する"""
        server = self.get_server(server_id)
        if not server:
            logging.error(f"Server '{server_id}' not found")
            return False
        return await server.start()
    
    async def stop_server(self, server_id: str) -> bool:
        """指定されたサーバーを停止する"""
        server = self.get_server(server_id)
        if not server:
            logging.error(f"Server '{server_id}' not found")
            return False
        return await server.stop()
    
    async def write_stdin(self, server_id: str, command_str: str) -> bool:
        """指定されたサーバーにコマンドを送信する"""
        server = self.get_server(server_id)
        if not server:
            return False
        return await server.write_stdin(command_str)
    
    def is_running(self, server_id: str) -> bool:
        """指定されたサーバーが起動中かどうかを返す"""
        server = self.get_server(server_id)
        if not server:
            return False
        return server.is_running()
    
    def get_server_stats(self, server_id: str) -> dict | None:
        """指定されたサーバーのステータスを取得する"""
        server = self.get_server(server_id)
        if not server:
            return None
        return server.get_stats()
    
    def get_all_running(self) -> list[str]:
        """起動中の全サーバーIDのリストを返す"""
        return [server_id for server_id, server in self.servers.items() if server.is_running()]
    
    def get_all_log_queues(self) -> dict[str, asyncio.Queue]:
        """全サーバーのログキューを返す"""
        return {server_id: server.log_queue for server_id, server in self.servers.items()}
    
    async def wait_for_exit(self, server_id: str):
        """指定されたサーバーの終了を待機する"""
        server = self.get_server(server_id)
        if server:
            await server.wait_for_exit()


# 後方互換性のためのエイリアス
ServerManager = MultiServerManager
