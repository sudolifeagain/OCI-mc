"""
Plugin Manager Utility
プラグインの一覧取得・更新チェック・ダウンロードを行うユーティリティ
"""
import fnmatch
import hashlib
import logging
import os
import shutil
import tempfile
import zipfile
from typing import Optional

import requests
import yaml


def get_plugins_dir(server_cwd: str) -> str:
    """サーバーディレクトリからpluginsフォルダのパスを取得"""
    return os.path.join(server_cwd, "plugins")


def parse_plugin_yml(jar_path: str) -> Optional[dict]:
    """
    JARファイルからplugin.ymlを読み取り、プラグイン情報を抽出する

    Returns:
        dict with keys: name, version, main, api_version, description, authors, website
        or None if plugin.yml is not found or invalid
    """
    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            # plugin.yml または paper-plugin.yml を探す
            yml_name = None
            for name in zf.namelist():
                if name == 'plugin.yml' or name == 'paper-plugin.yml':
                    yml_name = name
                    break

            if not yml_name:
                return None

            with zf.open(yml_name) as yml_file:
                data = yaml.safe_load(yml_file)
                if not data:
                    return None

                return {
                    'name': data.get('name', 'Unknown'),
                    'version': str(data.get('version', 'Unknown')),
                    'main': data.get('main', ''),
                    'api_version': data.get('api-version', ''),
                    'description': data.get('description', ''),
                    'authors': data.get('authors', data.get('author', [])),
                    'website': data.get('website', ''),
                }
    except (zipfile.BadZipFile, yaml.YAMLError, KeyError):
        return None


def list_plugins(plugins_dir: str) -> list[dict]:
    """
    pluginsディレクトリ内のすべてのプラグイン情報を取得する

    Returns:
        list of dicts with keys: filename, filepath, name, version, api_version, etc.
    """
    plugins = []

    if not os.path.exists(plugins_dir):
        return plugins

    for filename in os.listdir(plugins_dir):
        if not filename.endswith('.jar'):
            continue

        filepath = os.path.join(plugins_dir, filename)
        file_size = os.path.getsize(filepath) / (1024 * 1024)  # MB

        plugin_info = {
            'filename': filename,
            'filepath': filepath,
            'size_mb': round(file_size, 2),
        }

        # plugin.yml からメタデータを取得
        yml_data = parse_plugin_yml(filepath)
        if yml_data:
            plugin_info.update(yml_data)
        else:
            # plugin.yml が読めない場合はファイル名から推測
            plugin_info['name'] = filename.replace('.jar', '')
            plugin_info['version'] = 'Unknown'

        plugins.append(plugin_info)

    # プラグイン名でソート
    plugins.sort(key=lambda x: x.get('name', '').lower())

    return plugins


def format_plugins_list(plugins: list[dict], detailed: bool = False) -> str:
    """
    プラグインリストを整形した文字列に変換する

    Args:
        plugins: list_plugins() の戻り値
        detailed: 詳細表示するかどうか
    """
    if not plugins:
        return "プラグインが見つかりませんでした。"

    lines = [f"📦 プラグイン一覧 ({len(plugins)}件)\n"]
    lines.append("```")

    for p in plugins:
        name = p.get('name', 'Unknown')
        version = p.get('version', '?')

        if detailed:
            api = p.get('api_version', '')
            size = p.get('size_mb', 0)
            api_str = f" (API: {api})" if api else ""
            lines.append(f"• {name} v{version}{api_str} [{size:.1f}MB]")
            lines.append(f"  └─ {p.get('filename', '')}")
        else:
            lines.append(f"• {name}: v{version}")

    lines.append("```")
    return "\n".join(lines)


# ============================================
# プラグインダウンロード機能
# ============================================


def find_plugin_by_pattern(plugins_dir: str, pattern: str) -> Optional[str]:
    """
    パターンに一致するプラグインファイルを検索

    Args:
        plugins_dir: pluginsディレクトリのパス
        pattern: ファイル名のパターン (fnmatch形式, 例: "bluemap-*-paper.jar")

    Returns:
        見つかったファイルのフルパス、見つからない場合はNone
        複数見つかった場合は最初のものを返す
    """
    if not os.path.exists(plugins_dir):
        return None

    for filename in os.listdir(plugins_dir):
        if fnmatch.fnmatch(filename, pattern):
            return os.path.join(plugins_dir, filename)

    return None


def delete_plugins_by_pattern(plugins_dir: str, pattern: str) -> int:
    """
    パターンに一致するプラグインファイルをすべて削除

    Args:
        plugins_dir: pluginsディレクトリのパス
        pattern: ファイル名のパターン (fnmatch形式)

    Returns:
        削除したファイル数
    """
    if not os.path.exists(plugins_dir):
        return 0

    deleted = 0
    for filename in os.listdir(plugins_dir):
        # 安全対策: .jar ファイルのみ削除可能
        if fnmatch.fnmatch(filename, pattern) and filename.endswith('.jar'):
            filepath = os.path.join(plugins_dir, filename)
            try:
                os.remove(filepath)
                deleted += 1
            except Exception as e:
                logging.error(f"Failed to delete {filepath}: {e}")

    return deleted


def get_github_latest_release_asset(repo: str, asset_pattern: str) -> Optional[dict]:
    """
    GitHubの最新リリースから指定パターンに一致するアセットを取得

    Args:
        repo: "owner/repo" 形式のリポジトリ名
        asset_pattern: ファイル名のパターン (fnmatch形式, 例: "bluemap-*-paper.jar")

    Returns:
        dict with keys: name, download_url, size, tag_name (version)
        or None if not found
    """
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        headers = {"Accept": "application/vnd.github.v3+json"}

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return None

        data = resp.json()
        tag_name = data.get("tag_name", "")

        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if fnmatch.fnmatch(name, asset_pattern):
                return {
                    "name": name,
                    "download_url": asset.get("browser_download_url"),
                    "size": asset.get("size", 0),
                    "tag_name": tag_name,
                }

        return None
    except Exception as e:
        logging.error(f"GitHub API error ({repo}): {e}")
        return None


def download_file(url: str, dest_path: str, timeout: int = 300) -> bool:
    """
    URLからファイルをダウンロード

    Args:
        url: ダウンロードURL
        dest_path: 保存先パス
        timeout: タイムアウト秒数

    Returns:
        True if successful, False otherwise
    """
    try:
        with requests.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            with open(dest_path, 'wb') as f:
                shutil.copyfileobj(resp.raw, f)
        return True
    except Exception as e:
        logging.error(f"Download error ({url}): {e}")
        return False


def update_plugin(plugins_dir: str, plugin_config: dict) -> dict:
    """
    設定に基づいてプラグインを更新する

    Args:
        plugins_dir: pluginsディレクトリのパス
        plugin_config: プラグインの設定 (source, url/repo, filename等)

    Returns:
        dict with keys: success, message, filename
    """
    source = plugin_config.get("source", "")
    filename = plugin_config.get("filename", "")
    filename_pattern = plugin_config.get("filename_pattern", "")

    # GitHubソースでパターンがある場合、ダウンロードファイル名は後で決定
    use_pattern = bool(filename_pattern)

    if not filename and not filename_pattern:
        return {"success": False, "message": "filename が設定されていません", "filename": ""}

    # 一時ファイルにダウンロード
    temp_filename = filename if filename else "temp_plugin.jar"
    temp_path = os.path.join(tempfile.gettempdir(), f"plugin_download_{temp_filename}")

    try:
        if source == "direct":
            # 直接ダウンロード
            url = plugin_config.get("url", "")
            if not url:
                return {"success": False, "message": "URL が設定されていません", "filename": filename}

            if not download_file(url, temp_path):
                return {"success": False, "message": "ダウンロードに失敗しました", "filename": filename}

        elif source == "github":
            # GitHub releases から取得
            repo = plugin_config.get("repo", "")
            asset_pattern = plugin_config.get("asset_pattern", "")

            if not repo or not asset_pattern:
                return {"success": False, "message": "repo または asset_pattern が設定されていません", "filename": filename or asset_pattern}

            asset = get_github_latest_release_asset(repo, asset_pattern)
            if not asset:
                return {"success": False, "message": f"GitHub releases でアセットが見つかりません: {asset_pattern}", "filename": filename or asset_pattern}

            # パターン使用時は元のファイル名で保存
            if use_pattern:
                filename = asset.get("name", "")
                temp_path = os.path.join(tempfile.gettempdir(), f"plugin_download_{filename}")

            if not download_file(asset["download_url"], temp_path):
                return {"success": False, "message": "ダウンロードに失敗しました", "filename": filename}

        elif source == "geysermc":
            # GeyserMC API から取得
            project = plugin_config.get("project", "")
            platform = plugin_config.get("platform", "spigot")

            if not project:
                return {"success": False, "message": "project が設定されていません", "filename": filename}

            info = get_geysermc_latest_info(project, platform)
            if not info:
                return {"success": False, "message": "GeyserMC API からダウンロードURLを取得できません", "filename": filename}

            if not download_file(info["download_url"], temp_path):
                return {"success": False, "message": "ダウンロードに失敗しました", "filename": filename}

        else:
            return {"success": False, "message": f"不明なソース: {source}", "filename": filename or ""}

        # ダウンロード成功 - 既存ファイルを置換
        dest_path = os.path.join(plugins_dir, filename)

        # パターン使用時は古いバージョンをすべて削除
        if use_pattern and filename_pattern:
            delete_plugins_by_pattern(plugins_dir, filename_pattern)
        elif os.path.exists(dest_path):
            os.remove(dest_path)

        shutil.move(temp_path, dest_path)

        return {"success": True, "message": "更新完了", "filename": filename}

    except Exception as e:
        # クリーンアップ
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return {"success": False, "message": str(e), "filename": filename}


def update_all_plugins(plugins_dir: str, plugins_config: dict) -> list[dict]:
    """
    設定されているすべてのプラグインを更新

    Args:
        plugins_dir: pluginsディレクトリのパス
        plugins_config: プラグイン設定の辞書 {plugin_name: config}

    Returns:
        list of update results
    """
    results = []
    for plugin_name, config in plugins_config.items():
        result = update_plugin(plugins_dir, config)
        result["plugin_name"] = plugin_name
        results.append(result)
    return results


# ============================================
# プラグイン更新チェック機能
# ============================================


def compute_file_sha256(filepath: str) -> Optional[str]:
    """
    ファイルのSHA256ハッシュを計算する

    Args:
        filepath: ファイルパス

    Returns:
        SHA256ハッシュ文字列（小文字16進数）、エラー時はNone
    """
    try:
        sha256_hash = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()
    except Exception as e:
        logging.error(f"SHA256 calculation error ({filepath}): {e}")
        return None


def get_geysermc_latest_info(project: str, platform: str) -> Optional[dict]:
    """
    GeyserMC APIから最新ビルド情報を取得

    Args:
        project: "geyser" または "floodgate"
        platform: "spigot", "bungeecord", "velocity" など

    Returns:
        dict with keys: version, build, time, sha256, download_url
        or None if error
    """
    try:
        url = f"https://download.geysermc.org/v2/projects/{project}/versions/latest/builds/latest"
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            return None

        data = resp.json()
        downloads = data.get("downloads", {})
        platform_info = downloads.get(platform, {})

        if not platform_info:
            return None

        download_url = f"https://download.geysermc.org/v2/projects/{project}/versions/latest/builds/latest/downloads/{platform}"

        return {
            "version": data.get("version", ""),
            "build": data.get("build", 0),
            "time": data.get("time", ""),
            "sha256": platform_info.get("sha256", ""),
            "download_url": download_url,
            "filename": platform_info.get("name", ""),
        }
    except Exception as e:
        logging.error(f"GeyserMC API error ({project}/{platform}): {e}")
        return None


def get_github_latest_info(repo: str, asset_pattern: str) -> Optional[dict]:
    """
    GitHubの最新リリースから指定パターンに一致するアセット情報を取得（sha256付き）

    Args:
        repo: "owner/repo" 形式のリポジトリ名
        asset_pattern: ファイル名のパターン (fnmatch形式)

    Returns:
        dict with keys: name, download_url, size, tag_name, sha256
        or None if not found
    """
    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        headers = {"Accept": "application/vnd.github.v3+json"}

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return None

        data = resp.json()
        tag_name = data.get("tag_name", "")
        published_at = data.get("published_at", "")

        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if fnmatch.fnmatch(name, asset_pattern):
                # digestフィールドからsha256を抽出（形式: "sha256:..."）
                digest = asset.get("digest", "")
                sha256 = digest.replace("sha256:", "") if digest.startswith("sha256:") else ""

                return {
                    "name": name,
                    "download_url": asset.get("browser_download_url"),
                    "size": asset.get("size", 0),
                    "tag_name": tag_name,
                    "published_at": published_at,
                    "sha256": sha256,
                }

        return None
    except Exception as e:
        logging.error(f"GitHub API error ({repo}): {e}")
        return None


def check_plugin_update(plugins_dir: str, plugin_name: str, plugin_config: dict) -> dict:
    """
    単一プラグインの更新をチェックする

    Args:
        plugins_dir: pluginsディレクトリのパス
        plugin_name: プラグイン名（設定キー）
        plugin_config: プラグインの設定

    Returns:
        dict with keys: plugin_name, has_update, installed_version, latest_version, error
    """
    result = {
        "plugin_name": plugin_name,
        "has_update": False,
        "installed_version": "不明",
        "latest_version": "不明",
        "error": None,
    }

    source = plugin_config.get("source", "")
    filename = plugin_config.get("filename", "")
    filename_pattern = plugin_config.get("filename_pattern", "")

    # ファイルパスを決定（パターンマッチまたは固定ファイル名）
    if filename_pattern:
        filepath = find_plugin_by_pattern(plugins_dir, filename_pattern)
    elif filename:
        filepath = os.path.join(plugins_dir, filename)
    else:
        result["error"] = "filename が設定されていません"
        return result

    # ローカルファイルのSHA256を計算
    if not filepath or not os.path.exists(filepath):
        result["error"] = "ファイルが見つかりません"
        result["installed_version"] = "未インストール"
        result["has_update"] = True
        return result

    local_sha256 = compute_file_sha256(filepath)
    if not local_sha256:
        result["error"] = "SHA256計算に失敗"
        return result

    # リモートの最新情報を取得
    remote_sha256 = None

    if source == "geysermc":
        project = plugin_config.get("project", "")
        platform = plugin_config.get("platform", "spigot")

        info = get_geysermc_latest_info(project, platform)
        if info:
            remote_sha256 = info.get("sha256", "")
            latest_build = info.get('build', '?')
            result["latest_version"] = f"Build #{latest_build}"

            # ローカルバージョンはplugin.ymlから取得
            plugin_info = parse_plugin_yml(filepath)
            if plugin_info:
                local_version = plugin_info.get("version", "不明")
                result["installed_version"] = local_version
        else:
            result["error"] = "API取得に失敗"
            return result

    elif source == "github":
        repo = plugin_config.get("repo", "")
        asset_pattern = plugin_config.get("asset_pattern", "")

        info = get_github_latest_info(repo, asset_pattern)
        if info:
            remote_sha256 = info.get("sha256", "")
            result["latest_version"] = info.get("tag_name", "不明")
            # ローカルバージョンはplugin.ymlから取得
            plugin_info = parse_plugin_yml(filepath)
            if plugin_info:
                result["installed_version"] = plugin_info.get("version", "不明")
        else:
            result["error"] = "API取得に失敗"
            return result

    elif source == "direct":
        # 直接URLの場合はsha256比較ができないためスキップ
        result["error"] = "directソースは更新チェック非対応"
        return result

    else:
        result["error"] = f"不明なソース: {source}"
        return result

    # SHA256比較
    if remote_sha256 and local_sha256:
        result["has_update"] = (local_sha256.lower() != remote_sha256.lower())

    return result


def check_all_plugin_updates(plugins_dir: str, plugins_config: dict) -> list[dict]:
    """
    設定されているすべてのプラグインの更新をチェック

    Args:
        plugins_dir: pluginsディレクトリのパス
        plugins_config: プラグイン設定の辞書 {plugin_name: config}

    Returns:
        list of check results
    """
    results = []
    for plugin_name, config in plugins_config.items():
        result = check_plugin_update(plugins_dir, plugin_name, config)
        results.append(result)
    return results


# スタンドアロン実行用
if __name__ == "__main__":
    import sys

    # デフォルトパスまたはコマンドライン引数からパスを取得
    plugins_path = sys.argv[1] if len(sys.argv) > 1 else "/opt/minecraft/paper/plugins"

    print(f"Scanning: {plugins_path}\n")

    plugins = list_plugins(plugins_path)
    print(format_plugins_list(plugins, detailed=True))


