import os
import smtplib
import zipfile
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from datetime import datetime

try:
    from google.cloud import firestore as google_firestore
    HAS_FIRESTORE = True
except ImportError:
    HAS_FIRESTORE = False


class EmailReporter:
    def __init__(self):
        """
        從 .env 載入 Email 憑證，支援兩種 SMTP 後端：
          1. Amazon SES (優先) — 設定 SMTP_HOST 即啟用
          2. Gmail (備援)     — 傳統 Gmail 應用程式密碼

        環境變數：
          通用：   SMTP_FROM, TO_EMAIL
          SES：    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
          Gmail：  GMAIL_USER, GMAIL_APP_PASSWORD
        """
        # ---------- 判斷 SMTP 後端 ----------
        self.smtp_host = os.getenv("SMTP_HOST", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.from_email = os.getenv("SMTP_FROM", "")

        # SES 模式：SMTP_HOST 有設定
        if self.smtp_host and self.smtp_user and self.smtp_password:
            self.provider = "SES"
            if not self.from_email:
                self.from_email = f"noreply@{os.getenv('SMTP_DOMAIN', 'mail.app.taipei')}"
            self.valid = True
        else:
            # Gmail 備援模式
            self.provider = "Gmail"
            gmail_user = os.getenv("GMAIL_USER", "")
            gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")
            if gmail_user and gmail_password:
                self.smtp_host = "smtp.gmail.com"
                self.smtp_port = 465
                self.smtp_user = gmail_user
                self.smtp_password = gmail_password
                if not self.from_email:
                    self.from_email = gmail_user
                self.valid = True
            else:
                print("❌ 錯誤：未設定任何 SMTP 憑證 (SES 或 Gmail)")
                self.valid = False
                self.to_emails = []
                return

        # 20MB 限制 (SES 附件上限 40MB，但保守使用 20MB)
        self.MAX_ZIP_SIZE_BYTES = 20 * 1024 * 1024

        # ---------- 載入收件者 ----------
        try:
            recipients = self._load_recipients_from_firestore()
            if recipients:
                self.to_emails = recipients
            else:
                raise Exception("No Firestore recipients")
        except Exception:
            to_email_str = os.getenv("TO_EMAIL", self.from_email)
            self.to_emails = [e.strip() for e in to_email_str.split(",") if e.strip()]

        print(f"📧 EmailReporter 已初始化")
        print(f"   後端: {self.provider} ({self.smtp_host}:{self.smtp_port})")
        print(f"   發送者: {self.from_email}")
        print(f"   收件者: {', '.join(self.to_emails)}")

    def _load_recipients_from_firestore(self):
        """
        從 Firestore 集合 `email_recipients` 載入收件者清單。
        每個文件應包含 `email` 欄位。
        若 Firestore 未安裝或連線失敗，回傳空清單。
        """
        if not HAS_FIRESTORE:
            return []

        try:
            db = google_firestore.Client()
            docs = db.collection("email_recipients").stream()
            recipients = []
            for doc in docs:
                data = doc.to_dict()
                email = data.get("email", "").strip()
                if email:
                    recipients.append(email)
            return recipients
        except Exception as e:
            print(f"⚠️ 無法從 Firestore 載入收件者: {e}")
            return []

    def set_recipients(self, emails: list):
        """以程式方式覆寫收件者清單"""
        self.to_emails = [e.strip() for e in emails if e.strip()]
        print(f"📧 收件者已更新: {', '.join(self.to_emails)}")

    def add_recipient(self, email: str):
        """新增一位收件者"""
        email = email.strip()
        if email and email not in self.to_emails:
            self.to_emails.append(email)
            print(f"📧 已新增收件者: {email} (目前共 {len(self.to_emails)} 位)")

    def remove_recipient(self, email: str):
        """移除一位收件者"""
        email = email.strip()
        if email in self.to_emails:
            self.to_emails.remove(email)
            print(f"📧 已移除收件者: {email} (目前共 {len(self.to_emails)} 位)")
        else:
            print(f"⚠️ 收件者 {email} 不在清單中")

    def _connect_smtp(self):
        """
        建立 SMTP 連線，根據 port 自動選擇連線方式：
          - 465: SMTP_SSL (TLS Wrapper)
          - 587/其他: STARTTLS
        """
        if self.smtp_port == 465:
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
            server.starttls()
        server.login(self.smtp_user, self.smtp_password)
        return server

    def _send_part(self, zip_filename: str, part_num: int, total_parts: int, files_in_zip: list):
        """
        發送單一的 .zip 附件給所有收件者
        """
        if not self.valid:
            return False

        recipients_str = ", ".join(self.to_emails)
        print(f"📧 準備發送 Email (Part {part_num}/{total_parts}) via {self.provider} -> {recipients_str}")

        try:
            zip_size_mb = os.path.getsize(zip_filename) / 1024 / 1024

            msg = MIMEMultipart()
            msg['Subject'] = f"[TCGweb] 網站健康檢查報告 Part {part_num}/{total_parts} - {datetime.now().strftime('%Y-%m-%d')}"
            msg['From'] = self.from_email
            msg['To'] = ", ".join(self.to_emails)

            body_files = "\n".join([f"- {f}" for f in files_in_zip[:10]])
            if len(files_in_zip) > 10:
                body_files += f"\n... 及其他 {len(files_in_zip) - 10} 個檔案"

            body = (
                f"TCGweb 網站健康檢查報告\n\n"
                f"爬蟲任務已完成。\n"
                f"這是 {total_parts} 封郵件中的第 {part_num} 封。\n\n"
                f"此壓縮檔包含以下內容：\n{body_files}\n\n"
                f"壓縮檔大小: {zip_size_mb:.2f} MB\n\n"
                f"---\n"
                f"發送自: {self.provider} ({self.smtp_host})\n"
                f"發送域名: {self.from_email}"
            )
            msg.attach(MIMEText(body, 'plain'))

            with open(zip_filename, "rb") as f:
                part = MIMEApplication(f.read(), Name=zip_filename)
            part['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
            msg.attach(part)

            with self._connect_smtp() as server:
                server.send_message(msg)
                print(f"✅ Email (Part {part_num}) 發送成功！已寄送給 {len(self.to_emails)} 位收件者")
                return True

        except Exception as e:
            print(f"❌ Email (Part {part_num}) 發送失敗: {e}")
            return False

    def pack_and_send_simple(self, excel_report_path: str):
        """
        簡單版本：打包所有內容到單一 ZIP 檔案並發送
        如果檔案太大會警告但仍嘗試發送，有可能報錯失敗
        """
        if not self.valid:
            print("⚠️ EmailReporter 憑證無效，跳過郵寄。")
            return False

        print("📦 開始執行打包與郵寄...")

        # zip 檔名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        zip_filename = f"website_check_results_{timestamp}.zip"

        try:
            files_in_zip = []
            with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:

                # 加入 Excel 報告
                if os.path.exists(excel_report_path):
                    print(f"    + 加入報告: {excel_report_path}")
                    zipf.write(excel_report_path, os.path.basename(excel_report_path))
                    files_in_zip.append(os.path.basename(excel_report_path))
                else:
                    print(f"⚠️ 找不到報告檔: {excel_report_path}")

                # 加入爬蟲執行日誌
                vm_log_path = os.path.expanduser('~/crawler_execution.log')
                if os.path.exists(vm_log_path):
                    print(f"    + 加入日誌: {vm_log_path}")
                    zipf.write(vm_log_path, "crawler_execution.log")
                    files_in_zip.append("crawler_execution.log")
                else:
                    print(f"⚠️ 找不到日誌檔: {vm_log_path}")

                # 加入 assets 資料夾
                if os.path.exists("assets"):
                    print("    + 加入 assets 資料夾...")
                    for root, dirs, files in os.walk("assets"):
                        for file in files:
                            # 跳過 HTML 檔案
                            # if file.lower().endswith('.html'):
                            #     continue

                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, start=".")
                            zipf.write(file_path, arcname)
                else:
                    print("⚠️ 找不到 assets 資料夾")

            print(f"✅ 壓縮完成: {zip_filename}")

            # 檢查檔案大小
            zip_size_mb = os.path.getsize(zip_filename) / 1024 / 1024
            if zip_size_mb > 25:
                print(f"⚠️ 警告：檔案大小 {zip_size_mb:.2f} MB 超過 Gmail 25MB 限制")
                print("💡 建議使用 pack_and_send_multi_part() 方法進行分割發送")

            # 發送郵件
            success = self._send_part(zip_filename, 1, 1, files_in_zip)

            # 清理檔案
            if os.path.exists(zip_filename):
                os.remove(zip_filename)
                print(f"🗑️ 已清理暫存檔案: {zip_filename}")

            return success

        except Exception as e:
            print(f"❌ 打包或發送過程發生錯誤: {e}")
            # 清理可能存在的暫存檔案
            if os.path.exists(zip_filename):
                os.remove(zip_filename)
            return False

    def pack_and_send_seperate(self, excel_report_path: str):
        """
        分割打包，避免超過 Gmail 大小限制
        每個網站資料夾分別打包，確保不超過 20MB
        """
        if not self.valid:
            print("⚠️ EmailReporter 憑證無效，跳過郵寄。")
            return False

        print("📦 開始執行分割打包與郵寄...")

        zip_files_info = []  # (檔案名, 檔案大小, 內容清單)
        max_size_mb = 20 # 每個包的最大大小 (MB)
        max_size_bytes = max_size_mb * 1024 * 1024

        # === 第一包：(Excel + Log) ===
        print("準備(Excel + Log)...")
        first_zip_filename = "crawl_data_part1.zip"
        first_files_list = []

        with zipfile.ZipFile(first_zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 加入 Excel 報告
            if os.path.exists(excel_report_path):
                print(f"加入報告: {excel_report_path}")
                zipf.write(excel_report_path, os.path.basename(excel_report_path))
                first_files_list.append(os.path.basename(excel_report_path))
            else:
                print(f"⚠️ 找不到 Excel 報告: {excel_report_path}")

            # 加入爬蟲執行日誌
            vm_log_path = os.path.expanduser('~/crawler_execution.log')
            if os.path.exists(vm_log_path):
                print(f"加入日誌: {vm_log_path}")
                zipf.write(vm_log_path, "crawler_execution.log")
                first_files_list.append("crawler_execution.log")
            else:
                print(f"⚠️ 找不到執行日誌: {vm_log_path}")

        # 記錄第一個 ZIP 檔案資訊
        first_size = os.path.getsize(first_zip_filename)
        zip_files_info.append((first_zip_filename, first_size, first_files_list))
        first_size_mb = first_size//1024//1024
        print(f"Part 1 完成 ({first_size_mb:.1f} MB, 基本檔案)")

        # === 後續包：按大小動態處理 assets 資料夾 ===
        assets_dir = "assets"
        website_folders = []
        if os.path.exists(assets_dir):
            website_folders = [d for d in os.listdir(assets_dir) if os.path.isdir(os.path.join(assets_dir, d))]
            print(f"找到 {len(website_folders)} 個網站資料夾")
        else:
            print("⚠️ 沒有找到 assets 資料夾")

        if website_folders:
            part_num = 2
            website_index = 0

            while website_index < len(website_folders):
                current_zip_filename = f"crawl_data_part{part_num}.zip"
                files_in_current_zip = []

                print(f"  準備 Part {part_num} (網站資料)...")

                zipf = zipfile.ZipFile(current_zip_filename, 'w', zipfile.ZIP_DEFLATED)

                try:
                    # 逐一加入網站資料夾，檢查大小限制
                    while website_index < len(website_folders):
                        folder_name = website_folders[website_index]
                        folder_path = os.path.join(assets_dir, folder_name)

                        # 先加入這個資料夾的所有檔案
                        for root, dirs, files in os.walk(folder_path):
                            for file in files:
                                # 跳過 HTML 檔案
                                # if file.lower().endswith('.html'):
                                #     continue

                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, start=".")
                                zipf.write(file_path, arcname)

                        files_in_current_zip.append(f"{folder_name}/ (網站資料夾)")
                        website_index += 1

                        # 檢查當前大小
                        zipf.close()
                        current_size = os.path.getsize(current_zip_filename)

                        # 如果超過限制且不是第一個資料夾，就停止加入更多
                        if current_size > max_size_bytes and len(files_in_current_zip) > 1:
                            print(f"⚠️ 達到 {current_size//1024//1024}MB 限制，此包完成")
                            break
                        elif website_index < len(website_folders):
                            # 還沒超過限制，重新開啟準備加入下一個
                            zipf = zipfile.ZipFile(current_zip_filename, 'a', zipfile.ZIP_DEFLATED)

                finally:
                    if zipf.fp is not None and not zipf.fp.closed:
                        zipf.close()

                # 記錄 ZIP 檔案資訊
                final_size = os.path.getsize(current_zip_filename)
                zip_files_info.append((current_zip_filename, final_size, files_in_current_zip))
                final_size_mb = final_size//1024//1024
                print(f"Part {part_num} 完成 ({final_size_mb:.1f} MB, 包含 {len(files_in_current_zip)} 個網站)")
                part_num += 1

        total_parts = len(zip_files_info)
        print(f"📦 所有 ZIP 包準備完成！總共 {total_parts} 個包")

        # === 第二階段：依序寄送所有 ZIP 檔案 ===
        print(f"\n📧 開始寄送所有包給 {len(self.to_emails)} 位收件者...")
        success_count = 0

        for i, (zip_filename, zip_size, file_list) in enumerate(zip_files_info, 1):
            zip_size_mb = zip_size / 1024 / 1024
            print(f"  寄送 Part {i}/{total_parts} ({zip_size_mb:.1f} MB)...")

            if self._send_part(zip_filename, i, total_parts, file_list):
                success_count += 1
                print(f"    ✅ Part {i} 寄送成功")
            else:
                print(f"    ❌ Part {i} 寄送失敗")

            # 清理已寄送的檔案
            os.remove(zip_filename)

        print(f"\n🎉 多批次郵寄完成！成功發送 {success_count}/{total_parts} 個包給 {len(self.to_emails)} 位收件者")
        return success_count == total_parts
