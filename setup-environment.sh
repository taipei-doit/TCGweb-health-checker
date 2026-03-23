#!/bin/bash

# --- 環境安裝腳本 (只需要執行一次) ---
# 用於 GCE VM 的初始化設定

set -e

# --- 設定變數 ---
PROJECT_USER="hyc9977"
PROJECT_DIR="/home/$PROJECT_USER/TCGweb-health-checker"
REPO_URL="https://github.com/jchilling/TCGweb-health-checker"
LOG_FILE="/home/$PROJECT_USER/setup_environment.log"

# --- 記錄開始 ---
echo "========================================" > $LOG_FILE
echo "環境安裝開始時間: $(date)" >> $LOG_FILE

# --- 1. 更新系統並安裝基本套件 ---
echo "更新系統套件..." >> $LOG_FILE
apt-get update -y >> $LOG_FILE 2>&1

echo "安裝系統套件 (git, pip, curl)..." >> $LOG_FILE
apt-get install -y python3-pip git curl >> $LOG_FILE 2>&1

# --- 2. 安裝 Playwright 所需的系統依賴 ---
echo "安裝 Playwright 系統依賴..." >> $LOG_FILE
apt-get install -y \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libcairo2 \
    libpango-1.0-0 \
    libasound2 \
    libnss3 \
    libnspr4 \
    libdrm2 \
    libxss1 \
    libgconf-2-4 \
    libxshmfence1 \
    libxkbcommon-x11-0 \
    fonts-liberation \
    libappindicator3-1 \
    xdg-utils >> $LOG_FILE 2>&1ZZZZ

# --- 3. 建立使用者 (如果不存在) ---
if ! id "$PROJECT_USER" >/dev/null 2>&1; then
    echo "建立使用者 $PROJECT_USER..." >> $LOG_FILE
    useradd -m -s /bin/bash "$PROJECT_USER" >> $LOG_FILE 2>&1
fi

# --- 4. 複製程式碼 ---
if [ ! -d "$PROJECT_DIR" ]; then
    echo "複製程式碼從 $REPO_URL..." >> $LOG_FILE
    sudo -u $PROJECT_USER git clone "$REPO_URL" "$PROJECT_DIR" >> $LOG_FILE 2>&1
else
    echo "程式碼目錄已存在，跳過複製" >> $LOG_FILE
fi

# --- 5. 安裝 Python 依賴 ---
cd "$PROJECT_DIR"
echo "安裝 Python 依賴套件 (requirements.txt)..." >> $LOG_FILE
sudo -u $PROJECT_USER pip3 install -r requirements.txt >> $LOG_FILE 2>&1

echo "安裝 Playwright 瀏覽器 (chromium)..." >> $LOG_FILE
sudo -u $PROJECT_USER python3 -m playwright install chromium >> $LOG_FILE 2>&1

# --- 6. 設定權限 ---
chown -R $PROJECT_USER:$PROJECT_USER "$PROJECT_DIR" >> $LOG_FILE 2>&1

echo "環境安裝完成時間: $(date)" >> $LOG_FILE
echo "========================================" >> $LOG_FILE
echo ""
echo "✅ 環境安裝完成！"
echo "📁 專案目錄: $PROJECT_DIR"
echo "📝 安裝日誌: $LOG_FILE"
echo ""
echo "下次可以直接使用 run-crawler.sh 來執行爬蟲程式"