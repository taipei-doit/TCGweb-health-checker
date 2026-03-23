#!/bin/bash

# --- VM 啟動腳本 (用於 GCE metadata startup-script) ---
# 這個腳本會在 VM 啟動時自動執行環境安裝和爬蟲啟動

set -e

PROJECT_USER="hyc9977"
LOG_FILE="/home/$PROJECT_USER/vm_startup.log"

# 記錄 VM 啟動
echo "========================================" > $LOG_FILE
echo "VM 啟動時間: $(date)" >> $LOG_FILE

# 先安裝 git (用於 clone 程式碼)
echo "安裝 git..." >> $LOG_FILE
apt-get update -y >> $LOG_FILE 2>&1
apt-get install -y git >> $LOG_FILE 2>&1

# 取得腳本所在目錄
SCRIPT_DIR="/home/$PROJECT_USER/TCGweb-health-checker"

# 檢查是否已經安裝過環境
if [ ! -f "/home/$PROJECT_USER/.crawler_env_installed" ]; then
    echo "首次啟動，執行環境安裝..." >> $LOG_FILE
    
    # 如果專案目錄不存在，先 clone
    if [ ! -d "$SCRIPT_DIR" ]; then
        echo "複製專案程式碼..." >> $LOG_FILE
        if ! id "$PROJECT_USER" >/dev/null 2>&1; then
            useradd -m -s /bin/bash "$PROJECT_USER"
        fi
        sudo -u $PROJECT_USER git clone "https://github.com/jchilling/TCGweb-health-checker" "$SCRIPT_DIR" >> $LOG_FILE 2>&1
    fi
    
    # 執行環境安裝
    cd "$SCRIPT_DIR"
    chmod +x setup-environment.sh
    ./setup-environment.sh >> $LOG_FILE 2>&1
    
    # 標記環境已安裝
    touch "/home/$PROJECT_USER/.crawler_env_installed"
    echo "環境安裝完成" >> $LOG_FILE
else
    echo "環境已安裝，跳過安裝步驟" >> $LOG_FILE
fi

# 執行爬蟲
echo "啟動爬蟲程式..." >> $LOG_FILE
cd "$SCRIPT_DIR"
chmod +x run-crawler-selfqueue.sh
./run-crawler-selfqueue.sh >> $LOG_FILE 2>&1

echo "VM 啟動腳本執行完成: $(date)" >> $LOG_FILE