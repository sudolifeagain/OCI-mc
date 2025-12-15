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

    if DISCORD_OWNER_ID and user.id == DISCORD_OWNER_ID:
        return True
    
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
