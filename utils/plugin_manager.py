"""
Plugin Manager Utility
プラグインの一覧取得・更新チェック・ダウンロードを行うユーティリティ
"""
import os
import zipfile
import yaml
from typing import Optional


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
    except (zipfile.BadZipFile, yaml.YAMLError, KeyError) as e:
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


# スタンドアロン実行用
if __name__ == "__main__":
    import sys
    
    # デフォルトパスまたはコマンドライン引数からパスを取得
    plugins_path = sys.argv[1] if len(sys.argv) > 1 else "/opt/minecraft/paper/plugins"
    
    print(f"Scanning: {plugins_path}\n")
    
    plugins = list_plugins(plugins_path)
    print(format_plugins_list(plugins, detailed=True))
