"""
為每個網站資料夾產生兩個CSV檔案：
1. error_pages.csv - 內部錯誤頁面
2. error_external_links.csv - 錯誤外部連結
"""

import json
import csv
from pathlib import Path

def write_to_csv(data_list, output_file):
    """將結果寫入CSV檔案"""
    with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['problematic_url', 'status', 'parent_url']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        # 寫入標題行
        writer.writeheader()
        
        # 寫入資料
        for item in data_list:
            writer.writerow(item)

def extract_error_links_from_json(json_file_path):
    """從 page_summary.json 檔案中提取錯誤連結"""
    json_path = Path(json_file_path)
    
    if not json_path.exists():
        print(f"  ⚠️  JSON 檔案不存在: {json_file_path}")
        return
    
    website_folder = json_path.parent
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        error_pages = []
        error_external_links = []
        
        # 處理 page_summary 中的錯誤頁面
        page_summary = data.get('page_summary', {})
        for url, link_info in page_summary.items():
            status = link_info.get('status', 200)
            
            # 只處理有問題的連結 (4xx、5xx 及連線失敗，排除 3xx 重新導向)
            if status >= 400 or status == 0:
                source_page = link_info.get('source_page')
                source_page_url = source_page.get('url', '') if source_page else ''

                error_pages.append({
                    'problematic_url': url,
                    'status': status,
                    'parent_url': source_page_url
                })

        # 處理 external_links 中的錯誤外部連結
        external_links = data.get('external_links', {})
        for url, link_info in external_links.items():
            status = link_info.get('status', 200)

            # 只處理有問題的連結 (4xx、5xx 及連線失敗，排除 3xx 重新導向)
            if status >= 400 or status == 0:
                source_page = link_info.get('source_page')
                source_page_url = source_page.get('url', '') if source_page else ''
                
                error_external_links.append({
                    'problematic_url': url,
                    'status': status,
                    'parent_url': source_page_url
                })
        
        # 寫入CSV檔案
        if error_pages:
            error_pages_file = website_folder / "error_pages.csv"
            write_to_csv(error_pages, error_pages_file)
        
        if error_external_links:
            error_external_links_file = website_folder / "error_external_links.csv"
            write_to_csv(error_external_links, error_external_links_file)
                    
    except Exception as e:
        print(f"  ❌ 提取錯誤連結時發生錯誤: {e}")