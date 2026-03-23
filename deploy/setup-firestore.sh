#!/bin/bash

# =============================================================================
# Firestore 初始化腳本 - TCGweb 網站健康檢查系統
# =============================================================================
# 用途：啟用 Firestore API 並建立初始設定資料
# 使用方式：./setup-firestore.sh <project-id>
# 範例：./setup-firestore.sh my-gcp-project
# =============================================================================

set -euo pipefail

# --- 參數設定 ---
PROJECT_ID="${1:?錯誤：請提供 GCP 專案 ID。使用方式: ./setup-firestore.sh <project-id>}"
REGION="asia-east1"
DATABASE_MODE="firestore-native"

# --- 檢查 gcloud CLI ---
if ! command -v gcloud &> /dev/null; then
    echo "錯誤：找不到 gcloud CLI，請先安裝 Google Cloud SDK"
    exit 1
fi

# --- 顯示設定資訊 ---
echo "============================================"
echo "  Firestore 初始化 - 網站健康檢查系統"
echo "============================================"
echo "專案 ID:      ${PROJECT_ID}"
echo "區域:         ${REGION}"
echo "資料庫模式:   ${DATABASE_MODE}"
echo "============================================"
echo ""

# =============================================================================
# 步驟 1：啟用 Firestore API
# =============================================================================
echo "[步驟 1/4] 啟用 Firestore API..."
gcloud services enable firestore.googleapis.com \
    --project="${PROJECT_ID}"
echo "  Firestore API 已啟用"

# =============================================================================
# 步驟 2：建立 Firestore 資料庫（如果尚未建立）
# =============================================================================
echo "[步驟 2/4] 檢查 Firestore 資料庫..."

# 嘗試建立 Firestore 原生模式資料庫
# 注意：每個專案只能建立一次，如果已存在會顯示錯誤但不影響後續步驟
gcloud firestore databases create \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --type="${DATABASE_MODE}" 2>/dev/null && \
    echo "  Firestore 資料庫建立完成" || \
    echo "  Firestore 資料庫已存在，跳過建立"

# =============================================================================
# 步驟 3：建立初始 collection 和預設收件人（使用 REST API）
# =============================================================================
echo "[步驟 3/4] 建立初始資料集合..."

# 取得存取權杖
ACCESS_TOKEN=$(gcloud auth print-access-token --project="${PROJECT_ID}")

# --- 建立 email_recipients collection 的預設文件 ---
echo "  建立 email_recipients 集合..."
RECIPIENT_DOC_ID="default_recipient"
RECIPIENT_PAYLOAD='{
  "fields": {
    "email": { "stringValue": "admin@example.com" },
    "name": { "stringValue": "系統管理員" },
    "enabled": { "booleanValue": true },
    "created_at": { "timestampValue": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'" },
    "note": { "stringValue": "預設收件人 - 請修改為實際的收件人信箱" }
  }
}'

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PATCH \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "${RECIPIENT_PAYLOAD}" \
    "https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/documents/email_recipients/${RECIPIENT_DOC_ID}")

if [ "${HTTP_STATUS}" -eq 200 ]; then
    echo "  email_recipients 集合建立完成（預設收件人：admin@example.com）"
else
    echo "  警告：email_recipients 建立失敗 (HTTP ${HTTP_STATUS})，請手動建立"
fi

# --- 建立 crawler_config collection 的預設文件 ---
echo "  建立 crawler_config 集合..."
CONFIG_DOC_ID="default_config"
CONFIG_PAYLOAD='{
  "fields": {
    "depth": { "integerValue": "3" },
    "concurrent": { "integerValue": "13" },
    "save_html": { "booleanValue": false },
    "pagination": { "booleanValue": false },
    "mode": { "stringValue": "pool" },
    "vm_name": { "stringValue": "crawler-webcheck" },
    "auto_shutdown": { "booleanValue": true },
    "created_at": { "timestampValue": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'" },
    "note": { "stringValue": "爬蟲預設設定" }
  }
}'

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PATCH \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "${CONFIG_PAYLOAD}" \
    "https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/documents/crawler_config/${CONFIG_DOC_ID}")

if [ "${HTTP_STATUS}" -eq 200 ]; then
    echo "  crawler_config 集合建立完成"
else
    echo "  警告：crawler_config 建立失敗 (HTTP ${HTTP_STATUS})，請手動建立"
fi

# =============================================================================
# 步驟 4：建立 Firestore 複合索引
# =============================================================================
echo "[步驟 4/4] 建立 Firestore 索引..."

# 建立 crawl_results 集合的索引（依網站和時間排序）
# 注意：索引建立需要一些時間，可在 GCP Console 中確認狀態
INDEXES_PAYLOAD='{
  "fields": [
    { "fieldPath": "website_url", "order": "ASCENDING" },
    { "fieldPath": "crawl_time", "order": "DESCENDING" }
  ],
  "queryScope": "COLLECTION"
}'

INDEX_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "${INDEXES_PAYLOAD}" \
    "https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/collectionGroups/crawl_results/indexes")

if [ "${INDEX_RESPONSE}" -eq 200 ] || [ "${INDEX_RESPONSE}" -eq 409 ]; then
    echo "  crawl_results 索引已建立（或已存在）"
else
    echo "  注意：索引建立回應碼 ${INDEX_RESPONSE}，可能需要手動建立"
    echo "  可在 GCP Console > Firestore > 索引 中手動設定"
fi

# --- 完成 ---
echo ""
echo "============================================"
echo "  Firestore 初始化完成！"
echo "============================================"
echo ""
echo "已建立的集合："
echo "  1. email_recipients - 報告收件人清單"
echo "     預設收件人: admin@example.com（請修改為實際信箱）"
echo "  2. crawler_config   - 爬蟲設定參數"
echo ""
echo "重要事項："
echo "  - 請至 GCP Console 修改 email_recipients 中的收件人信箱"
echo "  - 索引建立可能需要數分鐘，可至 GCP Console > Firestore > 索引 確認"
echo "  - 確認 VM 的服務帳號有 Firestore 讀寫權限"
echo ""
echo "GCP Console Firestore 頁面："
echo "  https://console.cloud.google.com/firestore/databases?project=${PROJECT_ID}"
echo "============================================"
