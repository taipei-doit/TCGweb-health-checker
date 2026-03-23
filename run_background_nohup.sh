#!/bin/bash

# 背景運行腳本 - 使用 nohup
# 使用方法: ./run_background_nohup.sh

echo "=== 開始背景運行爬蟲程式 (nohup) ==="
echo "時間: $(date)"

# 切換到 conda 環境並背景運行
#python -u main.py --depth 3 --concurrent 2 --no-save-html --no-pagination
nohup bash -c "
    source ~/opt/anaconda3/etc/profile.d/conda.sh
    conda activate tpdic_web
    cd /Users/yoyo/Check_TCGweb
    python -u main.py --depth 3 --concurrent 2 --no-save-html --no-pagination
" > crawler_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# 記錄 PID
echo $! > crawler.pid

echo "程式已在背景運行"
echo "PID: $(cat crawler.pid)"
echo "日誌檔案: crawler_$(date +%Y%m%d_%H%M%S).log"
echo ""
echo "監控指令:"
echo "  tail -f crawler_*.log    # 查看即時日誌"
echo "  ps -p \$(cat crawler.pid)  # 檢查程式狀態"
echo "  kill \$(cat crawler.pid)   # 停止程式"