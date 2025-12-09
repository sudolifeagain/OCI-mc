import os
import sys
import asyncio
import json
import tarfile
import logging
import subprocess
from datetime import datetime
import requests
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

# --- 初期設定 ---
load_dotenv()
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
NOTION_TOKEN = os.getenv('NOTION_TOKEN')
NOTION_DB_ID = os.getenv('NOTION_DB_ID')
# 環境変数が読み込めない場合の対策としてデフォルト値0を設定
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID', 0))
DISCORD_ADMIN_ID = int(os.getenv('DISCORD_ADMIN_ID', 0))
DISCORD_MOD_ID = int(os.getenv('DISCORD_MOD_ID', 0))

# 設定読み込み
with open('config.json', 'r') as f:
    CONFIG = json.load(f)

# config.json 読み込み後に上書き
CONFIG['roles']['admin'] = DISCORD_ADMIN_ID
CONFIG['roles']['mod'] = DISCORD_MOD_ID

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Bot設定
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# グローバル変数
server_process = None
log_queue = asyncio.Queue()

# --- Notion API Upload Logic ---
def upload_to_notion(file_path):
    """
    Notion APIを使用してファイルをアップロードし、File IDを返します。
    20MBを超えるファイルは自動的にマルチパートアップロードとして処理します。
    """
    file_size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

    # 5GB未満はsingle_part, 5GB以上はmulti_part
    FIVE_GB = 5 * 1024 * 1024 * 1024
    is_multi_part = file_size > FIVE_GB
    mode = "multi_part" if is_multi_part else "single_part"

    print(f"Uploading {filename} ({file_size / 1024 / 1024:.2f} MB) as {mode}...")
    init_payload = {
        "filename": filename,
        "content_type": "application/gzip",
        "mode": mode
    }

    if is_multi_part:
        # 100MB単位で分割
        chunk_size = 100 * 1024 * 1024
        init_payload["number_of_parts"] = (file_size // chunk_size) + (1 if file_size % chunk_size else 0)

    res = requests.post("https://api.notion.com/v1/file_uploads", headers=headers, json=init_payload)
    if res.status_code != 200:
        raise Exception(f"Failed to initiate upload: {res.text}")

    upload_data = res.json()
    file_upload_id = upload_data['id']

    # 2. ファイルデータの送信
    with open(file_path, 'rb') as f:
        if not is_multi_part:
            # single_part: upload_urlを使う
            upload_url = upload_data.get('upload_url') or f"https://api.notion.com/v1/file_uploads/{file_upload_id}/send"
            resp = requests.post(
                upload_url,
                headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28"},
                files={'file': (filename, f)}
            )
            if resp.status_code not in (200, 201):
                raise Exception(f"Failed to upload file: {resp.text}")
        else:
            # multi_part: chunkごとに送信
            upload_url = upload_data.get('upload_url') or f"https://api.notion.com/v1/file_uploads/{file_upload_id}/send"
            part_num = 1
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                print(f"Uploading part {part_num}...")
                resp = requests.post(
                    upload_url,
                    headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28"},
                    files={'file': (filename, chunk)},
                    data={'part_number': str(part_num)}
                )
                if resp.status_code not in (200, 201):
                    raise Exception(f"Failed to upload part {part_num}: {resp.text}")
                part_num += 1

    # 3. アップロード完了通知 (multi_partのみ必要)
    if is_multi_part:
        complete_url = upload_data.get('complete_url') or f"https://api.notion.com/v1/file_uploads/{file_upload_id}/complete"
        resp = requests.post(
            complete_url,
            headers=headers,
            json={}
        )
        if resp.status_code not in (200, 201):
            raise Exception(f"Failed to complete upload: {resp.text}")

    return file_upload_id

def register_to_database(file_upload_id, filename, size_str):
    """アップロードしたファイルをDatabaseに登録"""
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Backup Name": {"title": [{"text": {"content": filename}}]},
            "Date": {"date": {"start": datetime.now().isoformat()}},
            "Size": {"rich_text": [{"text": {"content": size_str}}]},
            "File": {
                "files": [
                    {
                        "type": "file_upload",
                        "file_upload": {"id": file_upload_id},
                        "name": filename
                    }
                ]
            }
        }
    }
    res = requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
    return res.status_code == 200

# --- Minecraft Server Control ---
async def run_server():
    global server_process
    mem = CONFIG["java_memory"]
    jar = CONFIG["minecraft_server_jar"]

    # 修正: 起動コマンドリストを作成
    cmd = ['java', f'-Xmx{mem}', f'-Xms{mem}', '-jar', jar, 'nogui']

    server_process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd="/opt/minecraft",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )

    # ログ読み取りタスクを開始
    asyncio.create_task(read_stdout(server_process.stdout))

async def read_stdout(stream):
    """標準出力を非同期で読み取り、Queueに入れる"""
    while True:
        line = await stream.readline()
        if not line:
            break
        await log_queue.put(line.decode('utf-8', errors='ignore'))

@tasks.loop(seconds=2.0)
async def discord_log_sender():
    """Queueに溜まったログをまとめてDiscordに送信"""
    messages = [] # 修正: リストを初期化
    while not log_queue.empty():
        messages.append(await log_queue.get())

    if not messages:
        return

    # 1900文字ごとに分割して送信
    full_text = "".join(messages)
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        while len(full_text) > 0:
            chunk = full_text[:1900]
            full_text = full_text[1900:]
            try:
                await channel.send(f"```{chunk}```")
            except Exception as e:
                print(f"Log send error: {e}")

# --- Commands ---
def check_role(ctx, action):
    allowed_role_names = CONFIG['permissions'].get(action, []) # 修正: getの第二引数で安全に
    user_role_ids = [r.id for r in ctx.author.roles]
    for name in allowed_role_names:
        if CONFIG['roles'].get(name) in user_role_ids:
            return True
    return False

@bot.command()
async def start(ctx):
    if not check_role(ctx, 'start'): return await ctx.send("権限がありません。")
    if server_process and server_process.returncode is None:
        return await ctx.send("サーバーは既に起動しています。")
    await ctx.send("起動コマンドを送信しました。")
    await run_server()

@bot.command()
async def stop(ctx):
    if not check_role(ctx, 'stop'): return await ctx.send("権限がありません。")
    if server_process and server_process.returncode is None:
        server_process.stdin.write(b"stop\n")
        await server_process.stdin.drain()
        await ctx.send("停止コマンドを送信しました。")
    else:
        await ctx.send("サーバーは起動していません。")

@bot.command()
async def cmd(ctx, *, command_str):
    if not check_role(ctx, 'command'): return await ctx.send("権限がありません。")
    if server_process and server_process.returncode is None:
        server_process.stdin.write(f"{command_str}\n".encode())
        await server_process.stdin.drain()
        await ctx.send(f"コマンド送信: `{command_str}`")

@bot.command()
async def backup(ctx):
    if not check_role(ctx, 'backup'): return await ctx.send("権限がありません。")
    await ctx.send("バックアップ処理を開始します...")
    await perform_backup(ctx.channel)

# --- Backup Logic ---
async def perform_backup(channel):
    try:
        # 1. サーバー停止と待機
        was_running = False
        if server_process and server_process.returncode is None:
            was_running = True
            if channel: await channel.send("サーバーを停止してデータを保存します...")
            server_process.stdin.write(b"stop\n")
            await server_process.stdin.drain()
            await server_process.wait() # 終了待機

        # 2. 圧縮
        if channel: await channel.send("ワールドデータを圧縮中...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        zip_name = f"backup_{timestamp}.tar.gz"

        # 実行パスは /opt/minecraft を想定
        os.chdir("/opt/minecraft")
        with tarfile.open(zip_name, "w:gz") as tar:
            for d in CONFIG["backup"]["target_dirs"]:
                if os.path.exists(d):
                    tar.add(d)

        size_mb = os.path.getsize(zip_name) / (1024 * 1024)

        # 3. Notion Upload
        if channel: await channel.send(f"Notionへアップロード中... ({size_mb:.1f}MB)")

        # 非同期実行のためにExecutorを使用
        loop = asyncio.get_event_loop()
        file_id = await loop.run_in_executor(None, upload_to_notion, zip_name)

        # DB登録
        await loop.run_in_executor(None, register_to_database, file_id, zip_name, f"{size_mb:.1f}MB")

        if channel: await channel.send("✅ バックアップ完了！")

        # 4. 一時ファイル削除
        os.remove(zip_name)

        # 5. 再起動
        if was_running:
            if channel: await channel.send("サーバーを再起動します。")
            await run_server()

    except Exception as e:
        if channel: await channel.send(f"❌ バックアップエラー: {str(e)}")
        logging.error(e)

@tasks.loop(minutes=1)
async def scheduler():
    now = datetime.now().strftime("%H:%M")
    # 設定ファイルにバックアップスケジュールがある場合のみ実行
    if 'backup' in CONFIG and 'schedule_time' in CONFIG['backup']:
        if now == CONFIG['backup']['schedule_time']:
            channel = bot.get_channel(CHANNEL_ID)
            await perform_backup(channel)

# --- Main ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    discord_log_sender.start()
    scheduler.start()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
