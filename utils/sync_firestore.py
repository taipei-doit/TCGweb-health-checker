"""
開機同步腳本 — 從 Firestore 同步網站清單與收件人設定到本地

用法:
    python utils/sync_firestore.py                    # 同步全部
    python utils/sync_firestore.py --websites-only    # 只同步網站清單
    python utils/sync_firestore.py --recipients-only  # 只同步收件人

資料流:
    Cloud Run 管理平台 → Firestore → (本腳本) → config/websites.csv + .env
"""

import os
import csv
import sys
import argparse


def sync_websites(output_path: str = "config/websites.csv") -> int:
    """
    從 Firestore `websites` collection 同步到本地 CSV。
    如果 Firestore 無資料或連線失敗，保留原有 CSV 不動。
    回傳同步的網站數量。
    """
    try:
        from google.cloud import firestore
        db = firestore.Client()
    except Exception as e:
        print(f"⚠️ 無法連線 Firestore: {e}")
        print("   將使用本地 config/websites.csv")
        return -1

    try:
        docs = db.collection("websites").stream()
        websites = []
        for doc in docs:
            data = doc.to_dict()
            url = data.get("url", "").strip()
            if url:
                websites.append({
                    "URL": url,
                    "name": data.get("name", ""),
                    "depth": data.get("depth", ""),
                    "save_html": data.get("save_html", ""),
                    "pagination": data.get("pagination", ""),
                })

        if not websites:
            print("⚠️ Firestore websites collection 為空，保留本地 CSV")
            return 0

        # 備份原有 CSV
        if os.path.exists(output_path):
            backup_path = output_path + ".bak"
            os.replace(output_path, backup_path)
            print(f"📁 已備份原有 CSV → {backup_path}")

        # 寫入新 CSV
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["URL", "name", "depth", "save_html", "pagination"])
            writer.writeheader()
            writer.writerows(websites)

        print(f"✅ 網站清單已同步: {len(websites)} 個網站 → {output_path}")
        return len(websites)

    except Exception as e:
        print(f"❌ 同步網站清單失敗: {e}")
        print("   將使用本地 config/websites.csv")
        return -1


def sync_recipients(env_path: str = ".env") -> int:
    """
    從 Firestore `email_recipients` collection 同步到 .env 的 TO_EMAIL。
    保留 .env 中的其他設定不動。
    回傳同步的收件人數量。
    """
    try:
        from google.cloud import firestore
        db = firestore.Client()
    except Exception as e:
        print(f"⚠️ 無法連線 Firestore: {e}")
        print("   將使用 .env 中的 TO_EMAIL")
        return -1

    try:
        docs = db.collection("email_recipients").stream()
        emails = []
        for doc in docs:
            data = doc.to_dict()
            email = data.get("email", "").strip()
            if email:
                emails.append(email)

        if not emails:
            print("⚠️ Firestore email_recipients collection 為空，保留 .env 設定")
            return 0

        recipients_str = ", ".join(emails)

        # 讀取現有 .env
        env_lines = []
        to_email_found = False

        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("TO_EMAIL="):
                        env_lines.append(f"TO_EMAIL={recipients_str}\n")
                        to_email_found = True
                    else:
                        env_lines.append(line)

        if not to_email_found:
            env_lines.append(f"\nTO_EMAIL={recipients_str}\n")

        # 寫回 .env
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(env_lines)

        print(f"✅ 收件人已同步: {len(emails)} 位 → .env TO_EMAIL")
        for email in emails:
            print(f"   - {email}")
        return len(emails)

    except Exception as e:
        print(f"❌ 同步收件人失敗: {e}")
        print("   將使用 .env 中的 TO_EMAIL")
        return -1


def main():
    parser = argparse.ArgumentParser(description="從 Firestore 同步設定到本地")
    parser.add_argument("--websites-only", action="store_true", help="只同步網站清單")
    parser.add_argument("--recipients-only", action="store_true", help="只同步收件人")
    parser.add_argument("--csv-path", default="config/websites.csv", help="CSV 輸出路徑")
    parser.add_argument("--env-path", default=".env", help=".env 檔案路徑")
    args = parser.parse_args()

    print("=" * 50)
    print("  Firestore → 本地同步")
    print("=" * 50)

    sync_all = not args.websites_only and not args.recipients_only

    if sync_all or not args.recipients_only:
        sync_websites(args.csv_path)

    if sync_all or not args.websites_only:
        sync_recipients(args.env_path)

    print("=" * 50)
    print("  同步完成")
    print("=" * 50)


if __name__ == "__main__":
    main()
