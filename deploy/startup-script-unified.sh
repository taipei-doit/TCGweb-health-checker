#!/bin/bash

# =============================================================================
# VM 啟動腳本 (統一版本) - 用於 GCE metadata startup-script
# =============================================================================
# 這個腳本會在 VM 啟動時自動執行：
#   1. 環境安裝（首次啟動時）
#   2. 更新程式碼（git pull 取得最新版本）
#   3. 啟動統一版本的爬蟲程式 (gcp_main_unified.py)
# =============================================================================

set -e

# --- 設定變數 ---
PROJECT_USER="hyc9977"
PROJECT_DIR="/home/${PROJECT_USER}/TCGweb-health-checker"
REPO_URL="https://github.com/jchilling/TCGweb-health-checker"
LOG_FILE="/home/${PROJECT_USER}/vm_startup.log"
CRAWLER_LOG="/home/${PROJECT_USER}/crawler_execution.log"
PYTHON_CMD="python3 -u"
PYTHON_SCRIPT="gcp_main_unified.py"
VM_NAME="crawler-webcheck"

# --- 爬蟲參數 ---
CRAWLER_ARGS="--depth 3 --concurrent 13 --no-save-html --no-pagination --mode pool --vm-name ${VM_NAME}"

# =============================================================================
# 記錄 VM 啟動
# =============================================================================
echo "========================================" > "${LOG_FILE}"
echo "VM 啟動時間: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "${LOG_FILE}"
echo "主機名稱: $(hostname)" >> "${LOG_FILE}"
echo "========================================" >> "${LOG_FILE}"

# =============================================================================
# 步驟 1：安裝基本套件 (git)
# =============================================================================
echo "[$(date '+%H:%M:%S')] 安裝 git..." >> "${LOG_FILE}"
apt-get update -y >> "${LOG_FILE}" 2>&1
apt-get install -y git >> "${LOG_FILE}" 2>&1

# =============================================================================
# 步驟 2：環境安裝（僅首次啟動時執行）
# =============================================================================
if [ ! -f "/home/${PROJECT_USER}/.crawler_env_installed" ]; then
    echo "[$(date '+%H:%M:%S')] 首次啟動，執行完整環境安裝..." >> "${LOG_FILE}"

    # --- 建立使用者（如果不存在）---
    if ! id "${PROJECT_USER}" >/dev/null 2>&1; then
        echo "[$(date '+%H:%M:%S')] 建立使用者 ${PROJECT_USER}..." >> "${LOG_FILE}"
        useradd -m -s /bin/bash "${PROJECT_USER}"
    fi

    # --- 複製專案程式碼 ---
    if [ ! -d "${PROJECT_DIR}" ]; then
        echo "[$(date '+%H:%M:%S')] 從 GitHub 複製專案程式碼..." >> "${LOG_FILE}"
        sudo -u "${PROJECT_USER}" git clone "${REPO_URL}" "${PROJECT_DIR}" >> "${LOG_FILE}" 2>&1
    fi

    # --- 執行環境安裝腳本 ---
    cd "${PROJECT_DIR}"
    if [ -f "setup-environment.sh" ]; then
        echo "[$(date '+%H:%M:%S')] 執行環境安裝腳本 (setup-environment.sh)..." >> "${LOG_FILE}"
        chmod +x setup-environment.sh
        ./setup-environment.sh >> "${LOG_FILE}" 2>&1
    else
        # 手動安裝必要套件
        echo "[$(date '+%H:%M:%S')] 手動安裝 Python 依賴..." >> "${LOG_FILE}"
        apt-get install -y python3-pip curl >> "${LOG_FILE}" 2>&1

        # 安裝 Playwright 系統依賴
        apt-get install -y \
            libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 \
            libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
            libxrandr2 libgbm1 libcairo2 libpango-1.0-0 libasound2 \
            libnss3 libnspr4 libdrm2 libxss1 libxshmfence1 \
            libxkbcommon-x11-0 fonts-liberation xdg-utils >> "${LOG_FILE}" 2>&1

        # 安裝 Python 套件
        sudo -u "${PROJECT_USER}" pip3 install -r requirements.txt >> "${LOG_FILE}" 2>&1
        sudo -u "${PROJECT_USER}" python3 -m playwright install chromium >> "${LOG_FILE}" 2>&1
    fi

    # --- 設定權限 ---
    chown -R "${PROJECT_USER}:${PROJECT_USER}" "${PROJECT_DIR}"

    # --- 標記環境已安裝 ---
    touch "/home/${PROJECT_USER}/.crawler_env_installed"
    echo "[$(date '+%H:%M:%S')] 環境安裝完成" >> "${LOG_FILE}"
else
    echo "[$(date '+%H:%M:%S')] 環境已安裝，跳過安裝步驟" >> "${LOG_FILE}"
fi

# =============================================================================
# 步驟 3：更新程式碼（每次啟動都執行 git pull）
# =============================================================================
echo "[$(date '+%H:%M:%S')] 更新程式碼至最新版本..." >> "${LOG_FILE}"
cd "${PROJECT_DIR}"
sudo -u "${PROJECT_USER}" git reset --hard HEAD >> "${LOG_FILE}" 2>&1
sudo -u "${PROJECT_USER}" git pull >> "${LOG_FILE}" 2>&1
echo "[$(date '+%H:%M:%S')] 程式碼更新完成" >> "${LOG_FILE}"

# =============================================================================
# 步驟 4：檢查必要檔案
# =============================================================================
if [ ! -f "${PROJECT_DIR}/${PYTHON_SCRIPT}" ]; then
    echo "[$(date '+%H:%M:%S')] 錯誤：找不到 ${PYTHON_SCRIPT}" >> "${LOG_FILE}"
    echo "[$(date '+%H:%M:%S')] 可用的 gcp_main 檔案：" >> "${LOG_FILE}"
    ls -la "${PROJECT_DIR}"/gcp_main*.py >> "${LOG_FILE}" 2>&1 || true
    exit 1
fi

if [ ! -f "${PROJECT_DIR}/config/websites.csv" ]; then
    echo "[$(date '+%H:%M:%S')] 錯誤：找不到 config/websites.csv 設定檔" >> "${LOG_FILE}"
    exit 1
fi

# =============================================================================
# 步驟 5：檢查是否已有爬蟲在執行
# =============================================================================
if pgrep -u "${PROJECT_USER}" -f "${PYTHON_SCRIPT}" > /dev/null; then
    echo "[$(date '+%H:%M:%S')] 爬蟲程式已在執行中，跳過啟動" >> "${LOG_FILE}"
    echo "[$(date '+%H:%M:%S')] VM 啟動腳本結束 (未啟動新程序)" >> "${LOG_FILE}"
    exit 0
fi

# =============================================================================
# 步驟 6：啟動爬蟲程式
# =============================================================================
echo "[$(date '+%H:%M:%S')] 啟動統一版本爬蟲程式..." >> "${LOG_FILE}"
echo "[$(date '+%H:%M:%S')] 執行指令: ${PYTHON_CMD} ${PYTHON_SCRIPT} ${CRAWLER_ARGS}" >> "${LOG_FILE}"

# 在背景執行爬蟲（程式內建自動關機功能）
nohup sudo -u "${PROJECT_USER}" \
    ${PYTHON_CMD} ${PYTHON_SCRIPT} ${CRAWLER_ARGS} \
    > "${CRAWLER_LOG}" 2>&1 &

CRAWLER_PID=$!
echo "[$(date '+%H:%M:%S')] 爬蟲程式已在背景啟動，PID: ${CRAWLER_PID}" >> "${LOG_FILE}"
echo "[$(date '+%H:%M:%S')] 執行日誌: ${CRAWLER_LOG}" >> "${LOG_FILE}"
echo "[$(date '+%H:%M:%S')] VM 啟動腳本執行完成" >> "${LOG_FILE}"
echo "========================================" >> "${LOG_FILE}"
