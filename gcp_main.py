import csv
import os
import sys
import asyncio
import argparse
import time
import gc  # 用於強制垃圾回收
# from dotenv import load_dotenv
from playwright.async_api import async_playwright

from crawler.web_crawler import WebCrawlerAgent
from reporter.report_generation import ReportGenerationAgent
from utils.extract_problematic_links import extract_error_links_from_json


def load_websites(path: str):
    websites_config = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            websites_config.append(row)
    return websites_config


async def process_single_website(semaphore: asyncio.Semaphore, browser, url: str, name: str, reporter: ReportGenerationAgent, depth: int, save_html: bool = True, enable_pagination: bool = True) -> dict:
    """處理單一網站的異步函數，使用 semaphore 控制並行數量"""
    
    async with semaphore:
        print(f"\n🔍 開始處理網站: {name or url}")
        
        # 為每個網站創建獨立的 crawler 實例，傳入是否儲存HTML的參數和分頁控制參數
        crawler = WebCrawlerAgent(save_html_files=save_html, enable_pagination=enable_pagination)
        
        try:
            # 記錄開始時間
            start_time = time.time()
            
            # 使用深度爬蟲
            crawl_results = await crawler.crawl_site(browser, url, name=name, max_depth=depth)
            
            # 計算爬取耗時
            crawl_duration = time.time() - start_time
            crawl_duration_formatted = f"{int(crawl_duration // 60)}分{int(crawl_duration % 60)}秒"
            
            # 儲存頁面摘要為 JSON
            json_path = crawler.save_page_summary_to_json()
            if json_path:
                print(f"✅ 已儲存 {name or url} 頁面摘要到: {json_path}")
                
                # 立即提取錯誤連結並產生 CSV 檔案
                extract_error_links_from_json(json_path)
            
            # 儲存爬蟲 log
            crawl_log_path = crawler.save_crawl_log()
            if crawl_log_path:
                print(f"📝 已儲存 {name or url} 爬蟲 log 到: {crawl_log_path}")
            
            site_stats = {
                'site_name': name or url,
                'site_url': url,
                'crawl_results': crawl_results,  # 內部頁面 status 碼列表
                'page_summary': crawler.get_page_summary(),      
                'external_link_results': crawler.get_external_link_results(), 
                'crawl_duration': crawl_duration_formatted
            }
            
            # 立即將這個網站的資料寫入 Excel
            await reporter.add_site_to_excel(site_stats)
            
            print(f"✅ 網站 '{name or url}' 處理完成，共爬取 {len(crawl_results)} 個頁面，耗時 {crawl_duration_formatted}")
            
            return True
            
        except Exception as e:
            print(f"❌ 處理網站 '{name or url}' 時發生錯誤: {e}")
            return False
        
        finally:
            # 關閉 crawler httpx client
            await crawler.close()
            # 立即手動清除 crawler 內部的大型字典
            crawler.clear_memory()
            
            # 檢查 site_stats 變數是否存在並手動刪除它
            # 釋放對字典的最後一個參考
            if 'site_stats' in locals():
                del site_stats
            
            # 完全刪除 crawler 對象
            del crawler
            
            # 強制 Python 執行垃圾回收
            gc.collect()
            
async def auto_shutdown_vm():
    """
    自動關閉 GCE VM 執行個體
    """
    try:
        import subprocess
        
        # 直接使用固定的 VM 名稱和區域
        vm_name = "crawler-webcheck"
        zone = "asia-east1-c"
        
        print(f"🎉 任務全部完成，準備自動關閉 VM: {vm_name}")
        print(f"📍 VM 位置: {zone}")
        
        # 執行關機指令
        shutdown_cmd = f"gcloud compute instances stop {vm_name} --zone={zone} --quiet"
        print(f"💻 執行關機指令: {shutdown_cmd}")
        
        shutdown_result = subprocess.run(
            shutdown_cmd.split(),
            capture_output=True, text=True, timeout=60
        )
        
        if shutdown_result.returncode == 0:
            print("✅ VM 關機指令執行成功")
        else:
            print(f"❌ VM 關機指令執行失敗: {shutdown_result.stderr}")
            
    except Exception as e:
        print(f"⚠️ 自動關機失敗: {e}")
        print("ℹ️ VM 將保持開啟狀態")            


async def main():
    # 解析命令行參數
    parser = argparse.ArgumentParser(description='網站爬蟲和分析工具')
    parser.add_argument('--depth', type=int, default=2, 
                       help='爬蟲的最大深度 (預設: 2)')
    parser.add_argument('--config', type=str, default="config/websites.csv",
                       help='網站設定檔案路徑 (預設: config/websites.csv)')
    parser.add_argument('--concurrent', type=int, default=2,
                       help='同時處理的網站數量 (預設: 2)')
    parser.add_argument('--no-save-html', action='store_true',
                       help='不儲存HTML檔案，僅產生統計報告 (提升效能，節省磁碟空間)')
    parser.add_argument('--no-pagination', action='store_true',
                       help='禁用分頁爬取，將有分頁參數的頁面視為重複頁面跳過 (提升效能)')
    
    args = parser.parse_args()
    
    # 取得全域預設值
    global_depth = args.depth
    global_save_html = not args.no_save_html
    global_enable_pagination = not args.no_pagination
    
    #load_dotenv()
    
    if not os.path.exists(args.config):
        print(f"錯誤：找不到設定檔案 {args.config}")
        sys.exit(1)
    
    websites = load_websites(args.config)
    print(f"載入了 {len(websites)} 個網站，最大爬蟲深度: {global_depth}，並行數量: {args.concurrent}")
    if global_save_html:
        print("💾 HTML檔案儲存: 啟用")
    else:
        print("🚀 HTML檔案儲存: 停用 (僅產生統計報告，提升效能)")
    
    if global_enable_pagination:
        print("📄 分頁爬取: 啟用")
    else:
        print("⚡ 分頁爬取: 停用 (分頁視為重複頁面跳過，提升效能)")

    # 創建 semaphore 來控制並行數量
    semaphore = asyncio.Semaphore(args.concurrent)
    
    # 初始化報告生成器
    reporter = ReportGenerationAgent()
    output_path = reporter.initialize_excel_report()
    print(f"Excel 報告檔案初始化完成: {output_path}")
    
    # 取得已處理的網站URL列表（用於斷點續爬）
    processed_urls = reporter.get_processed_urls()
    
    # 過濾掉已處理的網站
    websites_to_process = []
    
    for site in websites:
        url = site["URL"]
        if url.strip() in processed_urls:
            continue
            
        websites_to_process.append(site)
    
    print(f"📋 總共 {len(websites)} 個網站，剩餘 {len(websites_to_process)} 個待處理")
    
    if not websites_to_process:
        print("🎉 所有網站都已處理完成！")
        reporter.finalize_excel_report()
        print(f"📄 報告已儲存到: {output_path}")
        
        # --- 自動關機功能 ---
        await auto_shutdown_vm()
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        crawl_success = True  # 預設為成功
        
        try:
            print(f"\n🚀 開始並行處理 {len(websites_to_process)} 個網站...")
            start_time = time.time()
            
            # 創建所有網站的處理任務
            tasks = []
            for site in websites_to_process:
                # 讀取 CSV 中的特定設定，如果為空或無效，則使用全域預設值
                
                try:
                    # 嘗試讀取 'depth'，如果失敗或為空，使用 global_depth
                    site_depth = int(site.get('depth')) if site.get('depth') else global_depth
                except (ValueError, TypeError):
                    site_depth = global_depth
                    
                # 嘗試讀取 'save_html'
                if site.get('save_html', '').lower() == 'true':
                    site_save_html = True
                elif site.get('save_html', '').lower() == 'false':
                    site_save_html = False
                else:
                    site_save_html = global_save_html # CSV 中為空，使用全域設定
                
                # 嘗試讀取 'pagination'
                if site.get('pagination', '').lower() == 'true':
                    site_enable_pagination = True
                elif site.get('pagination', '').lower() == 'false':
                    site_enable_pagination = False
                else:
                    site_enable_pagination = global_enable_pagination # CSV 中為空，使用全域設定

                url = site["URL"]
                name = site.get("name", "")

                # 建立任務時，傳入特定於該網站的參數
                task = process_single_website(
                    semaphore, 
                    browser, 
                    url, 
                    name, 
                    reporter, 
                    site_depth,            
                    site_save_html,        
                    site_enable_pagination 
                )
                tasks.append(task)
            
            # 並行執行所有任務，return_exceptions=True 確保單一失敗不會影響其他任務
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 統計結果
            successful_sites = sum(1 for result in results if result is True)
            failed_sites = len(websites_to_process) - successful_sites
            total_duration = time.time() - start_time
            total_duration_formatted = f"{int(total_duration // 60)}分{int(total_duration % 60)}秒"
            
            print(f"\n🎉 並行處理完成!")
            print(f"📊 成功處理: {successful_sites} 個網站")
            print(f"❌ 失敗: {failed_sites} 個網站") 
            print(f"⏱️ 總耗時: {total_duration_formatted}")
                        
        except Exception as e:
            print(f"\n💥 爬蟲執行過程中發生嚴重錯誤: {e}")
            crawl_success = False  # 有異常就不關機
            
        finally:
            await browser.close()
            
            # 完成 Excel 報告
            reporter.finalize_excel_report()
            print(f"\n📄 報告已儲存到: {output_path}")
            print(f"✅ 總共處理了 {len(websites_to_process)} 個新網站")
            
            # 只有在成功時才關機
            if crawl_success:
                await auto_shutdown_vm()
            else:
                print("🔧 由於執行過程中發生錯誤，VM 將保持開啟狀態以便除錯")

if __name__ == "__main__":
    asyncio.run(main())
