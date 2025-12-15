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

# 環境変数
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
NOTION_TOKEN = os.getenv('NOTION_TOKEN')
NOTION_DB_ID = os.getenv('NOTION_DB_ID')

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

# Support multiple User IDs via DISCORD_USER_IDS (commas sep) or legacy DISCORD_USER_ID
user_ids_env = os.getenv('DISCORD_USER_IDS')
if user_ids_env:
    DISCORD_USER_IDS = parse_ids(user_ids_env)
else:
    # Fallback to single ID
    DISCORD_USER_IDS = int(os.getenv('DISCORD_USER_ID', 0))

# config.json のロールIDを上書き
CONFIG['roles']['admin'] = DISCORD_ADMIN_ID
CONFIG['roles']['mod'] = DISCORD_MOD_ID
CONFIG['roles']['owner'] = DISCORD_OWNER_ID
CONFIG['roles']['user'] = DISCORD_USER_IDS # Can be int or list[int]

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
