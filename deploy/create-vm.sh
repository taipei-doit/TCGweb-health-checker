#!/bin/bash

# =============================================================================
# 建立 GCE VM 腳本 - TCGweb 網站健康檢查系統
# =============================================================================
# 用途：在 GCP 上建立用於網站健康檢查的 VM 執行個體
# 使用方式：./create-vm.sh [vm-name] <project-id>
# 範例：./create-vm.sh crawler-webcheck my-gcp-project
# =============================================================================

set -euo pipefail

# --- 參數設定 ---
VM_NAME="${1:-crawler-webcheck}"
PROJECT_ID="${2:?錯誤：請提供 GCP 專案 ID。使用方式: ./create-vm.sh [vm-name] <project-id>}"
ZONE="asia-east1-c"
MACHINE_TYPE="n1-standard-16"
BOOT_DISK_SIZE="100GB"
BOOT_DISK_TYPE="pd-ssd"
IMAGE_FAMILY="ubuntu-2204-lts"
IMAGE_PROJECT="ubuntu-os-cloud"
STARTUP_SCRIPT="startup-script-unified.sh"

# --- 取得腳本所在目錄 ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- 檢查 startup script 是否存在 ---
if [ ! -f "${SCRIPT_DIR}/${STARTUP_SCRIPT}" ]; then
    echo "錯誤：找不到啟動腳本 ${SCRIPT_DIR}/${STARTUP_SCRIPT}"
    echo "請確認 ${STARTUP_SCRIPT} 檔案存在於 deploy/ 目錄中"
    exit 1
fi

# --- 檢查 gcloud CLI 是否已安裝 ---
if ! command -v gcloud &> /dev/null; then
    echo "錯誤：找不到 gcloud CLI，請先安裝 Google Cloud SDK"
    echo "安裝指南：https://cloud.google.com/sdk/docs/install"
    exit 1
fi

# --- 顯示建立資訊 ---
echo "============================================"
echo "  建立 GCE VM - 網站健康檢查系統"
echo "============================================"
echo "VM 名稱:      ${VM_NAME}"
echo "專案 ID:      ${PROJECT_ID}"
echo "區域:         ${ZONE}"
echo "機器類型:     ${MACHINE_TYPE} (16 vCPU, 60GB RAM)"
echo "開機磁碟:     ${BOOT_DISK_SIZE} SSD (${IMAGE_FAMILY})"
echo "啟動腳本:     ${STARTUP_SCRIPT}"
echo "============================================"
echo ""

# --- 確認操作 ---
read -p "確定要建立 VM 嗎？(y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消建立 VM"
    exit 0
fi

# --- 建立 VM ---
echo "正在建立 VM ${VM_NAME}..."

gcloud compute instances create "${VM_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --machine-type="${MACHINE_TYPE}" \
    --boot-disk-size="${BOOT_DISK_SIZE}" \
    --boot-disk-type="${BOOT_DISK_TYPE}" \
    --image-family="${IMAGE_FAMILY}" \
    --image-project="${IMAGE_PROJECT}" \
    --scopes=compute-rw,datastore,logging-write \
    --metadata-from-file=startup-script="${SCRIPT_DIR}/${STARTUP_SCRIPT}" \
    --labels=purpose=web-health-check \
    --tags=crawler

# --- 檢查建立結果 ---
if [ $? -eq 0 ]; then
    echo ""
    echo "============================================"
    echo "  VM 建立成功！"
    echo "============================================"
    echo "VM 名稱:   ${VM_NAME}"
    echo "區域:      ${ZONE}"
    echo ""
    echo "查看 VM 狀態："
    echo "  gcloud compute instances describe ${VM_NAME} --zone=${ZONE} --project=${PROJECT_ID}"
    echo ""
    echo "SSH 連線："
    echo "  gcloud compute ssh ${VM_NAME} --zone=${ZONE} --project=${PROJECT_ID}"
    echo ""
    echo "查看啟動日誌："
    echo "  gcloud compute ssh ${VM_NAME} --zone=${ZONE} --project=${PROJECT_ID} --command='tail -f /home/hyc9977/vm_startup.log'"
    echo "============================================"
else
    echo "錯誤：VM 建立失敗，請檢查錯誤訊息"
    exit 1
fi
