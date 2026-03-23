import csv
import os
import sys
import asyncio
import argparse
import time
import psutil
import multiprocessing
from multiprocessing import Process, Queue
from queue import Empty 
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
from dotenv import load_dotenv

# 載入環境變數
load_dotenv()

from crawler.web_crawler import WebCrawlerAgent
from reporter.report_generation_mp import ReportGenerationAgent
from utils.extract_problematic_links import extract_error_links_from_json
from utils.email_reporter import EmailReporter


def load_websites(path: str):
    websites_config = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            websites_config.append(row)
    return websites_config


async def _async_crawl_worker(site_config: dict) -> dict:
    """
    Subprocess 中 asyncio 迴圈內執行的真正爬蟲
    """
    url = site_config["URL"]
    name = site_config.get("name", "")
    depth = site_config["global_depth"]
    save_html = site_config["global_save_html"]
    enable_pagination = site_config["global_enable_pagination"]

    print(f"\n🔍 [PID {os.getpid()}] 開始處理網站: {name or url}")
    
    try:
        # 在 subprocess 中建立 crawler 和 playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            crawler = WebCrawlerAgent(save_html_files=save_html, enable_pagination=enable_pagination)
            
            start_time = time.time()
            
            # 執行爬蟲
            crawl_results = await crawler.crawl_site(browser, url, name=name, max_depth=depth)
            crawl_duration = time.time() - start_time
            crawl_duration_formatted = f"{int(crawl_duration // 60)}分{int(crawl_duration % 60)}秒"
            
            page_summary = crawler.get_page_summary()
            external_link_results = crawler.get_external_link_results()

            # 儲存 JSON/Log
            json_path = crawler.save_page_summary_to_json()
            if json_path:
                extract_error_links_from_json(json_path)
            crawler.save_crawl_log()

            # 預先計算統計數據給 Excel
            one_year_ago = datetime.now() - timedelta(days=365)
            total_pages = len(crawl_results)
            failed_pages = sum(1 for status in crawl_results if status >= 400 or status == 0)
            failed_external_links = sum(1 for link_info in external_link_results.values() 
                                       if link_info.get('status', 0) >= 400 or link_info.get('status', 0) == 0)
            
            # 計算日期相關統計
            today = datetime.now().date()
            pages_with_date = 0
            no_date_pages = 0
            outdated_pages = 0
            past_dates = []  # 今天或以前的日期
            future_dates = []  # 未來日期
            
            for url_key, page_info in page_summary.items():
                last_updated = page_info.get('last_updated', '')
                
                # 統計無日期的頁面
                if last_updated == "[無日期]" or last_updated == "[爬取失敗]" or not last_updated:
                    no_date_pages += 1
                    continue
                
                try:
                    update_date = datetime.strptime(last_updated, '%Y-%m-%d')
                    update_date_only = update_date.date()
                    
                    if update_date_only <= today:
                        past_dates.append(update_date)
                        # 檢查是否為一年前的內容
                        if update_date < one_year_ago:
                            outdated_pages += 1
                    else:
                        future_dates.append(update_date)
                        
                except ValueError:
                    no_date_pages += 1
                    continue
            
            # 計算最新更新日期：優先使用過去日期的最新值，沒有才用最接近今天的未來日期
            if past_dates:
                latest_update = max(past_dates).strftime('%Y-%m-%d')
            elif future_dates:
                latest_update = min(future_dates).strftime('%Y-%m-%d')
            else:
                latest_update = "無有效日期"
            
            # 計算一年前內容的比例
            pages_with_date = len(past_dates) + len(future_dates)
            outdated_percentage = (outdated_pages / pages_with_date * 100) if pages_with_date > 0 else 0
            
            # 建立小型結果字典
            stats_for_excel = {
                'site_name': name or url,
                'site_url': url,
                'total_pages': total_pages,
                'pages_with_date': pages_with_date,
                'no_date_pages': no_date_pages,
                'latest_update': latest_update,
                'outdated_pages': outdated_pages,
                'outdated_percentage': round(outdated_percentage, 2),
                'failed_pages': failed_pages,
                'failed_external_links': failed_external_links,
                'total_external_links': len(external_link_results),
                'crawl_duration': crawl_duration_formatted
            }
            
            # 清理並關閉
            del page_summary
            del external_link_results
            await crawler.close()
            crawler.clear_memory()
            del crawler
            await browser.close()
            
            return stats_for_excel
                
    except Exception as e:
        print(f"❌ [PID {os.getpid()}] 處理網站 '{name or url}' 時發生錯誤: {e}")
        try:
            if 'crawler' in locals() and crawler:
                await crawler.close()
                crawler.clear_memory()
                del crawler
            if 'browser' in locals() and browser:
                await browser.close()
            
            # 給予 5 秒緩衝時間
            await asyncio.sleep(5)
            
        except Exception as cleanup_e:
            print(f"💥 [PID {os.getpid()}] 在錯誤清理中發生了額外錯誤: {cleanup_e}")
            
        return None  # 發生錯誤時返回 None


def worker_process_loop(worker_id: int, task_queue: Queue, result_queue: Queue, max_mem_mb: int):
    """
    自訂的 Worker Process 迴圈
    它會先檢查記憶體，再決定是否接任務
    """
    print(f"✅ [Worker {worker_id} | PID {os.getpid()}] 啟動")
    
    process = psutil.Process(os.getpid())
    
    while True:
        try:
            # 接任務前的記憶體檢查
            memory_mb = process.memory_info().rss / 1024 / 1024
            
            if memory_mb > max_mem_mb:
                print(f"♻️  [Worker {worker_id} | PID {os.getpid()}] 記憶體超標 ({memory_mb:.1f} MB)，請求重啟...")
                result_queue.put(("RESTART", worker_id)) 
                break 

            # 記憶體正常，嘗試接任務
            try:
                site_config = task_queue.get(timeout=5.0) 
            except Empty:
                print(f"⌛ [Worker {worker_id} | PID {os.getpid()}] 任務佇列為空，自動退出")
                break

            # 檢查結束訊號
            if site_config is None:
                # 收到 None 訊號，代表任務已全部派發，直接退出
                print(f"🛑 [Worker {worker_id} | PID {os.getpid()}] 收到結束訊號，退出")
                break

            # 執行爬蟲任務
            try:
                stats_for_excel = asyncio.run(_async_crawl_worker(site_config))
                
                # 傳回結果
                result_queue.put(stats_for_excel)
                print(f"\n✅ [Worker {worker_id} | PID {os.getpid()}] 網站 '{site_config.get('name', 'N/A')}' 處理完成")
                
            except Exception as e:
                print(f"💥 [Worker {worker_id} | PID {os.getpid()}] 執行任務 '{site_config.get('name', 'N/A')}' 時發生錯誤: {e}")
                # 回報失敗，包含網站資訊以便追蹤
                result_queue.put(("FAILED", site_config.get('name', 'N/A')))
        
        except Exception as loop_e:
            # 捕捉 worker 迴圈本身的錯誤
            print(f"🆘 [Worker {worker_id} | PID {os.getpid()}] 迴圈發生嚴重錯誤: {loop_e}")
            result_queue.put(("RESTART", worker_id)) # 也請求重啟
            break
            
    print(f"👋 [Worker {worker_id} | PID {os.getpid()}] 結束")


def pack_and_send_email(excel_report_path):
    """
    使用 EmailReporter 來處理打包和發送 Email
    """
    print("開始執行打包與郵寄...")
    try:
        email_reporter = EmailReporter()
        
        # 根據需求選擇方法：
        # - pack_and_send_simple(): 單一檔案，適合小型資料
        # - pack_and_send_seperate(): 智慧分割，適合大型資料
        success = email_reporter.pack_and_send_seperate(excel_report_path)
        
        if success:
            print("✅ 郵寄任務完成！")
        else:
            print("❌ 郵寄任務失敗，但程式將繼續執行")
            
    except Exception as e:
        print(f"❌ EmailReporter 執行時發生錯誤: {e}")
        print("⚠️ 郵寄失敗，但程式將繼續執行")


def main():
    """
    主函數
    """
    parser = argparse.ArgumentParser(description='網站爬蟲和分析工具 (Multiprocessing 版本)')
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
    parser.add_argument('--max-mem-mb', type=int, default=1024,
                       help='subprocess 記憶體上限 (MB)，超過此值將自動回收 (預設: 1024)')
    
    args = parser.parse_args()
    
    # 取得全域預設值
    global_depth = args.depth
    global_save_html = not args.no_save_html
    global_enable_pagination = not args.no_pagination
    
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

    print("🚀 啟動 Multiprocessing 網站爬蟲...")

    # 初始化 Reporter
    reporter = ReportGenerationAgent()
    output_path = reporter.initialize_excel_report()
    print(f"Excel 報告檔案初始化完成: {output_path}")
    
    processed_urls = reporter.get_processed_urls()
    
    # 任務列表
    websites_to_process = []
    for site in websites:
        url = site["URL"]
        if url.strip() in processed_urls:
            continue
            
        # 參數合併處理  
        try:
            # 讀取表格內的 depth 值
            csv_depth = int(site.get('depth')) if site.get('depth') else None
            # 只有當全域深度大於表格內深度時，才使用表格內的較小值
            if csv_depth is not None and global_depth > csv_depth:
                site_depth = csv_depth
            else:
                site_depth = global_depth
        except (ValueError, TypeError):
            site_depth = global_depth
        site['global_depth'] = site_depth
        
        # 嘗試讀取 'save_html'
        if site.get('save_html', '').lower() == 'true':
            site['global_save_html'] = True
        elif site.get('save_html', '').lower() == 'false':
            site['global_save_html'] = False
        else:
            site['global_save_html'] = global_save_html  # CSV 中為空，使用全域設定
        
        # 嘗試讀取 'pagination'
        if site.get('pagination', '').lower() == 'true':
            site['global_enable_pagination'] = True
        elif site.get('pagination', '').lower() == 'false':
            site['global_enable_pagination'] = False
        else:
            site['global_enable_pagination'] = global_enable_pagination  # CSV 中為空，使用全域設定
        
        site['global_max_mem_mb'] = args.max_mem_mb
            
        websites_to_process.append(site)
    
    num_total_tasks = len(websites_to_process)
    print(f"📋 總共 {len(websites)} 個網站，剩餘 {num_total_tasks} 個待處理")
    
    if not websites_to_process:
        print("🎉 所有網站都已處理完成！")
        reporter.finalize_excel_report()
        print(f"📄 報告已儲存到: {output_path}")
        
        print("準備打包並發送報告...")
        pack_and_send_email(output_path)
        return

    # 建立手動的 Process 和 Queue
    
    task_queue = Queue()
    result_queue = Queue()
    
    for site_config in websites_to_process:
        task_queue.put(site_config)
    
    # 放入結束訊號，等於 worker 數的 None
    for _ in range(args.concurrent):
        task_queue.put(None)
        
    # 建立 worker pool. {id: Process}
    worker_pool = {}

    print(f"\n🚀 啟動 {args.concurrent} 個自訂 worker...")
    start_time = time.time()
    
    # 啟動新 worker 的輔助函數
    def start_new_worker(worker_id):
        print(f"🌱 [Main-NEW_WORKER] 正在啟動新的 Worker {worker_id}...")
        p = Process(
            target=worker_process_loop, 
            args=(worker_id, task_queue, result_queue, args.max_mem_mb)
        )
        p.start()
        worker_pool[worker_id] = p

    for i in range(args.concurrent):
        start_new_worker(i)

    # --- Main 處理迴圈開始---
    
    successful_sites = 0
    failed_sites = 0
    processed_count = 0
    crawl_success = True

    try:
        while processed_count < num_total_tasks:
            
            try:
                result = result_queue.get(timeout=600.0) 
                
                # 處理 RESTART
                if isinstance(result, tuple) and result[0] == "RESTART":
                    worker_id_to_restart = result[1]
                    
                    print(f"\n🔥 [Main-RESTART_PROCESS_1] 收到 Worker {worker_id_to_restart} 的重啟請求")
                    
                    # 確保舊的 process 被清理
                    if worker_id_to_restart in worker_pool:
                        old_worker = worker_pool.pop(worker_id_to_restart)
                        if old_worker.is_alive():
                            print(f"⏳ [[Main-RESTART_PROCESS_2] 正在 join 舊的 Worker {worker_id_to_restart}...")
                            old_worker.join(timeout=10) # 給 10 秒
                            if old_worker.is_alive():
                                print(f"⚠️ [[Main-RESTART_PROCESS_3] Worker {worker_id_to_restart} join 超時，強制 terminate")
                                old_worker.terminate()
                                old_worker.join() # 確保 terminate 完成
                    
                    # 重新啟動一個同 ID 的新 worker
                    start_new_worker(worker_id_to_restart)

                # 處理 FAILED
                elif isinstance(result, tuple) and result[0] == "FAILED":
                    failed_site_name = result[1]
                    print(f"📊 [Main-FAILED] 收到失敗任務: {failed_site_name}")
                    failed_sites += 1
                    processed_count += 1
                    print(f"📈 [進度] {processed_count} / {num_total_tasks} (成功: {successful_sites}, 失敗: {failed_sites})")

                # 處理成功的任務
                else:
                    try:
                        crawl_date = datetime.now().strftime('%Y-%m-%d %H:%M')
                        result['crawl_date'] = crawl_date
                        
                        reporter.add_site_to_excel(result) # 寫入 Excel
                        successful_sites += 1
                    except Exception as e:
                        print(f"❌ 寫入 Excel 失敗: {e}")
                        failed_sites += 1
                    
                    processed_count += 1
                    print(f"📈 [進度] {processed_count} / {num_total_tasks} (成功: {successful_sites}, 失敗: {failed_sites})")
            
            except Empty:
                # 若 10 分鐘都沒有任何 worker 回傳結果檢查
                print("⏰ [Main-CHECK] 等待結果超過 10 分鐘，檢查 worker 狀態")
                all_dead = True
                for i, p in worker_pool.items():
                    if p.is_alive():
                        print(f"  -> Worker {i} (PID {p.pid}) 仍在執行中")
                        all_dead = False
                
                if all_dead:
                    print("❌ [Main-CHECK] 所有 worker 都已死亡，但任務未完成！強制退出...")
                    crawl_success = False
                    break # 跳出 while 迴圈
                else:
                    print("... 仍有 worker 存活，繼續等待...")

    except Exception as e:
        print(f"\n💥 主迴圈發生嚴重錯誤: {e}")
        crawl_success = False
        print("🚨 [Main-TERMINATE] 正在終止所有 worker...")
        for p in worker_pool.values():
            if p.is_alive():
                p.terminate()
                p.join()

    finally:
        total_duration = time.time() - start_time
        total_duration_formatted = f"{int(total_duration // 60)}分{int(total_duration % 60)}秒"
        
        print(f"\n{'='*50}")
        print(f"🎉 並行處理完成!")
        print(f"📊 成功處理: {successful_sites} 個網站")
        print(f"❌ 失敗: {failed_sites} 個網站") 
        print(f"⏱️ 總耗時: {total_duration_formatted}")
        
        reporter.finalize_excel_report()
        print(f"📄 報告已儲存到: {output_path}\n")
        
        if crawl_success and processed_count == num_total_tasks:
            print("🎉 任務全部完成，準備打包並發送報告...")
            pack_and_send_email(output_path)
        elif not crawl_success:
            print("� 由於執行過程中發生錯誤，程式結束但不會執行自動關機")
        else:
            print(f"⚠️ 任務未全部完成 ({processed_count}/{num_total_tasks})，程式結束")


if __name__ == "__main__":
    # 確保 multiprocessing 在 macOS/Windows 上正常運作
    multiprocessing.freeze_support() 
    main()
