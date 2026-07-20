import os
import sys
import json
import logging
from dotenv import load_dotenv

# --- 初期設定 ---
load_dotenv()

# config.json 読み込み
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        CONFIG = json.load(f)
except FileNotFoundError:
    print("config.jsonが見つかりません。")
    sys.exit(1)

# user_permissions.json 読み込み（なければ空辞書、壊れていても空辞書）
USER_PERMISSIONS_FILE = 'user_permissions.json'
try:
    with open(USER_PERMISSIONS_FILE, 'r', encoding='utf-8') as f:
        USER_PERMISSIONS = json.load(f)
except FileNotFoundError:
    USER_PERMISSIONS = {}
except json.JSONDecodeError as e:
    logging.warning(f"user_permissions.json is corrupted, using empty: {e}")
    USER_PERMISSIONS = {}

# CONFIGにマージ（既存コードとの互換性維持）
CONFIG['user_permissions'] = USER_PERMISSIONS

# 環境変数
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
NOTION_TOKEN = os.getenv('NOTION_TOKEN')
NOTION_DB_ID = os.getenv('NOTION_DB_ID')
NOTION_DS_ID = os.getenv('NOTION_DS_ID')

def parse_ids(env_val):
    if not env_val:
        return None
    try:
        # Check for comma
        if ',' in env_val:
            return [int(x.strip()) for x in env_val.split(',') if x.strip()]
        else:
            return int(env_val)
    except ValueError:
        return None

# Channel / Roles
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID', 0))
DISCORD_ADMIN_ID = int(os.getenv('DISCORD_ADMIN_ID', 0))
DISCORD_MOD_ID = int(os.getenv('DISCORD_MOD_ID', 0))
DISCORD_OWNER_ID = int(os.getenv('DISCORD_OWNER_ID', 0))

# サーバー別ログチャンネル（環境変数から読み込み、未設定の場合はデフォルトにフォールバック）
LOG_CHANNEL_IDS = {
    'paper': int(os.getenv('DISCORD_PAPER_LOG_CHANNEL_ID', 0)) or CHANNEL_ID,
    'forge': int(os.getenv('DISCORD_FORGE_LOG_CHANNEL_ID', 0)) or CHANNEL_ID,
    'forge-alt': int(
        os.getenv(
            'DISCORD_FORGE_ALT_LOG_CHANNEL_ID',
            os.getenv('DISCORD_NEOFORGE_LOG_CHANNEL_ID', '0'),
        )
    ) or CHANNEL_ID,
}

def get_log_channel_id(server_id: str) -> int:
    """サーバーごとのログチャンネルIDを取得"""
    return LOG_CHANNEL_IDS.get(server_id, CHANNEL_ID)

# リアルタイムステータス表示用チャンネル
STATUS_CHANNEL_ID = int(os.getenv('DISCORD_STATUS_CHANNEL_ID', 0))

# Support multiple User IDs via DISCORD_USER_IDS (commas sep) or legacy DISCORD_USER_ID
user_ids_env = os.getenv('DISCORD_USER_IDS')
if user_ids_env:
    DISCORD_USER_IDS = parse_ids(user_ids_env)
else:
    # Fallback to single ID
    DISCORD_USER_IDS = int(os.getenv('DISCORD_USER_ID', 0))

# ロールIDを設定（.envから読み込み）
DISCORD_CLAUDE_ROLE_ID = int(os.getenv('DISCORD_CLAUDE_ROLE_ID', 0))

CONFIG['roles'] = {
    'admin': DISCORD_ADMIN_ID,
    'mod': DISCORD_MOD_ID,
    'owner': DISCORD_OWNER_ID,
    'user': DISCORD_USER_IDS,  # Can be int or list[int]
    'claude_user': DISCORD_CLAUDE_ROLE_ID,
}

# --- サーバー設定 ---
# 新形式（servers配列）と旧形式（単一サーバー）の両方に対応
if 'servers' in CONFIG:
    SERVERS_CONFIG = CONFIG['servers']
    DEFAULT_SERVER = CONFIG.get('default_server', list(SERVERS_CONFIG.keys())[0])
else:
    # 旧形式からの変換（後方互換性）
    SERVERS_CONFIG = {
        'paper': {
            'name': 'Paper',
            'jar': CONFIG.get('minecraft_server_jar', 'paper.jar'),
            'cwd': '/opt/minecraft',
            'memory': CONFIG.get('java_memory', '4G'),
            'port': 25565
        }
    }
    DEFAULT_SERVER = 'paper'

# サーバーIDのリスト
SERVER_IDS = list(SERVERS_CONFIG.keys())

# ロギング設定
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(module)s]: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(log_dir, 'discord_audit.log'), encoding='utf-8')
    ]
)
