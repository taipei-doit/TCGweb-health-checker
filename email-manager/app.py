"""
網站健康檢查管理平台 - Cloud Run 版
管理功能：Email 收件人名單 + 網站清單 + 爬蟲控制
"""
import os
import re
import csv
import io
from datetime import datetime

from flask import Flask, render_template, request, jsonify
from google.cloud import firestore

try:
    from google.cloud import compute_v1
    HAS_COMPUTE = True
except ImportError:
    HAS_COMPUTE = False

try:
    from google.cloud import storage as gcs_storage
    HAS_GCS = True
except ImportError:
    HAS_GCS = False

app = Flask(__name__)
db = firestore.Client()

# ============================================================
#  Config
# ============================================================
RECIPIENTS_COLLECTION = "email_recipients"
WEBSITES_COLLECTION = "websites"
DEFAULT_VM_NAME = os.environ.get("CRAWLER_VM_NAME", "crawler-webcheck")
DEFAULT_ZONE = os.environ.get("CRAWLER_VM_ZONE", "asia-east1-c")
GCP_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GCS_BUCKET = os.environ.get("GCS_REPORT_BUCKET", "doit-dic-itteam-crawler-reports")


# ============================================================
#  頁面路由
# ============================================================
@app.route("/")
def index():
    """主頁 - 管理介面"""
    return render_template("index.html")


# ============================================================
#  Email 收件人 API
# ============================================================
@app.route("/api/recipients", methods=["GET"])
def get_recipients():
    """取得所有 Email 收件人"""
    try:
        docs = db.collection(RECIPIENTS_COLLECTION).order_by("created_at", direction=firestore.Query.DESCENDING).stream()
        recipients = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            # 轉換 timestamp 為字串
            if data.get("created_at"):
                data["created_at"] = data["created_at"].strftime("%Y-%m-%d %H:%M")
            recipients.append(data)
        return jsonify({"success": True, "data": recipients})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/recipients", methods=["POST"])
def add_recipient():
    """新增 Email 收件人"""
    try:
        body = request.get_json()
        email = body.get("email", "").strip()
        name = body.get("name", "").strip()

        if not email:
            return jsonify({"success": False, "error": "Email 不可為空"}), 400

        # 驗證 email 格式
        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
            return jsonify({"success": False, "error": "Email 格式不正確"}), 400

        # 檢查是否已存在
        existing = db.collection(RECIPIENTS_COLLECTION).where("email", "==", email).limit(1).stream()
        if any(True for _ in existing):
            return jsonify({"success": False, "error": f"'{email}' 已存在於收件人名單中"}), 409

        doc_ref = db.collection(RECIPIENTS_COLLECTION).add({
            "email": email,
            "name": name or email.split("@")[0],
            "created_at": firestore.SERVER_TIMESTAMP,
        })

        return jsonify({"success": True, "id": doc_ref[1].id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/recipients/<doc_id>", methods=["DELETE"])
def delete_recipient(doc_id):
    """刪除 Email 收件人"""
    try:
        db.collection(RECIPIENTS_COLLECTION).document(doc_id).delete()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
#  網站清單 API
# ============================================================
@app.route("/api/websites", methods=["GET"])
def get_websites():
    """取得所有網站"""
    try:
        docs = db.collection(WEBSITES_COLLECTION).order_by("created_at", direction=firestore.Query.DESCENDING).stream()
        websites = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            if data.get("created_at"):
                data["created_at"] = data["created_at"].strftime("%Y-%m-%d %H:%M")
            websites.append(data)
        return jsonify({"success": True, "data": websites})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/websites", methods=["POST"])
def add_website():
    """新增網站"""
    try:
        body = request.get_json()
        url = body.get("url", "").strip()
        name = body.get("name", "").strip()
        depth = body.get("depth", "")
        save_html = body.get("save_html", "")
        pagination = body.get("pagination", "")

        if not url:
            return jsonify({"success": False, "error": "URL 不可為空"}), 400

        if not url.startswith(("http://", "https://")):
            return jsonify({"success": False, "error": "URL 必須以 http:// 或 https:// 開頭"}), 400

        # 檢查是否已存在
        existing = db.collection(WEBSITES_COLLECTION).where("url", "==", url).limit(1).stream()
        if any(True for _ in existing):
            return jsonify({"success": False, "error": f"'{url}' 已存在於網站清單中"}), 409

        doc_data = {
            "url": url,
            "name": name,
            "depth": str(depth) if depth else "",
            "save_html": str(save_html) if save_html else "",
            "pagination": str(pagination) if pagination else "",
            "created_at": firestore.SERVER_TIMESTAMP,
        }

        doc_ref = db.collection(WEBSITES_COLLECTION).add(doc_data)
        return jsonify({"success": True, "id": doc_ref[1].id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/websites/<doc_id>", methods=["PUT"])
def update_website(doc_id):
    """更新網站設定"""
    try:
        body = request.get_json()
        update_data = {}
        for field in ["url", "name", "depth", "save_html", "pagination"]:
            if field in body:
                update_data[field] = body[field]

        if not update_data:
            return jsonify({"success": False, "error": "沒有要更新的欄位"}), 400

        db.collection(WEBSITES_COLLECTION).document(doc_id).update(update_data)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/websites/<doc_id>", methods=["DELETE"])
def delete_website(doc_id):
    """刪除網站"""
    try:
        db.collection(WEBSITES_COLLECTION).document(doc_id).delete()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/websites/import-csv", methods=["POST"])
def import_websites_csv():
    """從 CSV 匯入網站清單"""
    try:
        if "file" not in request.files:
            return jsonify({"success": False, "error": "未上傳檔案"}), 400

        file = request.files["file"]
        if not file.filename.endswith(".csv"):
            return jsonify({"success": False, "error": "只接受 CSV 檔案"}), 400

        content = file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))

        batch = db.batch()
        count = 0
        skipped = 0

        for row in reader:
            url = row.get("URL", "").strip()
            if not url:
                continue

            # 檢查是否已存在
            existing = db.collection(WEBSITES_COLLECTION).where("url", "==", url).limit(1).stream()
            if any(True for _ in existing):
                skipped += 1
                continue

            doc_ref = db.collection(WEBSITES_COLLECTION).document()
            batch.set(doc_ref, {
                "url": url,
                "name": row.get("name", ""),
                "depth": row.get("depth", ""),
                "save_html": row.get("save_html", ""),
                "pagination": row.get("pagination", ""),
                "created_at": firestore.SERVER_TIMESTAMP,
            })
            count += 1

            # Firestore batch 限制 500 筆
            if count % 450 == 0:
                batch.commit()
                batch = db.batch()

        if count % 450 != 0:
            batch.commit()

        return jsonify({
            "success": True,
            "imported": count,
            "skipped": skipped,
            "message": f"成功匯入 {count} 個網站，跳過 {skipped} 個重複網站"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/websites/export-csv", methods=["GET"])
def export_websites_csv():
    """匯出網站清單為 CSV"""
    try:
        docs = db.collection(WEBSITES_COLLECTION).order_by("created_at").stream()

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["URL", "name", "depth", "save_html", "pagination"])
        writer.writeheader()

        for doc in docs:
            data = doc.to_dict()
            writer.writerow({
                "URL": data.get("url", ""),
                "name": data.get("name", ""),
                "depth": data.get("depth", ""),
                "save_html": data.get("save_html", ""),
                "pagination": data.get("pagination", ""),
            })

        from flask import Response
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=websites_{datetime.now().strftime('%Y%m%d')}.csv"}
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
#  爬蟲 VM 控制 API
# ============================================================

def _get_vm_client():
    """取得 Compute Engine client"""
    if not HAS_COMPUTE:
        raise RuntimeError("google-cloud-compute 未安裝，無法控制 VM")
    return compute_v1.InstancesClient()


@app.route("/api/vm/status", methods=["GET"])
def get_vm_status():
    """取得爬蟲 VM 狀態"""
    vm_name = request.args.get("vm", DEFAULT_VM_NAME)
    zone = request.args.get("zone", DEFAULT_ZONE)
    project = request.args.get("project", GCP_PROJECT)

    if not project:
        return jsonify({"success": False, "error": "未設定 GOOGLE_CLOUD_PROJECT"}), 400

    try:
        client = _get_vm_client()
        instance = client.get(project=project, zone=zone, instance=vm_name)
        return jsonify({
            "success": True,
            "data": {
                "name": instance.name,
                "status": instance.status,  # RUNNING, TERMINATED, STOPPED, etc.
                "machine_type": instance.machine_type.split("/")[-1],
                "zone": zone,
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vm/start", methods=["POST"])
def start_vm():
    """啟動爬蟲 VM（觸發爬蟲執行）"""
    body = request.get_json() or {}
    vm_name = body.get("vm", DEFAULT_VM_NAME)
    zone = body.get("zone", DEFAULT_ZONE)
    project = body.get("project", GCP_PROJECT)

    if not project:
        return jsonify({"success": False, "error": "未設定 GOOGLE_CLOUD_PROJECT"}), 400

    try:
        client = _get_vm_client()

        # 先檢查狀態
        instance = client.get(project=project, zone=zone, instance=vm_name)
        if instance.status == "RUNNING":
            return jsonify({
                "success": False,
                "error": f"VM '{vm_name}' 已在執行中，請等待目前的爬蟲任務完成"
            }), 409

        # 啟動 VM
        operation = client.start(project=project, zone=zone, instance=vm_name)
        # 等待操作完成（最多 60 秒）
        operation.result(timeout=60)

        # 記錄啟動事件到 Firestore
        db.collection("crawler_events").add({
            "event": "vm_started",
            "vm_name": vm_name,
            "triggered_by": "web_ui",
            "timestamp": firestore.SERVER_TIMESTAMP,
        })

        return jsonify({
            "success": True,
            "message": f"VM '{vm_name}' 已啟動，爬蟲將自動開始執行"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vm/stop", methods=["POST"])
def stop_vm():
    """停止爬蟲 VM"""
    body = request.get_json() or {}
    vm_name = body.get("vm", DEFAULT_VM_NAME)
    zone = body.get("zone", DEFAULT_ZONE)
    project = body.get("project", GCP_PROJECT)

    if not project:
        return jsonify({"success": False, "error": "未設定 GOOGLE_CLOUD_PROJECT"}), 400

    try:
        client = _get_vm_client()
        instance = client.get(project=project, zone=zone, instance=vm_name)

        if instance.status != "RUNNING":
            return jsonify({
                "success": False,
                "error": f"VM '{vm_name}' 目前不在執行中 (狀態: {instance.status})"
            }), 409

        operation = client.stop(project=project, zone=zone, instance=vm_name)
        operation.result(timeout=60)

        db.collection("crawler_events").add({
            "event": "vm_stopped",
            "vm_name": vm_name,
            "triggered_by": "web_ui",
            "timestamp": firestore.SERVER_TIMESTAMP,
        })

        return jsonify({
            "success": True,
            "message": f"VM '{vm_name}' 已停止"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/vm/events", methods=["GET"])
def get_vm_events():
    """取得最近的 VM 事件紀錄"""
    try:
        docs = db.collection("crawler_events").order_by(
            "timestamp", direction=firestore.Query.DESCENDING
        ).limit(20).stream()

        events = []
        for doc in docs:
            data = doc.to_dict()
            if data.get("timestamp"):
                data["timestamp"] = data["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            events.append(data)
        return jsonify({"success": True, "data": events})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
#  歷史報告 API (GCS)
# ============================================================

@app.route("/api/reports", methods=["GET"])
def get_reports():
    """列出所有月份的報告"""
    if not HAS_GCS:
        return jsonify({"success": False, "error": "google-cloud-storage 未安裝"}), 500

    try:
        client = gcs_storage.Client()
        bucket = client.bucket(GCS_BUCKET)

        # 列出所有 Excel 報告（頂層目錄按年月分）
        months = set()
        reports = []

        blobs = bucket.list_blobs()
        for blob in blobs:
            parts = blob.name.split("/")
            if len(parts) >= 2:
                month = parts[0]  # e.g. "2026-03"
                months.add(month)

                # 只列出 Excel 和頂層 JSON/CSV
                filename = parts[-1]
                if filename.endswith((".xlsx", ".json", ".csv", ".txt")):
                    reports.append({
                        "month": month,
                        "path": blob.name,
                        "filename": filename,
                        "size_mb": round(blob.size / 1024 / 1024, 2) if blob.size else 0,
                        "updated": blob.updated.strftime("%Y-%m-%d %H:%M") if blob.updated else "",
                        "is_excel": filename.endswith(".xlsx"),
                        "subfolder": "/".join(parts[1:-1]) if len(parts) > 2 else "",
                    })

        # 按月份倒序排列
        sorted_months = sorted(months, reverse=True)

        return jsonify({
            "success": True,
            "months": sorted_months,
            "reports": reports,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/reports/download/<path:blob_path>", methods=["GET"])
def download_report(blob_path):
    """下載指定的報告檔案"""
    if not HAS_GCS:
        return jsonify({"success": False, "error": "google-cloud-storage 未安裝"}), 500

    try:
        client = gcs_storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(blob_path)

        if not blob.exists():
            return jsonify({"success": False, "error": "檔案不存在"}), 404

        content = blob.download_as_bytes()
        filename = blob_path.split("/")[-1]

        # 判斷 MIME type
        if filename.endswith(".xlsx"):
            mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif filename.endswith(".json"):
            mimetype = "application/json"
        elif filename.endswith(".csv"):
            mimetype = "text/csv"
        else:
            mimetype = "application/octet-stream"

        from flask import Response
        return Response(
            content,
            mimetype=mimetype,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
