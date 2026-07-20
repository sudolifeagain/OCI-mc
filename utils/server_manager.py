import asyncio
import subprocess
import logging
import time
import os
import json
from collections.abc import Awaitable, Callable
import psutil
import re

from utils.rcon import RconClient, get_rcon_client


MEMORY_THRESHOLD = 0.8  # 空きメモリが割り当ての80%未満なら起動拒否


class ServerInstance:
    """個別のMinecraftサーバーインスタンスを管理するクラス"""

    def __init__(self, server_id: str, config: dict, runtime_dir: str | None = None):
        self.server_id = server_id
        self.config = config
        self.name = config.get("name", server_id)
        self.jar = config.get("jar")
        self.use_script = config.get("use_script")
        self.java_command = config.get("java_command", "java")
        self.cwd = config["cwd"]
        self.memory = config.get("memory", "4G")
        self.port = config.get("port", 25565)
        self.run_as_user = config.get("run_as_user")
        self.startup_timeout = int(config.get("startup_timeout", 300))
        self.log_forwarding = config.get("log_forwarding", True)
        self.runtime_dir = runtime_dir or os.path.join(self.cwd, ".bot-runtime")
        self.process = None
        self.log_queue = asyncio.Queue(maxsize=int(config.get("log_queue_size", 1000)))
        self.dropped_log_lines = 0
        self.online_players = {}  # {name: join_timestamp}
        self._stopping = False  # ロックフラグ
        self._graceful_stopping = False  # graceful stop 中フラグ
        self._maintenance = False
        self._generation = 0
        self._stdout_task: asyncio.Task | None = None
        self.last_error = ""
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
        return os.path.join(self.runtime_dir, "pids", f'{self.server_id}.json')

    def _read_pid_file(self) -> dict | None:
        """PIDファイルからプロセス識別情報を読み取る。"""
        pid_file = self._get_pid_file_path()
        try:
            with open(pid_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("pid"), int):
                return None
            return data
        except (FileNotFoundError, ValueError, PermissionError, json.JSONDecodeError):
            return None

    def _write_pid_file(self, pid: int) -> bool:
        """PIDと起動時刻をBot専用領域へ原子的に保存する。"""
        pid_file = self._get_pid_file_path()
        temp_file = f"{pid_file}.tmp"
        try:
            os.makedirs(os.path.dirname(pid_file), mode=0o700, exist_ok=True)
            proc = psutil.Process(pid)
            payload = {
                "pid": pid,
                "create_time": proc.create_time(),
                "cwd": os.path.normpath(self.cwd),
            }
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temp_file, 0o600)
            os.replace(temp_file, pid_file)
            return True
        except (psutil.NoSuchProcess, PermissionError, OSError) as e:
            logging.warning(f"Failed to write PID file for '{self.server_id}': {e}")
            try:
                os.remove(temp_file)
            except OSError:
                pass
            return False

    def _cleanup_pid_file(self) -> None:
        """PIDファイルを削除"""
        pid_file = self._get_pid_file_path()
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except (PermissionError, OSError) as e:
            logging.warning(f"Failed to remove PID file for '{self.server_id}': {e}")

    def _cleanup_server_state(self, expected_process=None) -> None:
        """サーバー終了時のクリーンアップ"""
        if expected_process is not None and self.process is not expected_process:
            return
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

    def _build_start_command(self) -> list[str]:
        """サーバー起動コマンドを構築する"""
        if self.use_script:
            server_command = [self.use_script, 'nogui']
        else:
            server_command = [
            self.java_command,
            f'-Xmx{self.memory}',
            f'-Xms{self.memory}',
            '-jar',
            self.jar,
            'nogui',
            ]
        if self.run_as_user and os.name == "posix":
            return ["/usr/bin/sudo", "-n", "-H", "-u", self.run_as_user, "--", *server_command]
        return server_command

    @staticmethod
    def _build_child_environment() -> dict[str, str]:
        """Botの秘密情報を除外した最小限の子プロセス環境を構築する。"""
        allowed_keys = {
            "HOME",
            "LANG",
            "LANGUAGE",
            "LC_ALL",
            "PATH",
            "TERM",
            "TZ",
        }
        child_env = {key: value for key, value in os.environ.items() if key in allowed_keys}
        child_env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        return child_env

    async def _wait_until_ready(self, process, generation: int) -> bool:
        """RCONまたはゲームポートが利用可能になるまで待機する。"""
        deadline = time.monotonic() + self.startup_timeout
        rcon_client = get_rcon_client(self.config)
        last_message = ""

        while time.monotonic() < deadline:
            if self.process is not process or self._generation != generation:
                self.last_error = "起動状態が別の操作で変更された"
                return False
            if process.returncode is not None:
                self.last_error = f"プロセスが起動中に終了した (code={process.returncode})"
                return False

            if rcon_client:
                success, response = await rcon_client.execute("list")
                if success:
                    return True
                last_message = response
            else:
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", self.port),
                        timeout=2,
                    )
                    writer.close()
                    await writer.wait_closed()
                    return True
                except (OSError, asyncio.TimeoutError):
                    pass
            await asyncio.sleep(2)

        self.last_error = f"起動確認が{self.startup_timeout}秒でタイムアウトした"
        if last_message:
            logging.warning(
                "Server '%s' readiness timeout: %s",
                self.server_id,
                last_message,
            )
        return False

    async def start(self) -> bool:
        """サーバーを起動する"""
        self.last_error = ""
        if self._stopping:
            logging.warning(f"Server '{self.server_id}' is currently stopping")
            self.last_error = "停止処理中である"
            return False

        if self.is_running():
            self.last_error = "既に起動している"
            return False

        # 古いPIDファイルをクリーンアップ
        self._cleanup_pid_file()

        cmd = self._build_start_command()

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=self._build_child_environment(),
            )
        except (OSError, ValueError) as e:
            self.last_error = f"プロセスを生成できない: {e}"
            logging.exception("Failed to start server '%s'", self.server_id)
            return False

        self.process = process
        self._generation += 1
        generation = self._generation

        # PIDファイルに保存
        self._write_pid_file(process.pid)

        self._stdout_task = asyncio.create_task(self._read_stdout(process, generation))
        self.online_players.clear()
        logging.info(f"Server '{self.server_id}' started with PID {process.pid}")

        if await self._wait_until_ready(process, generation):
            logging.info(f"Server '{self.server_id}' is ready")
            return True

        logging.error("Server '%s' failed readiness check: %s", self.server_id, self.last_error)
        if process.returncode is None:
            await self._stop_process(process)
        return False

    async def _stop_process(self, expected_process=None) -> bool:
        """stdinまたはRCONで停止し、応答しない場合だけシグナルへ移行する。"""
        proc = self._get_process()
        if not proc:
            self._cleanup_server_state(expected_process)
            return False

        java_proc = self._find_java_child(proc)
        target_proc = java_proc if java_proc else proc
        stop_requested = False

        if self.process and self.process.returncode is None and self.process.stdin:
            try:
                self.process.stdin.write(b"stop\n")
                await self.process.stdin.drain()
                stop_requested = True
                logging.info(f"Sent 'stop' command to server '{self.server_id}'")
            except (BrokenPipeError, ConnectionError, OSError) as e:
                logging.warning(f"Failed to send stop command: {e}")

        if not stop_requested:
            rcon_client = get_rcon_client(self.config)
            if rcon_client:
                try:
                    # stopでは接続切断が成功応答より先に発生する場合がある。
                    await rcon_client.execute("stop")
                    stop_requested = True
                    logging.info(f"Sent RCON stop to server '{self.server_id}'")
                except Exception as e:
                    logging.warning(f"Failed to send RCON stop: {e}")

        try:
            if stop_requested:
                await asyncio.to_thread(target_proc.wait, timeout=60)
                logging.info(f"Server '{self.server_id}' stopped gracefully")
                return True

            logging.warning(
                "Server '%s' has no usable console channel; sending SIGTERM",
                self.server_id,
            )
            target_proc.terminate()
            await asyncio.to_thread(target_proc.wait, timeout=10)
            return True
        except psutil.TimeoutExpired:
            logging.warning(f"Server '{self.server_id}' did not stop gracefully, sending SIGTERM")
            try:
                target_proc.terminate()
                await asyncio.to_thread(target_proc.wait, timeout=10)
                return True
            except psutil.TimeoutExpired:
                logging.warning(f"Server '{self.server_id}' did not respond to SIGTERM, sending SIGKILL")
                try:
                    target_proc.kill()
                    await asyncio.to_thread(target_proc.wait, timeout=5)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
                    pass
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return True
        finally:
            if java_proc:
                try:
                    if proc.is_running():
                        proc.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            self._cleanup_server_state(expected_process)

    async def stop(self) -> bool:
        """サーバーを停止する。"""
        if self._stopping:
            logging.warning(f"Server '{self.server_id}' is already stopping")
            self.last_error = "既に停止処理中である"
            return False

        self._stopping = True
        expected_process = self.process
        try:
            return await self._stop_process(expected_process)
        finally:
            self._stopping = False

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

    def _enqueue_log(self, text: str) -> None:
        """stdoutを有限キューへ追加し、満杯時は最古の行を破棄する。"""
        item = (self.server_id, text[:8192])
        if self.log_queue.full():
            try:
                self.log_queue.get_nowait()
                self.dropped_log_lines += 1
            except asyncio.QueueEmpty:
                pass
        try:
            self.log_queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped_log_lines += 1

    async def _read_stdout(self, process, generation: int):
        """標準出力を非同期で読み取り、Queueに入れる"""
        if not process.stdout:
            return

        try:
            while True:
                line = await process.stdout.readline()
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

                self._enqueue_log(text)
        finally:
            # 古いstdoutタスクが新しいプロセス状態を消さないよう世代を照合する。
            if (
                not self._stopping
                and self._generation == generation
                and self.process is process
            ):
                logging.info(f"Server '{self.server_id}' process ended unexpectedly")
                self._cleanup_server_state(process)

    def _get_process(self) -> psutil.Process | None:
        """現在実行中のサーバープロセスを取得する"""
        # 1. 現在のBotが保持するプロセスハンドルを優先する。
        if self.process is not None and self.process.returncode is None:
            try:
                proc = psutil.Process(self.process.pid)
                java_child = self._find_java_child(proc)
                return java_child or proc
            except psutil.NoSuchProcess:
                pass

        # 2. Bot専用PIDファイルを起動時刻・cwdまで検証する。
        metadata = self._read_pid_file()
        if metadata:
            try:
                proc = psutil.Process(metadata["pid"])
                create_time = float(metadata.get("create_time", -1))
                if abs(proc.create_time() - create_time) > 0.01:
                    raise psutil.NoSuchProcess(metadata["pid"])
                recorded_cwd = os.path.normpath(str(metadata.get("cwd", "")))
                if recorded_cwd != os.path.normpath(self.cwd):
                    raise psutil.NoSuchProcess(metadata["pid"])
                java_child = self._find_java_child(proc)
                return java_child or proc
            except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, TypeError):
                self._cleanup_pid_file()

        # 3. cwdの完全一致だけを用いる限定的なフォールバック。
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

    async def wait_for_exit(self, timeout: float = 120):
        """サーバーの終了を上限時間付きで待機する。"""
        if self.process:
            try:
                await asyncio.wait_for(self.process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return False

        deadline = time.monotonic() + timeout
        while self.is_running() and time.monotonic() < deadline:
            await asyncio.sleep(1)
        return not self.is_running()


class MultiServerManager:
    """複数のMinecraftサーバーを管理するクラス"""

    def __init__(self, servers_config: dict, runtime_dir: str = ".runtime"):
        self._servers_config = servers_config
        self.runtime_dir = runtime_dir
        self.servers: dict[str, ServerInstance] = {}
        self._operation_locks: dict[str, asyncio.Lock] = {}
        self._start_admission_lock = asyncio.Lock()
        for server_id, config in servers_config.items():
            self.servers[server_id] = ServerInstance(server_id, config, runtime_dir)
            self._operation_locks[server_id] = asyncio.Lock()
        logging.info(f"MultiServerManager initialized with servers: {list(self.servers.keys())}")

    def _desired_state_path(self) -> str:
        return os.path.join(self.runtime_dir, "desired_servers.json")

    def _load_desired_servers(self) -> set[str]:
        try:
            with open(self._desired_state_path(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            servers = data.get("servers", []) if isinstance(data, dict) else []
            return {server_id for server_id in servers if server_id in self.servers}
        except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
            return set()

    def _save_desired_servers(self, server_ids: set[str]) -> None:
        os.makedirs(self.runtime_dir, mode=0o700, exist_ok=True)
        path = self._desired_state_path()
        temp_path = f"{path}.tmp"
        payload = {"servers": sorted(server_ids), "updated_at": int(time.time())}
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)

    def _set_desired(self, server_id: str, desired: bool) -> None:
        desired_servers = self._load_desired_servers()
        if desired:
            desired_servers.add(server_id)
        else:
            desired_servers.discard(server_id)
        self._save_desired_servers(desired_servers)

    def get_desired_servers(self) -> list[str]:
        """Bot再起動後に復元すべきサーバーIDを返す。"""
        configured = {
            server_id
            for server_id, config in self._servers_config.items()
            if config.get("auto_start", False)
        }
        return sorted(configured | self._load_desired_servers())

    def get_server(self, server_id: str) -> ServerInstance | None:
        """指定されたサーバーインスタンスを取得する"""
        return self.servers.get(server_id)

    def get_server_ids(self) -> list[str]:
        """登録されている全サーバーIDのリストを返す"""
        return list(self.servers.keys())

    def get_server_choices(self) -> list[tuple[str, str]]:
        """Discord用の選択肢リストを返す [(name, value), ...]"""
        return [(server.name, server_id) for server_id, server in self.servers.items()]

    async def start_server(
        self,
        server_id: str,
        *,
        persist_desired: bool = True,
        maintenance: bool = False,
    ) -> bool:
        """指定されたサーバーを起動する"""
        server = self.get_server(server_id)
        if not server:
            logging.error(f"Server '{server_id}' not found")
            return False
        if server._maintenance and not maintenance:
            server.last_error = "メンテナンス処理中である"
            return False

        async with self._operation_locks[server_id]:
            if server.is_running():
                server.last_error = "既に起動している"
                return False
            # 異なるサーバーの同時起動でも空きメモリ判定と予約を直列化する。
            async with self._start_admission_lock:
                mem_ok, mem_msg = self.check_memory_for_start(server_id)
                if not mem_ok:
                    server.last_error = mem_msg
                    return False
                success = await server.start()
            if success and persist_desired:
                self._set_desired(server_id, True)
            return success

    def _get_rcon_client(self, server_id: str) -> RconClient | None:
        """サーバーIDからRCONクライアントを取得"""
        config = self._servers_config.get(server_id, {})
        return get_rcon_client(config)

    @staticmethod
    def _parse_player_count(list_response: str) -> int:
        """RCON list レスポンスからプレイヤー数を抽出"""
        match = re.search(r'There are (\d+)', list_response)
        return int(match.group(1)) if match else 0

    async def _prepare_graceful_stop(
        self,
        server_id: str,
        *,
        progress_callback: Callable[[str], Awaitable[None]] | None = None,
        force: bool = False,
    ) -> bool:
        """接続者へ停止予告し、安全確認ができない場合は通常停止を拒否する。"""
        server = self.servers[server_id]
        rcon_client = self._get_rcon_client(server_id)
        has_rcon_config = bool(
            self._servers_config.get(server_id, {}).get("rcon_port")
        )
        if not rcon_client:
            if has_rcon_config and not force:
                server.last_error = "RCONを利用できないため停止を中止した。force=trueで強制停止できる"
                return False
            return True

        try:
            success, response = await asyncio.wait_for(
                rcon_client.execute("list"), timeout=5.0
            )
        except Exception as e:
            logging.warning(f"Server '{server_id}': RCON check failed: {e}")
            if not force:
                server.last_error = "接続者を確認できないため停止を中止した。force=trueで強制停止できる"
                return False
            return True

        if not success:
            logging.warning(f"Server '{server_id}': RCON list command failed: {response}")
            if not force:
                server.last_error = "接続者を確認できないため停止を中止した。force=trueで強制停止できる"
                return False
            return True

        player_count = self._parse_player_count(response)
        if player_count <= 0:
            logging.info(f"Server '{server_id}': no players online, stopping immediately")
            return True

        logging.info(
            f"Server '{server_id}': {player_count} players online, announcing shutdown"
        )
        await rcon_client.execute("say サーバーが60秒後にシャットダウンします")
        if progress_callback:
            try:
                await progress_callback(
                    f"{server.name}: {player_count}人のプレイヤーが接続中 — "
                    "60秒後にシャットダウンします"
                )
            except Exception as e:
                logging.warning(f"Failed to report shutdown progress: {e}")
        await asyncio.sleep(50)
        await rcon_client.execute("say サーバーが10秒後にシャットダウンします")
        await asyncio.sleep(10)
        return True

    async def stop_server(
        self,
        server_id: str,
        *,
        progress_callback: Callable[[str], Awaitable[None]] | None = None,
        force: bool = False,
        preserve_desired: bool = False,
        maintenance: bool = False,
    ) -> bool:
        """指定されたサーバーを停止する (プレイヤーがいる場合はアナウンス後に待機)"""
        server = self.get_server(server_id)
        if not server:
            logging.error(f"Server '{server_id}' not found")
            return False
        if server._maintenance and not maintenance:
            server.last_error = "メンテナンス処理中である"
            return False

        async with self._operation_locks[server_id]:
            if server._graceful_stopping or server._stopping:
                logging.warning(f"Server '{server_id}' is already stopping")
                server.last_error = "既に停止処理中である"
                return False

            server._graceful_stopping = True
            try:
                if not await self._prepare_graceful_stop(
                    server_id,
                    progress_callback=progress_callback,
                    force=force,
                ):
                    return False
                result = await server.stop()
                if result and not preserve_desired:
                    self._set_desired(server_id, False)
                return result
            finally:
                server._graceful_stopping = False

    async def restart_server(
        self,
        server_id: str,
        *,
        progress_callback: Callable[[str], Awaitable[None]] | None = None,
        force: bool = False,
    ) -> bool:
        """同一ロック内で安全に停止・起動する。"""
        server = self.get_server(server_id)
        if not server:
            return False
        if server._maintenance:
            server.last_error = "メンテナンス処理中である"
            return False

        async with self._operation_locks[server_id]:
            if server.is_running():
                if not await self._prepare_graceful_stop(
                    server_id,
                    progress_callback=progress_callback,
                    force=force,
                ):
                    return False
                if not await server.stop():
                    return False

            async with self._start_admission_lock:
                mem_ok, mem_msg = self.check_memory_for_start(server_id)
                if not mem_ok:
                    server.last_error = mem_msg
                    return False
                success = await server.start()
            if success:
                self._set_desired(server_id, True)
            return success

    def begin_maintenance(self, server_id: str) -> bool:
        """外部更新処理中のライフサイクル操作を拒否する。"""
        server = self.get_server(server_id)
        if not server or server._maintenance:
            return False
        server._maintenance = True
        return True

    def end_maintenance(self, server_id: str) -> None:
        server = self.get_server(server_id)
        if server:
            server._maintenance = False

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

    async def wait_for_exit(self, server_id: str, timeout: float = 120) -> bool:
        """指定されたサーバーの終了を待機する"""
        server = self.get_server(server_id)
        if server:
            return await server.wait_for_exit(timeout)
        return True


# 後方互換性のためのエイリアス
ServerManager = MultiServerManager
