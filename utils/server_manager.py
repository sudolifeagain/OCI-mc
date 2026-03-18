import asyncio
import subprocess
import logging
import time
import os
import psutil
import re


MEMORY_THRESHOLD = 0.8  # 空きメモリが割り当ての80%未満なら起動拒否


class ServerInstance:
    """個別のMinecraftサーバーインスタンスを管理するクラス"""

    def __init__(self, server_id: str, config: dict):
        self.server_id = server_id
        self.name = config.get("name", server_id)
        self.jar = config.get("jar")
        self.use_script = config.get("use_script")
        self.cwd = config["cwd"]
        self.memory = config.get("memory", "4G")
        self.port = config.get("port", 25565)
        self.log_forwarding = config.get("log_forwarding", True)
        self.process = None
        self.log_queue = asyncio.Queue()
        self.online_players = {}  # {name: join_timestamp}
        self._stopping = False  # ロックフラグ
        # Regex patterns for join/leave
        self.join_pattern = re.compile(r': (.+) joined the game')
        self.leave_pattern = re.compile(r': (.+) left the game')

    @staticmethod
    def _parse_memory_value(value: str) -> int | None:
        """メモリ指定文字列をMB単位の整数に変換する (例: "4G"→4096, "2500M"→2500)"""
        match = re.match(r'^(\d+)\s*([gGmM])$', value.strip())
        if not match:
            return None
        num = int(match.group(1))
        unit = match.group(2).upper()
        return num * 1024 if unit == 'G' else num

    def _get_allocated_memory_mb(self) -> int | None:
        """サーバーに割り当てられた最大メモリ(MB)を取得する"""
        if self.use_script:
            # スクリプト起動: user_jvm_args.txt から -Xmx を読み取る
            jvm_args_path = os.path.join(self.cwd, 'user_jvm_args.txt')
            try:
                with open(jvm_args_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('#') or not line:
                            continue
                        match = re.match(r'-Xmx(\d+[gGmM])', line)
                        if match:
                            return self._parse_memory_value(match.group(1))
            except FileNotFoundError:
                logging.warning(f"JVM args file not found: {jvm_args_path}")
            return None
        else:
            # jar直接起動: config の memory 値を使用
            return self._parse_memory_value(self.memory)

    def _check_memory_available(self) -> tuple[bool, str, dict]:
        """システム空きメモリが割り当ての80%以上あるか確認する

        Returns:
            (ok, message, details) - details は呼び出し元で詳細メッセージ構築に使用
        """
        allocated_mb = self._get_allocated_memory_mb()
        if allocated_mb is None:
            logging.warning(f"Server '{self.server_id}': メモリ割り当て値を取得できないため、チェックをスキップ")
            return True, "", {}

        mem = psutil.virtual_memory()
        available_mb = mem.available / (1024 * 1024)
        total_mb = mem.total / (1024 * 1024)
        required_mb = allocated_mb * MEMORY_THRESHOLD

        if available_mb < required_mb:
            msg = (
                f"メモリ不足: 空き {available_mb:.0f}MB < "
                f"必要 {required_mb:.0f}MB "
                f"(割り当て {allocated_mb}MB の {int(MEMORY_THRESHOLD * 100)}%)"
            )
            details = {
                "available_mb": available_mb,
                "total_mb": total_mb,
                "required_mb": required_mb,
                "allocated_mb": allocated_mb,
            }
            logging.warning(f"Server '{self.server_id}': {msg}")
            return False, msg, details

        return True, "", {}

    def _get_pid_file_path(self) -> str:
        """PIDファイルのパスを返す"""
        return os.path.join(self.cwd, f'.{self.server_id}.pid')

    def _read_pid_file(self) -> int | None:
        """PIDファイルからPIDを読み取る"""
        pid_file = self._get_pid_file_path()
        try:
            with open(pid_file, 'r') as f:
                return int(f.read().strip())
        except (FileNotFoundError, ValueError, PermissionError):
            return None

    def _write_pid_file(self, pid: int) -> bool:
        """PIDファイルにPIDを書き込む"""
        pid_file = self._get_pid_file_path()
        try:
            with open(pid_file, 'w') as f:
                f.write(str(pid))
            return True
        except (PermissionError, OSError) as e:
            logging.warning(f"Failed to write PID file for '{self.server_id}': {e}")
            return False

    def _cleanup_pid_file(self) -> None:
        """PIDファイルを削除"""
        pid_file = self._get_pid_file_path()
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except (PermissionError, OSError) as e:
            logging.warning(f"Failed to remove PID file for '{self.server_id}': {e}")

    def _cleanup_server_state(self) -> None:
        """サーバー終了時のクリーンアップ"""
        self.online_players.clear()
        self._cleanup_pid_file()
        self.process = None
        logging.info(f"Server '{self.server_id}' state cleaned up")

    def _find_java_child(self, proc: psutil.Process) -> psutil.Process | None:
        """プロセスの子プロセスからjavaを探す"""
        try:
            children = proc.children(recursive=True)
            for child in children:
                try:
                    if 'java' in child.name().lower():
                        return child
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return None

    async def start(self) -> bool:
        """サーバーを起動する"""
        if self._stopping:
            logging.warning(f"Server '{self.server_id}' is currently stopping")
            return False

        if self.is_running():
            return False

        # 古いPIDファイルをクリーンアップ
        self._cleanup_pid_file()

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

        # PIDファイルに保存
        self._write_pid_file(self.process.pid)

        asyncio.create_task(self._read_stdout())
        self.online_players.clear()
        logging.info(f"Server '{self.server_id}' started with PID {self.process.pid}")
        return True

    async def stop(self) -> bool:
        """サーバーを停止する"""
        if self._stopping:
            logging.warning(f"Server '{self.server_id}' is already stopping")
            return False

        self._stopping = True
        stopped_something = False

        try:
            # プロセスを検索
            proc = self._get_process()
            if not proc:
                self._cleanup_server_state()
                return False

            # 子プロセス（java）を探す
            java_proc = self._find_java_child(proc)
            target_proc = java_proc if java_proc else proc

            # 自分が起動したプロセスにstopコマンドを送信（可能な場合）
            if self.process and self.process.returncode is None and self.process.stdin:
                try:
                    self.process.stdin.write(b"stop\n")
                    await self.process.stdin.drain()
                    logging.info(f"Sent 'stop' command to server '{self.server_id}'")
                except Exception as e:
                    logging.warning(f"Failed to send stop command: {e}")

            # 終了を待機（最大60秒）
            try:
                await asyncio.to_thread(target_proc.wait, timeout=60)
                stopped_something = True
                logging.info(f"Server '{self.server_id}' stopped gracefully")
            except psutil.TimeoutExpired:
                # タイムアウト: SIGTERM送信
                logging.warning(f"Server '{self.server_id}' did not stop gracefully, sending SIGTERM")
                try:
                    target_proc.terminate()
                    await asyncio.to_thread(target_proc.wait, timeout=10)
                    stopped_something = True
                except psutil.TimeoutExpired:
                    # さらにタイムアウト: SIGKILL送信
                    logging.warning(f"Server '{self.server_id}' did not respond to SIGTERM, sending SIGKILL")
                    try:
                        target_proc.kill()
                        stopped_something = True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    stopped_something = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                stopped_something = True

            # 親プロセス（シェル）も終了
            if java_proc and proc.is_running():
                try:
                    proc.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        finally:
            self._cleanup_server_state()
            self._stopping = False

        return stopped_something

    async def write_stdin(self, command_str: str) -> bool:
        """サーバーコンソールにコマンドを送信する"""
        if self.process and self.process.returncode is None:
            if self.process.stdin:
                try:
                    self.process.stdin.write(f"{command_str}\n".encode())
                    await self.process.stdin.drain()
                    return True
                except Exception as e:
                    logging.warning(f"Failed to write to stdin: {e}")
        return False

    async def _read_stdout(self):
        """標準出力を非同期で読み取り、Queueに入れる"""
        if not self.process or not self.process.stdout:
            return

        try:
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
        finally:
            # プロセス終了時のクリーンアップ
            if not self._stopping:
                logging.info(f"Server '{self.server_id}' process ended unexpectedly")
                self._cleanup_server_state()

    def _get_process(self) -> psutil.Process | None:
        """現在実行中のサーバープロセスを取得する"""
        # 1. PIDファイルからチェック
        pid = self._read_pid_file()
        if pid:
            try:
                proc = psutil.Process(pid)
                if proc.is_running():
                    # 子プロセス（java）があればそれを返す
                    java_child = self._find_java_child(proc)
                    if java_child:
                        return java_child
                    return proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # PIDファイルが古い
                self._cleanup_pid_file()

        # 2. 自身が起動したプロセスをチェック
        if self.process is not None and self.process.returncode is None:
            try:
                proc = psutil.Process(self.process.pid)
                java_child = self._find_java_child(proc)
                if java_child:
                    return java_child
                return proc
            except psutil.NoSuchProcess:
                pass

        # 3. プロセスリストから検索（フォールバック）
        cwd_normalized = os.path.normpath(self.cwd)
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = proc.info['name'] or ''
                if 'java' not in name.lower():
                    continue

                cmdline = proc.info['cmdline']
                if not cmdline:
                    continue

                # cwdで識別
                try:
                    proc_cwd = os.path.normpath(proc.cwd())
                    if proc_cwd == cwd_normalized:
                        return proc
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass

                # コマンドラインにcwdパスが含まれているか確認
                cmdline_str = ' '.join(cmdline)
                if self.cwd in cmdline_str:
                    return proc

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        return None

    def is_running(self) -> bool:
        """サーバーが起動中かどうかを返す"""
        return self._get_process() is not None

    async def get_tick_stats(self, rcon_client) -> dict | None:
        """RCON経由でTPS/MSPTを取得する"""
        if not rcon_client:
            return None
        try:
            success, response = await rcon_client.execute("forge tps")
            if not success:
                return None
            tps_match = re.search(r'Overall.*Mean TPS:\s*([\d.]+)', response)
            mspt_match = re.search(r'Overall.*Mean tick time:\s*([\d.]+)', response)
            if tps_match and mspt_match:
                return {
                    "tps": float(tps_match.group(1)),
                    "mspt": float(mspt_match.group(1)),
                }
        except Exception as e:
            logging.debug(f"Failed to get tick stats for '{self.server_id}': {e}")
        return None

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

    def check_memory_for_start(self, server_id: str) -> tuple[bool, str]:
        """サーバー起動前のメモリチェック。不足時は稼働中サーバーの使用量を含む詳細メッセージを返す"""
        server = self.get_server(server_id)
        if not server:
            return False, f"サーバー '{server_id}' が見つからない"

        mem_ok, mem_msg, details = server._check_memory_available()
        if mem_ok:
            return True, ""

        # 稼働中サーバーのメモリ使用量を収集
        running_info = []
        for sid, srv in self.servers.items():
            stats = srv.get_stats()
            if stats:
                running_info.append(f"  - {srv.name}: {stats['memory_mb'] / 1024:.1f}GB")

        lines = [mem_msg]
        if running_info:
            lines.append("稼働中サーバー:")
            lines.extend(running_info)
        lines.append(
            f"マシン: 合計 {details['total_mb'] / 1024:.1f}GB / "
            f"空き {details['available_mb'] / 1024:.1f}GB"
        )

        return False, "\n".join(lines)

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
