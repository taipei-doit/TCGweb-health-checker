#!/bin/bash
# ============================================================
# 部署 Email & 網站管理平台到 Cloud Run
# 用法: ./deploy.sh <gcp-project-id>
# ============================================================

set -e

PROJECT_ID="${1:?用法: ./deploy.sh <gcp-project-id>}"
REGION="asia-east1"
SERVICE_NAME="crawler-manager"

echo "=========================================="
echo "部署管理平台到 Cloud Run"
echo "  專案: $PROJECT_ID"
echo "  區域: $REGION"
echo "  服務: $SERVICE_NAME"
echo "=========================================="

# 確保 API 啟用
echo "啟用必要的 API..."
gcloud services enable run.googleapis.com --project=$PROJECT_ID
gcloud services enable firestore.googleapis.com --project=$PROJECT_ID
gcloud services enable cloudbuild.googleapis.com --project=$PROJECT_ID

# 建立 Firestore 資料庫（如果尚未建立）
echo "檢查 Firestore..."
gcloud firestore databases describe --project=$PROJECT_ID 2>/dev/null || \
    gcloud firestore databases create --project=$PROJECT_ID --location=$REGION

# 取得 Cloud Run 預設 service account
COMPUTE_SA="$(gcloud iam service-accounts list --project=$PROJECT_ID --filter='displayName:Compute Engine default' --format='value(email)' 2>/dev/null)"
CR_SA="$(gcloud iam service-accounts list --project=$PROJECT_ID --filter='displayName:Default compute' --format='value(email)' 2>/dev/null)"
SA_TO_USE="${CR_SA:-$COMPUTE_SA}"

# 授予 Compute Engine 啟動/停止 VM 權限
if [ -n "$SA_TO_USE" ]; then
    echo "授予 Cloud Run service account Compute 權限..."
    gcloud projects add-iam-policy-binding $PROJECT_ID \
        --member="serviceAccount:${SA_TO_USE}" \
        --role="roles/compute.instanceAdmin.v1" \
        --condition=None --quiet 2>/dev/null || true
fi

# 設定 VM 名稱（可自訂）
VM_NAME="${2:-crawler-webcheck}"
VM_ZONE="asia-east1-c"

# 部署到 Cloud Run
echo "開始部署..."
gcloud run deploy $SERVICE_NAME \
    --source . \
    --region $REGION \
    --project $PROJECT_ID \
    --allow-unauthenticated \
    --memory 512Mi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 2 \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT_ID,CRAWLER_VM_NAME=$VM_NAME,CRAWLER_VM_ZONE=$VM_ZONE"

# 取得服務 URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
    --region $REGION \
    --project $PROJECT_ID \
    --format='value(status.url)')

echo ""
echo "=========================================="
echo "部署完成！"
echo "管理平台網址: $SERVICE_URL"
echo "=========================================="
