import os
import requests
from datetime import datetime
from settings import NOTION_TOKEN, NOTION_DB_ID

def upload_to_notion(file_path):
    """
    Notion APIを使用してファイルをアップロードし、File IDを返します。
    20MBを超えるファイルは自動的にマルチパートアップロードとして処理します。
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"{file_path} not found.")

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

def get_backups_list(limit=10):
    """Notion DBから最新のバックアップリストを取得"""
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
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

    res = requests.post(f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query", headers=headers, json=payload)
    if res.status_code != 200:
        raise Exception(f"Failed to fetch backups: {res.text}")

    results = res.json().get("results", [])
    backups = []

    for page in results:
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
