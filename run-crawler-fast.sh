#!/bin/bash

# --- 爬蟲執行腳本 (每次運行使用) ---
# 用於更新程式碼並執行爬蟲

set -e

# --- 設定變數 ---
PROJECT_USER="hyc9977"
PROJECT_DIR="/home/$PROJECT_USER/TCGweb-health-checker"
PYTHON_CMD="python3 -u"
PYTHON_SCRIPT="gcp_main_mpfast.py"  # 使用 multiprocessing 版本
LOG_FILE="/home/$PROJECT_USER/crawler_startup.log"

# --- 記錄啟動 ---
echo "========================================" >> $LOG_FILE
echo "爬蟲啟動時間: $(date)" >> $LOG_FILE

# --- 1. 檢查目錄是否存在 ---
if [ ! -d "$PROJECT_DIR" ]; then
    echo "錯誤：專案目錄不存在 $PROJECT_DIR" >> $LOG_FILE
    echo "請先執行 setup-environment.sh 進行環境安裝" >> $LOG_FILE
    exit 1
fi

# --- 2. 更新程式碼 ---
cd "$PROJECT_DIR"
echo "更新程式碼..." >> $LOG_FILE
sudo -u $PROJECT_USER git reset --hard HEAD >> $LOG_FILE 2>&1
sudo -u $PROJECT_USER git pull >> $LOG_FILE 2>&1

# --- 3. 檢查必要檔案 ---
if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo "錯誤：找不到 $PYTHON_SCRIPT 檔案" >> $LOG_FILE
    exit 1
fi

if [ ! -f "config/websites.csv" ]; then
    echo "錯誤：找不到 config/websites.csv 設定檔" >> $LOG_FILE
    exit 1
fi

# --- 4. 檢查是否已在執行 ---
if pgrep -u "$PROJECT_USER" -f "$PYTHON_SCRIPT" > /dev/null; then
    echo "檢查：$PYTHON_SCRIPT 已經在執行，啟動腳本將退出" >> $LOG_FILE
    echo "爬蟲啟動腳本執行完成 (未啟動新程序): $(date)" >> $LOG_FILE
    exit 0
fi

# --- 5. 執行爬蟲程式 ---
echo "開始執行網站爬蟲 ($PYTHON_SCRIPT)..." >> $LOG_FILE

# 使用 multiprocessing 版本的推薦參數
CRAWLER_ARGS="--depth 3 --concurrent 13 --no-save-html --no-pagination"
echo "執行指令: $PYTHON_CMD $PYTHON_SCRIPT $CRAWLER_ARGS" >> $LOG_FILE

# 在背景執行 (程式內建自動關機功能)
nohup sudo -u $PROJECT_USER \
    $PYTHON_CMD $PYTHON_SCRIPT $CRAWLER_ARGS \
    > /home/$PROJECT_USER/crawler_execution.log 2>&1 &

CRAWLER_PID=$!
echo "爬蟲程式已在背景啟動，PID: $CRAWLER_PID" >> $LOG_FILE
echo "執行日誌: /home/$PROJECT_USER/crawler_execution.log" >> $LOG_FILE
echo "爬蟲啟動腳本執行完成: $(date)" >> $LOG_FILE

echo ""
echo "✅ 爬蟲程式已啟動！"
echo "🔍 程序 PID: $CRAWLER_PID"
echo "📝 啟動日誌: $LOG_FILE"
echo "📊 執行日誌: /home/$PROJECT_USER/crawler_execution.log"
echo ""
echo "📌 程式完成後會自動關閉 VM"
echo "   可以使用以下指令查看執行進度："
echo "   tail -f /home/$PROJECT_USER/crawler_execution.log"