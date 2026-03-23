#!/bin/bash

# =============================================================================
# VM 啟動腳本 (統一版本) - 用於 GCE metadata startup-script
# =============================================================================
# 這個腳本會在 VM 啟動時自動執行：
#   1. 環境安裝（僅首次）
#   2. 從 Secret Manager 載入 .env
#   3. 啟動爬蟲程式（程式內部會從 Firestore 同步網站清單和收件人）
# =============================================================================

set -e

# --- 設定變數 ---
PROJECT_USER="crawler"
PROJECT_DIR="/home/${PROJECT_USER}/TCGweb-health-checker"
REPO_URL="https://github.com/tpe-doit/TCGweb-health-checker"
LOG_FILE="/home/${PROJECT_USER}/vm_startup.log"
CRAWLER_LOG="/home/${PROJECT_USER}/crawler_execution.log"
PYTHON_CMD="python3 -u"
PYTHON_SCRIPT="gcp_main_unified.py"
VM_NAME="crawler-webcheck"
GCP_PROJECT="doit-dic-itteam"
SECRET_NAME="crawler-env"

# --- 爬蟲參數 ---
CRAWLER_ARGS="--depth 3 --concurrent 13 --no-save-html --no-pagination --mode pool --vm-name ${VM_NAME}"

# =============================================================================
# 記錄 VM 啟動
# =============================================================================
mkdir -p "/home/${PROJECT_USER}"
echo "========================================" > "${LOG_FILE}"
echo "VM 啟動時間: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "${LOG_FILE}"
echo "主機名稱: $(hostname)" >> "${LOG_FILE}"
echo "========================================" >> "${LOG_FILE}"

# =============================================================================
# 步驟 1：環境安裝（僅首次啟動時執行）
# =============================================================================
if [ ! -f "/home/${PROJECT_USER}/.crawler_env_installed" ]; then
    echo "[$(date '+%H:%M:%S')] 首次啟動，執行完整環境安裝..." >> "${LOG_FILE}"

    # --- 安裝系統套件 ---
    apt-get update -y >> "${LOG_FILE}" 2>&1
    apt-get install -y python3-pip git curl >> "${LOG_FILE}" 2>&1

    # --- 建立使用者（如果不存在）---
    if ! id "${PROJECT_USER}" >/dev/null 2>&1; then
        echo "[$(date '+%H:%M:%S')] 建立使用者 ${PROJECT_USER}..." >> "${LOG_FILE}"
        useradd -m -s /bin/bash "${PROJECT_USER}"
    fi

    # --- 複製專案程式碼（僅首次）---
    if [ ! -d "${PROJECT_DIR}" ]; then
        echo "[$(date '+%H:%M:%S')] 從 GitHub 複製專案程式碼..." >> "${LOG_FILE}"
        sudo -u "${PROJECT_USER}" git clone "${REPO_URL}" "${PROJECT_DIR}" >> "${LOG_FILE}" 2>&1
    fi

    # --- 安裝 Python 依賴 ---
    cd "${PROJECT_DIR}"
    echo "[$(date '+%H:%M:%S')] 安裝 Python 依賴..." >> "${LOG_FILE}"
    pip3 install -r requirements.txt google-cloud-firestore google-cloud-storage >> "${LOG_FILE}" 2>&1

    # --- 安裝 Playwright ---
    echo "[$(date '+%H:%M:%S')] 安裝 Playwright Chromium..." >> "${LOG_FILE}"

    # 安裝 Playwright 系統依賴
    apt-get install -y \
        libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
        libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
        libxrandr2 libgbm1 libcairo2 libpango-1.0-0 libasound2 \
        libnss3 libnspr4 libdrm2 libxss1 libxshmfence1 \
        libxkbcommon-x11-0 fonts-liberation xdg-utils >> "${LOG_FILE}" 2>&1

    python3 -m playwright install chromium >> "${LOG_FILE}" 2>&1

    # --- 設定權限 ---
    chown -R "${PROJECT_USER}:${PROJECT_USER}" "${PROJECT_DIR}"
    chown -R "${PROJECT_USER}:${PROJECT_USER}" "/home/${PROJECT_USER}"

    # --- 標記環境已安裝 ---
    touch "/home/${PROJECT_USER}/.crawler_env_installed"
    echo "[$(date '+%H:%M:%S')] 環境安裝完成" >> "${LOG_FILE}"
else
    echo "[$(date '+%H:%M:%S')] 環境已安裝，跳過安裝步驟" >> "${LOG_FILE}"
fi

# =============================================================================
# 步驟 2：檢查 Git 更新
# =============================================================================
echo "[$(date '+%H:%M:%S')] 檢查 Git 是否有新版本..." >> "${LOG_FILE}"
cd "${PROJECT_DIR}"
sudo -u "${PROJECT_USER}" git fetch origin main >> "${LOG_FILE}" 2>&1 || true
LOCAL=$(sudo -u "${PROJECT_USER}" git rev-parse HEAD 2>/dev/null)
REMOTE=$(sudo -u "${PROJECT_USER}" git rev-parse origin/main 2>/dev/null)
if [ "$LOCAL" != "$REMOTE" ]; then
    echo "[$(date '+%H:%M:%S')] 發現新版本，更新中..." >> "${LOG_FILE}"
    sudo -u "${PROJECT_USER}" git reset --hard origin/main >> "${LOG_FILE}" 2>&1
    echo "[$(date '+%H:%M:%S')] 已更新至: $(sudo -u ${PROJECT_USER} git log --oneline -1)" >> "${LOG_FILE}"
else
    echo "[$(date '+%H:%M:%S')] 已是最新版本" >> "${LOG_FILE}"
fi

# =============================================================================
# 步驟 3：從 Secret Manager 載入 .env
# =============================================================================
echo "[$(date '+%H:%M:%S')] 載入 .env（Secret Manager）..." >> "${LOG_FILE}"
ENV_FILE="${PROJECT_DIR}/.env"

# 使用 metadata token + REST API（避免 gcloud CLI token cache 問題）
SECRET_URL="https://secretmanager.googleapis.com/v1/projects/${GCP_PROJECT}/secrets/${SECRET_NAME}/versions/latest:access"
TOKEN=$(curl -s -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])" 2>/dev/null)

if [ -n "$TOKEN" ]; then
    SECRET_DATA=$(curl -s -H "Authorization: Bearer ${TOKEN}" "${SECRET_URL}" \
        | python3 -c "import sys,json,base64;d=json.load(sys.stdin);print(base64.b64decode(d['payload']['data']).decode())" 2>/dev/null)

    if [ -n "$SECRET_DATA" ]; then
        echo "$SECRET_DATA" > "${ENV_FILE}"
        chown "${PROJECT_USER}:${PROJECT_USER}" "${ENV_FILE}"
        chmod 600 "${ENV_FILE}"
        echo "[$(date '+%H:%M:%S')] .env 載入成功（Secret Manager）" >> "${LOG_FILE}"
    else
        echo "[$(date '+%H:%M:%S')] Secret Manager 回應為空" >> "${LOG_FILE}"
    fi
else
    echo "[$(date '+%H:%M:%S')] 無法取得 metadata token" >> "${LOG_FILE}"
fi

# Fallback: 如果 Secret Manager 失敗，檢查本地 .env
if [ ! -f "${ENV_FILE}" ]; then
    echo "[$(date '+%H:%M:%S')] 警告：無 .env 檔案，Email 功能可能無法使用" >> "${LOG_FILE}"
fi

# =============================================================================
# 步驟 3：檢查必要檔案
# =============================================================================
cd "${PROJECT_DIR}"

if [ ! -f "${PYTHON_SCRIPT}" ]; then
    echo "[$(date '+%H:%M:%S')] 錯誤：找不到 ${PYTHON_SCRIPT}" >> "${LOG_FILE}"
    ls -la gcp_main*.py >> "${LOG_FILE}" 2>&1 || true
    exit 1
fi

# =============================================================================
# 步驟 4：檢查是否已有爬蟲在執行
# =============================================================================
if pgrep -f "${PYTHON_SCRIPT}" > /dev/null; then
    echo "[$(date '+%H:%M:%S')] 爬蟲程式已在執行中，跳過啟動" >> "${LOG_FILE}"
    exit 0
fi

# =============================================================================
# 步驟 5：啟動爬蟲程式
# =============================================================================
echo "[$(date '+%H:%M:%S')] 啟動爬蟲程式..." >> "${LOG_FILE}"
echo "[$(date '+%H:%M:%S')] 指令: ${PYTHON_CMD} ${PYTHON_SCRIPT} ${CRAWLER_ARGS}" >> "${LOG_FILE}"

nohup sudo -u "${PROJECT_USER}" \
    ${PYTHON_CMD} ${PYTHON_SCRIPT} ${CRAWLER_ARGS} \
    > "${CRAWLER_LOG}" 2>&1 &

CRAWLER_PID=$!
echo "[$(date '+%H:%M:%S')] 爬蟲已啟動，PID: ${CRAWLER_PID}" >> "${LOG_FILE}"
echo "[$(date '+%H:%M:%S')] 執行日誌: ${CRAWLER_LOG}" >> "${LOG_FILE}"
echo "========================================" >> "${LOG_FILE}"
