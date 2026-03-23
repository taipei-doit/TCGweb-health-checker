#!/bin/bash

# =============================================================================
# Cloud Scheduler 設定腳本 - 每月自動啟動爬蟲 VM
# =============================================================================
# 用途：建立 Cloud Scheduler 定時任務，每月1號凌晨2點自動啟動爬蟲 VM
# 使用方式：./setup-scheduler.sh <project-id> [vm-name]
# 範例：./setup-scheduler.sh my-gcp-project crawler-webcheck
# =============================================================================

set -euo pipefail

# --- 參數設定 ---
PROJECT_ID="${1:?錯誤：請提供 GCP 專案 ID。使用方式: ./setup-scheduler.sh <project-id> [vm-name]}"
VM_NAME="${2:-crawler-webcheck}"
ZONE="asia-east1-c"
REGION="asia-east1"
JOB_NAME="monthly-crawler-trigger"
SCHEDULE="0 2 1 * *"
TIME_ZONE="Asia/Taipei"
SA_NAME="crawler-scheduler"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# --- 檢查 gcloud CLI ---
if ! command -v gcloud &> /dev/null; then
    echo "錯誤：找不到 gcloud CLI，請先安裝 Google Cloud SDK"
    exit 1
fi

# --- 顯示設定資訊 ---
echo "============================================"
echo "  設定 Cloud Scheduler - 每月爬蟲排程"
echo "============================================"
echo "專案 ID:      ${PROJECT_ID}"
echo "目標 VM:      ${VM_NAME}"
echo "區域:         ${ZONE}"
echo "排程:         ${SCHEDULE} (每月1號 02:00)"
echo "時區:         ${TIME_ZONE}"
echo "服務帳號:     ${SA_EMAIL}"
echo "============================================"
echo ""

# --- 1. 啟用必要的 API ---
echo "[步驟 1/5] 啟用必要的 GCP API..."
gcloud services enable cloudscheduler.googleapis.com \
    --project="${PROJECT_ID}" 2>/dev/null || true
gcloud services enable compute.googleapis.com \
    --project="${PROJECT_ID}" 2>/dev/null || true
echo "  API 已啟用"

# --- 2. 建立服務帳號 (如果不存在) ---
echo "[步驟 2/5] 建立 Scheduler 服務帳號..."
if gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" &> /dev/null; then
    echo "  服務帳號已存在，跳過建立"
else
    gcloud iam service-accounts create "${SA_NAME}" \
        --display-name="Crawler Scheduler SA" \
        --description="用於 Cloud Scheduler 啟動爬蟲 VM 的服務帳號" \
        --project="${PROJECT_ID}"
    echo "  服務帳號建立完成: ${SA_EMAIL}"
fi

# --- 3. 授予 Compute Engine 啟動權限 ---
echo "[步驟 3/5] 授予 compute.instances.start 權限..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/compute.instanceAdmin.v1" \
    --condition=None \
    --quiet
echo "  已授予 roles/compute.instanceAdmin.v1 權限"

# --- 4. 刪除舊的 Scheduler job (如果存在) ---
echo "[步驟 4/5] 檢查並建立 Cloud Scheduler 排程..."
if gcloud scheduler jobs describe "${JOB_NAME}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" &> /dev/null; then
    echo "  排程任務已存在，刪除舊任務..."
    gcloud scheduler jobs delete "${JOB_NAME}" \
        --project="${PROJECT_ID}" \
        --location="${REGION}" \
        --quiet
fi

# --- 5. 建立 Cloud Scheduler 排程任務 ---
echo "[步驟 5/5] 建立 Cloud Scheduler 排程任務..."
gcloud scheduler jobs create http "${JOB_NAME}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --schedule="${SCHEDULE}" \
    --time-zone="${TIME_ZONE}" \
    --uri="https://compute.googleapis.com/compute/v1/projects/${PROJECT_ID}/zones/${ZONE}/instances/${VM_NAME}/start" \
    --http-method=POST \
    --oauth-service-account-email="${SA_EMAIL}" \
    --description="每月1號凌晨2點啟動網站健康檢查爬蟲VM"

# --- 完成 ---
echo ""
echo "============================================"
echo "  Cloud Scheduler 設定完成！"
echo "============================================"
echo "排程名稱:   ${JOB_NAME}"
echo "排程時間:   每月1號 02:00 (${TIME_ZONE})"
echo "目標 VM:    ${VM_NAME} (${ZONE})"
echo "服務帳號:   ${SA_EMAIL}"
echo ""
echo "常用指令："
echo "  查看排程狀態："
echo "    gcloud scheduler jobs describe ${JOB_NAME} --location=${REGION} --project=${PROJECT_ID}"
echo ""
echo "  手動觸發排程 (測試用)："
echo "    gcloud scheduler jobs run ${JOB_NAME} --location=${REGION} --project=${PROJECT_ID}"
echo ""
echo "  暫停排程："
echo "    gcloud scheduler jobs pause ${JOB_NAME} --location=${REGION} --project=${PROJECT_ID}"
echo ""
echo "  恢復排程："
echo "    gcloud scheduler jobs resume ${JOB_NAME} --location=${REGION} --project=${PROJECT_ID}"
echo "============================================"
