import json
import logging
from settings import CONFIG, DISCORD_OWNER_ID


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

    # Ownerは常に許可
    if DISCORD_OWNER_ID and user.id == DISCORD_OWNER_ID:
        return True

    # ユーザー単位の権限チェック
    user_permissions = CONFIG.get('user_permissions', {})
    user_id_str = str(user.id)
    if user_id_str in user_permissions:
        if action in user_permissions[user_id_str]:
            return True

    # ロール単位の権限チェック
    allowed_role_names = CONFIG['permissions'].get(action, [])
    # user.roles は Member オブジェクトの場合のみ存在
    user_role_ids = [r.id for r in getattr(user, 'roles', [])]

    for name in allowed_role_names:
        role_config = CONFIG['roles'].get(name)
        if not role_config:
            continue

        # Support both single ID (int) and list of IDs (list)
        if isinstance(role_config, list):
            # Check if any of the allowed IDs for this logical role matches user's roles
            for valid_id in role_config:
                if valid_id in user_role_ids:
                    return True
        else:
            # Single ID case
            if role_config in user_role_ids:
                return True

    return False


def save_user_permissions():
    """user_permissions.jsonに保存する"""
    try:
        with open('user_permissions.json', 'w', encoding='utf-8') as f:
            json.dump(CONFIG.get('user_permissions', {}), f, indent='\t', ensure_ascii=False)
        return True
    except Exception as e:
        logging.error(f"Failed to save user_permissions: {e}")
        return False


def add_user_permission(user_id: int, action: str) -> bool:
    """ユーザーに権限を追加する"""
    if 'user_permissions' not in CONFIG:
        CONFIG['user_permissions'] = {}

    user_id_str = str(user_id)
    if user_id_str not in CONFIG['user_permissions']:
        CONFIG['user_permissions'][user_id_str] = []

    if action not in CONFIG['user_permissions'][user_id_str]:
        CONFIG['user_permissions'][user_id_str].append(action)
        return save_user_permissions()
    return True


def remove_user_permission(user_id: int, action: str) -> bool:
    """ユーザーから権限を削除する"""
    if 'user_permissions' not in CONFIG:
        return True

    user_id_str = str(user_id)
    if user_id_str not in CONFIG['user_permissions']:
        return True

    if action in CONFIG['user_permissions'][user_id_str]:
        CONFIG['user_permissions'][user_id_str].remove(action)
        # 空になったら削除
        if not CONFIG['user_permissions'][user_id_str]:
            del CONFIG['user_permissions'][user_id_str]
        return save_user_permissions()
    return True


def add_role_permission(role_name: str, action: str) -> bool:
    """ロールに権限を追加する"""
    if action not in CONFIG['permissions']:
        CONFIG['permissions'][action] = []

    if role_name not in CONFIG['permissions'][action]:
        CONFIG['permissions'][action].append(role_name)
        return save_user_permissions()
    return True


def remove_role_permission(role_name: str, action: str) -> bool:
    """ロールから権限を削除する"""
    if action not in CONFIG['permissions']:
        return True

    if role_name in CONFIG['permissions'][action]:
        CONFIG['permissions'][action].remove(role_name)
        return save_user_permissions()
    return True


def get_all_actions() -> list:
    """利用可能なすべてのアクションを取得する"""
    return list(CONFIG['permissions'].keys())


def get_user_permissions(user_id: int) -> list:
    """ユーザーの権限リストを取得する"""
    user_id_str = str(user_id)
    return CONFIG.get('user_permissions', {}).get(user_id_str, [])


def get_role_permissions(role_name: str) -> list:
    """ロールが持つ権限（アクション）のリストを取得する"""
    result = []
    for action, roles in CONFIG['permissions'].items():
        if role_name in roles:
            result.append(action)
    return result
