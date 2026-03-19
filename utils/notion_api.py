import os
import requests
from datetime import datetime
from settings import NOTION_TOKEN, NOTION_DB_ID, NOTION_DS_ID

NOTION_API_VERSION = "2026-03-11"

_data_source_id = None


def get_data_source_id():
    """NOTION_DB_ID から data_source_id を解決する。結果はキャッシュされる。"""
    global _data_source_id
    if _data_source_id:
        return _data_source_id

    if NOTION_DS_ID:
        _data_source_id = NOTION_DS_ID
        return _data_source_id

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_API_VERSION,
    }
    res = requests.get(
        f"https://api.notion.com/v1/databases/{NOTION_DB_ID}",
        headers=headers,
    )
    if res.status_code != 200:
        raise Exception(f"Failed to retrieve database: {res.text}")

    data_sources = res.json().get("data_sources", [])
    if not data_sources:
        raise Exception("No data sources found for database")

    _data_source_id = data_sources[0]["id"]
    return _data_source_id


def upload_to_notion(file_path, custom_filename=None, content_type="application/zip"):
    """
    Notion APIを使用してファイルをアップロードし、File IDを返す。
    20MBを超えるファイルは自動的にマルチパートアップロードとして処理する。
    custom_filenameが指定された場合、その名前でアップロードを初期化する。
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"{file_path} not found.")

    file_size = os.path.getsize(file_path)
    filename = custom_filename if custom_filename else os.path.basename(file_path)

    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json"
    }

    # 20MB以上のファイルはmulti_partアップロードとする (API制限)
    UPLOAD_THRESHOLD = 20 * 1024 * 1024
    is_multi_part = file_size > UPLOAD_THRESHOLD
    mode = "multi_part" if is_multi_part else "single_part"

    print(f"Uploading {filename} ({file_size / 1024 / 1024:.2f} MB) as {mode}...")
    init_payload = {
        "filename": filename,
        "content_type": content_type,
        "mode": mode
    }

    if is_multi_part:
        # 10MB単位で分割 (Notion API推奨: 5-20MB)
        chunk_size = 10 * 1024 * 1024
        init_payload["number_of_parts"] = (file_size // chunk_size) + (1 if file_size % chunk_size else 0)

    res = requests.post("https://api.notion.com/v1/file_uploads", headers=headers, json=init_payload)
    if res.status_code not in (200, 201):
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
                headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_API_VERSION},
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
                    headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_API_VERSION},
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

def register_to_database(file_upload_id, filename, size_mb):
    """アップロードしたファイルをDatabaseに登録"""
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json"
    }

    payload = {
        "parent": {"type": "data_source_id", "data_source_id": get_data_source_id()},
        "properties": {
            "Backup Name": {"title": [{"text": {"content": filename}}]},
            "Date": {"date": {"start": datetime.now().isoformat()}},
            "Size": {"number": size_mb},
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
    if res.status_code not in (200, 201):
        raise Exception(f"Failed to register to Notion DB: {res.text}")
    return True

def get_backups_list(limit=10):
    """Notion DBから最新のバックアップリストを取得"""
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json"
    }
    payload = {
        "sorts": [
            {
                "property": "Date",
                "direction": "descending"
            }
        ],
        "page_size": limit
    }

    ds_id = get_data_source_id()
    res = requests.post(f"https://api.notion.com/v1/data_sources/{ds_id}/query", headers=headers, json=payload)
    if res.status_code != 200:
        raise Exception(f"Failed to fetch backups: {res.text}")

    results = res.json().get("results", [])
    backups = []

    for page in results:
        if page.get("object") != "page":
            continue
        props = page["properties"]
        # プロパティ構造の解析
        title_list = props.get("Backup Name", {}).get("title", [])
        filename = title_list[0]["text"]["content"] if title_list else "Unknown"

        date_prop = props.get("Date", {}).get("date", {})
        date_str = date_prop.get("start") if date_prop else "Unknown"

        # ファイル情報の取得
        files = props.get("File", {}).get("files", [])
        if files:
            file_obj = files[0]
            # Notionホストか外部かでURLの場所が異なる
            file_url = file_obj.get("file", {}).get("url") or file_obj.get("external", {}).get("url")
            backups.append({
                "page_id": page["id"],
                "filename": filename,
                "date": date_str,
                "url": file_url
            })

    return backups

def download_file(url, save_path):
    """URLからファイルをダウンロード"""
    print(f"Downloading to {save_path}...")
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
