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

# Channel / Roles
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID', 0))
DISCORD_ADMIN_ID = int(os.getenv('DISCORD_ADMIN_ID', 0))
DISCORD_MOD_ID = int(os.getenv('DISCORD_MOD_ID', 0))
DISCORD_OWNER_ID = int(os.getenv('DISCORD_OWNER_ID', 0))
DISCORD_USER_ID = int(os.getenv('DISCORD_USER_ID', 0)) # 新規追加

# config.json のロールIDを上書き
CONFIG['roles']['admin'] = DISCORD_ADMIN_ID
CONFIG['roles']['mod'] = DISCORD_MOD_ID
CONFIG['roles']['owner'] = DISCORD_OWNER_ID
CONFIG['roles']['user'] = DISCORD_USER_ID # 新規追加

# ロギング設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

def check_role(ctx_or_interaction, action):
    """
    指定されたアクションに対して権限があるかチェックする
    Ownerは常に許可
    Context と Interaction の両方に対応
    """
    # ユーザーオブジェクトの取得
    user = getattr(ctx_or_interaction, 'author', getattr(ctx_or_interaction, 'user', None))
    
    if not user:
        return False

    if DISCORD_OWNER_ID and user.id == DISCORD_OWNER_ID:
        return True
    
    allowed_role_names = CONFIG['permissions'].get(action, [])
    # user.roles は Member オブジェクトの場合のみ存在 (DMなどでは注意が必要だが、今回はサーバー前提)
    user_role_ids = [r.id for r in getattr(user, 'roles', [])]
    
    for name in allowed_role_names:
        role_id = CONFIG['roles'].get(name)
        if role_id and role_id in user_role_ids:
            return True
            
    return False

def check_whitelist_add_permission(ctx_or_interaction, command_str):
    """
    'user' ロールが 'whitelist add' コマンドを実行する場合のみ許可する特例チェック
    Context と Interaction の両方に対応
    """
    # 既存の権限(admin/mod/owner)がある場合はOK
    if check_role(ctx_or_interaction, 'command'):
        return True

    # ユーザーオブジェクトの取得
    user = getattr(ctx_or_interaction, 'author', getattr(ctx_or_interaction, 'user', None))
    if not user:
        return False

    # userロールを持っているか確認
    user_role_id = CONFIG['roles'].get('user')
    user_role_ids = [r.id for r in getattr(user, 'roles', [])]
    
    if user_role_id and user_role_id in user_role_ids:
        # コマンド内容をチェック
        # 先頭の空白除去などを考慮
        cmd_body = command_str.strip()
        if cmd_body.startswith("whitelist add"):
            return True
            
    return False
