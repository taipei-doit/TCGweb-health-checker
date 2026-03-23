# TCGweb Website Health Checker

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![GCP](https://img.shields.io/badge/GCP-Compute%20Engine-4285F4.svg)](https://cloud.google.com/compute)
[![Cloud Run](https://img.shields.io/badge/Cloud%20Run-Management%20UI-00C853.svg)](https://cloud.google.com/run)

臺北市政府網站健康度檢測系統，提供全面的網站內容時效性分析、內外部連結有效性檢查，支援 GCP 全自動化月度排程。

> 本專案由臺北市政府資訊局多位替代役同仁開發，後續由資訊局同仁接手進行維護、功能擴充與系統上線部署。

## 主要功能

- **智能深度爬蟲**：自動發現 sitemap，支援 SPA (React/Vue/Angular)、Frameset，多層級 BFS 爬取
- **智能日期識別**：支援民國年、中英日期、meta tag 等多種格式，自動清除 header/footer 雜訊
- **連結有效性檢查**：檢測內部頁面和外部連結狀態，HEAD → GET fallback，HTTP → HTTPS 自動重試
- **專業報告生成**：Excel 統計報告 + JSON 詳細摘要 + 錯誤連結 CSV
- **高效能處理**：multiprocessing Pool / Queue 雙模式，psutil 記憶體監控自動重啟 worker
- **重複檢測**：標題比對 + 路徑分析 + 內容前 500 字比較，自動跳過重複頁面和分頁
- **Email 報告**：Amazon SES 自訂域名發送（Gmail 備援），ZIP 智慧分割，多收件人支援
- **Cloud Run 管理平台**：Web UI 管理收件人、網站清單、一鍵啟動爬蟲
- **全自動化排程**：Cloud Scheduler 每月觸發 → VM 自動開機執行 → 完成後自動關機

## 系統架構

```
Cloud Scheduler (每月1號 02:00)
  → 啟動 GCP VM (n1-standard-16)
  → startup-script: git pull → Firestore 同步 → 執行爬蟲
  → 完成 → SES Email 報告 → 自動關機

Cloud Run (管理平台)
  → Web UI: Email 收件人 / 網站清單 / 爬蟲控制
  → Firestore 作為單一資料來源

Firestore
  → email_recipients: 收件人名單
  → websites: 受檢網站清單
  → crawler_events: 執行紀錄
```

## 快速開始

### 本地執行

```bash
# 安裝依賴
pip install -r requirements.txt
playwright install chromium

# 設定環境變數
cp .env.example .env
# 編輯 .env 填入 SMTP 憑證

# 執行檢測
python main.py --depth 2 --concurrent 2
```

### GCP 全自動部署

```bash
# 1. 初始化 Firestore
cd deploy
./setup-firestore.sh <project-id>

# 2. 部署管理平台 (Cloud Run)
cd ../email-manager
./deploy.sh <project-id>

# 3. 建立爬蟲 VM (n1-standard-16, 16 vCPU / 60GB RAM)
cd ../deploy
./create-vm.sh <project-id> crawler-webcheck

# 4. 設定每月排程
./setup-scheduler.sh <project-id> crawler-webcheck
```

## 統一入口程式

`gcp_main_unified.py` 合併了所有執行模式：

```bash
# Pool 模式（預設，每任務自動重啟 process）
python gcp_main_unified.py --mode pool --depth 3 --concurrent 13

# Queue 模式（psutil 記憶體監控，超標自動重啟 worker）
python gcp_main_unified.py --mode queue --depth 3 --concurrent 13 --max-mem-mb 1024

# 本地測試（不關機、不寄信、不同步 Firestore）
python gcp_main_unified.py --no-shutdown --no-email --no-sync
```

### 完整參數

| 參數 | 說明 | 預設值 |
|------|------|--------|
| `--mode` | 執行模式：`pool` 或 `queue` | `pool` |
| `--depth` | 爬蟲最大深度 | 2 |
| `--config` | 網站設定檔路徑 | `config/websites.csv` |
| `--concurrent` | 並行 worker 數量 | 2 |
| `--no-save-html` | 不儲存 HTML 檔案 | False |
| `--no-pagination` | 禁用分頁爬取 | False |
| `--max-mem-mb` | worker 記憶體上限 (MB)，僅 queue 模式 | 1024 |
| `--vm-name` | GCE VM 名稱（自動關機用） | `crawler-webcheck` |
| `--no-shutdown` | 不自動關機 | False |
| `--no-email` | 不發送 Email | False |
| `--no-sync` | 跳過 Firestore 同步 | False |

## 管理平台 (Cloud Run)

部署後提供 Web UI，包含三個功能頁面：

### Email 收件人管理

管理報告的 Email 收件者名單。新增或刪除收件人後，資料即時存入 Firestore。爬蟲 VM 每次開機時會自動同步最新的收件人名單。

### 網站清單管理

管理要進行健康檢查的網站清單，每個網站可設定以下參數：

| 欄位 | 說明 |
|------|------|
| **URL** | 網站完整網址（必填） |
| **網站名稱** | 用於報告中的顯示名稱和資料夾命名（選填） |
| **爬取深度** | 爬蟲從首頁開始往下爬幾層連結。例如深度 1 = 只爬首頁 + 首頁上的連結；深度 3 = 往下三層。留空則使用全域設定（預設 2）。深度越大爬取越完整但耗時越長 |
| **是否存 HTML** | 設為 TRUE 會將爬到的每個頁面儲存為 HTML 檔案，方便離線檢視原始內容。設為 FALSE 則只產生統計數據不存檔，大幅節省磁碟空間和加快速度。留空使用全域設定 |
| **是否爬分頁** | 設為 TRUE 時，若頁面有分頁列表（如 `?page=2`、`?offset=10`），爬蟲會逐頁爬取並提取每頁中的連結。設為 FALSE 則將分頁視為重複頁面直接跳過，適用於大量分頁列表的網站以提升效能。留空使用全域設定 |

支援功能：
- **CSV 匯入**：上傳 CSV 批次新增網站（格式：`URL,name,depth,save_html,pagination`）
- **CSV 匯出**：將目前清單匯出為 CSV，可作為備份或交由其他系統使用
- **搜尋**：即時篩選，輸入關鍵字搜尋網站名稱或 URL

### 爬蟲控制面板

| 功能 | 說明 |
|------|------|
| **VM 狀態** | 即時顯示爬蟲 VM 是否執行中（RUNNING）或已停止（TERMINATED），含機型與區域資訊 |
| **啟動爬蟲** | 一鍵啟動 VM。VM 開機後會自動從 Firestore 同步最新的網站清單和收件人，然後開始執行健康檢查。完成後自動寄送報告並關機 |
| **停止 VM** | 緊急停止 VM（會中斷正在執行的爬蟲，已完成的網站資料不受影響） |
| **執行紀錄** | 顯示最近的啟動/停止事件記錄，包含觸發來源（Web UI 手動觸發或 Cloud Scheduler 排程觸發） |

## 網站設定檔 (CSV)

除了透過管理平台操作，也可以直接編輯 `config/websites.csv`：

```csv
URL,name,depth,save_html,pagination
https://example.gov.taipei/,範例網站,,,
https://large-site.gov.taipei/,大型網站,3,FALSE,FALSE
https://archive-site.gov.taipei/,需要存檔的網站,2,TRUE,TRUE
```

## Email 設定

支援 Amazon SES（推薦）和 Gmail 兩種 SMTP 後端，系統自動判斷：

```env
# Amazon SES（優先）— 使用自訂域名發送，信譽高、不易被標為垃圾信
SMTP_HOST=email-smtp.us-east-1.amazonaws.com
SMTP_PORT=587
SMTP_USER=YOUR_SES_SMTP_USERNAME
SMTP_PASSWORD=YOUR_SES_SMTP_PASSWORD
SMTP_FROM=noreply@mail.app.taipei

# Gmail（備援）— 僅在 SMTP_HOST 未設定時使用
# GMAIL_USER=your_email@gmail.com
# GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

# 收件人（Firestore 優先，此處為 fallback）
TO_EMAIL=admin@example.com
```

## 輸出報告

### Excel 統計報告
`output/website_summary_report_YYYY-MM.xlsx`

包含：網站名稱、URL、總頁面數、有/無日期頁面數、最後更新日期、一年前內容數量與比例、失效內/外部連結數、爬取耗時與日期。支援斷點續爬 — 若中途中斷，下次執行會自動跳過已完成的網站繼續處理。

### JSON 詳細摘要
`assets/{網站名稱}/page_summary.json`

包含每個頁面的標題、更新日期、狀態碼、來源頁面，以及所有外部連結的狀態。頁面按日期由新到舊排序，外部連結按狀態碼分類排序。

### 錯誤連結 CSV
- `error_pages.csv` — 失效內部頁面（4xx/5xx/連線失敗）
- `error_external_links.csv` — 失效外部連結

## 專案結構

```
├── gcp_main_unified.py        # 統一入口（Pool + Queue 雙模式）
├── main.py                    # 本地執行版
├── crawler/
│   └── web_crawler.py         # 爬蟲引擎（Playwright + httpx）
├── analyzer/
│   └── date_extraction.py     # 日期提取引擎
├── reporter/
│   ├── report_generation.py   # Excel 報告（async 版）
│   └── report_generation_mp.py # Excel 報告（multiprocessing 版）
├── utils/
│   ├── email_reporter.py      # Email 發送（SES/Gmail + 多收件人）
│   ├── sync_firestore.py      # 開機同步 Firestore → 本地
│   ├── extract_problematic_links.py
│   └── log_writer.py
├── email-manager/             # Cloud Run 管理平台
│   ├── app.py                 # Flask 後端（收件人 + 網站 + VM 控制 API）
│   ├── templates/index.html   # Web UI（三頁 Tab 介面）
│   ├── Dockerfile
│   └── deploy.sh
├── deploy/                    # GCP 部署腳本
│   ├── create-vm.sh           # 建立 VM (n1-standard-16)
│   ├── setup-scheduler.sh     # 設定每月排程 (Cloud Scheduler)
│   ├── setup-firestore.sh     # 初始化 Firestore 資料庫
│   └── startup-script-unified.sh  # VM 開機自動執行腳本
├── config/
│   └── websites.csv           # 網站設定檔
├── assets/                    # 爬取資料（gitignore）
└── output/                    # 報告輸出（gitignore）
```

## 系統需求

- Python 3.8+
- 穩定的網路連線
- 本地執行：建議每個 process 4GB+ 記憶體
- GCP 推薦：n1-standard-16（16 vCPU / 60GB RAM）搭配 100GB SSD

## 專案資訊

- **版本**: 2.0.0
- **最後更新**: 2026-03
- **維護者**: tpe-doit (臺北市政府資訊局)
