"""
gcp_main_unified.py — Unified entry point for TCGweb Health Checker.

Merges Pool mode (from gcp_main_mpfast.py) and Queue mode with memory
monitoring (from gcp_main_mpselfqueue.py) into a single script controlled
by the --mode flag.

Usage examples:
    python gcp_main_unified.py --mode pool --concurrent 4
    python gcp_main_unified.py --mode queue --concurrent 2 --max-mem-mb 512
    python gcp_main_unified.py --no-shutdown --no-email   # local testing
"""

import csv
import os
import sys
import asyncio
import argparse
import time
import subprocess
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

# psutil is only required for queue mode; import lazily so pool mode works
# even when psutil is not installed.
try:
    import psutil
except ImportError:
    psutil = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def load_websites(path: str) -> list[dict]:
    """Load site configurations from a CSV file."""
    websites_config = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            websites_config.append(row)
    return websites_config


def prepare_site_configs(
    websites: list[dict],
    processed_urls: set,
    global_depth: int,
    global_save_html: bool,
    global_enable_pagination: bool,
    max_mem_mb: int,
) -> list[dict]:
    """
    Merge per-site CSV overrides with global CLI defaults and filter out
    already-processed URLs (resume support).
    """
    websites_to_process = []

    for site in websites:
        url = site["URL"]
        if url.strip() in processed_urls:
            continue

        # --- depth ---
        try:
            csv_depth = int(site.get("depth")) if site.get("depth") else None
            if csv_depth is not None and global_depth > csv_depth:
                site_depth = csv_depth
            else:
                site_depth = global_depth
        except (ValueError, TypeError):
            site_depth = global_depth
        site["global_depth"] = site_depth

        # --- save_html ---
        csv_save = site.get("save_html", "").lower()
        if csv_save == "true":
            site["global_save_html"] = True
        elif csv_save == "false":
            site["global_save_html"] = False
        else:
            site["global_save_html"] = global_save_html

        # --- pagination ---
        csv_pag = site.get("pagination", "").lower()
        if csv_pag == "true":
            site["global_enable_pagination"] = True
        elif csv_pag == "false":
            site["global_enable_pagination"] = False
        else:
            site["global_enable_pagination"] = global_enable_pagination

        site["global_max_mem_mb"] = max_mem_mb
        websites_to_process.append(site)

    return websites_to_process


async def _async_crawl_worker(site_config: dict) -> dict | None:
    """
    The actual async crawl worker executed inside a subprocess.
    Shared by both pool and queue modes.
    """
    url = site_config["URL"]
    name = site_config.get("name", "")
    depth = site_config["global_depth"]
    save_html = site_config["global_save_html"]
    enable_pagination = site_config["global_enable_pagination"]

    print(f"\n[PID {os.getpid()}] Start processing: {name or url}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            crawler = WebCrawlerAgent(
                save_html_files=save_html,
                enable_pagination=enable_pagination,
            )

            start_time = time.time()
            crawl_results = await crawler.crawl_site(
                browser, url, name=name, max_depth=depth
            )
            crawl_duration = time.time() - start_time
            crawl_duration_formatted = (
                f"{int(crawl_duration // 60)}m{int(crawl_duration % 60)}s"
            )

            page_summary = crawler.get_page_summary()
            external_link_results = crawler.get_external_link_results()

            # Save JSON / Log
            json_path = crawler.save_page_summary_to_json()
            if json_path:
                extract_error_links_from_json(json_path)
            crawler.save_crawl_log()

            # ---------- compute statistics for Excel ----------
            one_year_ago = datetime.now() - timedelta(days=365)
            total_pages = len(crawl_results)
            failed_pages = sum(
                1 for status in crawl_results if status >= 400 or status == 0
            )
            failed_external_links = sum(
                1
                for link_info in external_link_results.values()
                if link_info.get("status", 0) >= 400
                or link_info.get("status", 0) == 0
            )

            today = datetime.now().date()
            no_date_pages = 0
            outdated_pages = 0
            past_dates: list[datetime] = []
            future_dates: list[datetime] = []

            for _url_key, page_info in page_summary.items():
                last_updated = page_info.get("last_updated", "")

                if (
                    last_updated == "[無日期]"
                    or last_updated == "[爬取失敗]"
                    or not last_updated
                ):
                    no_date_pages += 1
                    continue

                try:
                    update_date = datetime.strptime(last_updated, "%Y-%m-%d")
                    if update_date.date() <= today:
                        past_dates.append(update_date)
                        if update_date < one_year_ago:
                            outdated_pages += 1
                    else:
                        future_dates.append(update_date)
                except ValueError:
                    no_date_pages += 1

            if past_dates:
                latest_update = max(past_dates).strftime("%Y-%m-%d")
            elif future_dates:
                latest_update = min(future_dates).strftime("%Y-%m-%d")
            else:
                latest_update = "無有效日期"

            pages_with_date = len(past_dates) + len(future_dates)
            outdated_percentage = (
                (outdated_pages / pages_with_date * 100) if pages_with_date > 0 else 0
            )

            stats_for_excel = {
                "site_name": name or url,
                "site_url": url,
                "total_pages": total_pages,
                "pages_with_date": pages_with_date,
                "no_date_pages": no_date_pages,
                "latest_update": latest_update,
                "outdated_pages": outdated_pages,
                "outdated_percentage": round(outdated_percentage, 2),
                "failed_pages": failed_pages,
                "failed_external_links": failed_external_links,
                "total_external_links": len(external_link_results),
                "crawl_duration": crawl_duration_formatted,
            }

            # Cleanup
            del page_summary
            del external_link_results
            await crawler.close()
            crawler.clear_memory()
            del crawler
            await browser.close()

            print(f"[PID {os.getpid()}] Done: {name or url}")
            return stats_for_excel

    except Exception as e:
        print(f"[PID {os.getpid()}] Error processing '{name or url}': {e}")
        try:
            if "crawler" in locals() and crawler:
                await crawler.close()
                crawler.clear_memory()
                del crawler
            if "browser" in locals() and browser:
                await browser.close()
            await asyncio.sleep(5)
        except Exception as cleanup_e:
            print(f"[PID {os.getpid()}] Cleanup error: {cleanup_e}")
        return None


# ---------------------------------------------------------------------------
# Progress tracking (Firestore)
# ---------------------------------------------------------------------------

def update_progress(total: int, successful: int, failed: int, status: str = "running"):
    """Write crawl progress to Firestore for the management UI."""
    try:
        from google.cloud import firestore as _fs
        db = _fs.Client()
        db.collection("crawler_progress").document("current").set({
            "total": total,
            "successful": successful,
            "failed": failed,
            "processed": successful + failed,
            "status": status,  # running, completed, error
            "updated_at": _fs.SERVER_TIMESTAMP,
            "month": datetime.now().strftime("%Y-%m"),
        })
    except Exception:
        pass  # Non-critical, don't crash the crawler


# ---------------------------------------------------------------------------
# Pool mode helpers (from gcp_main_mpfast.py)
# ---------------------------------------------------------------------------

def _pool_crawl_task(site_config: dict) -> dict | None:
    """Wrapper for multiprocessing.Pool — creates its own event loop."""
    try:
        return asyncio.run(_async_crawl_worker(site_config))
    except Exception as e:
        print(
            f"[PID {os.getpid()}] Task '{site_config.get('name', 'N/A')}' "
            f"crashed: {e}"
        )
        return None


def run_pool_mode(
    websites_to_process: list[dict],
    concurrent: int,
    reporter: ReportGenerationAgent,
) -> tuple[int, int, bool]:
    """
    Pool mode: multiprocessing.Pool with maxtasksperchild=1.
    Returns (successful_sites, failed_sites, crawl_success).
    """
    print(
        f"\n[Pool mode] Starting {concurrent} workers "
        f"(maxtasksperchild=1)"
    )
    start_time = time.time()
    num_total = len(websites_to_process)
    successful_sites = 0
    failed_sites = 0
    crawl_success = True

    update_progress(num_total, 0, 0, "running")

    try:
        with multiprocessing.Pool(
            processes=concurrent, maxtasksperchild=1
        ) as pool:
            results = pool.imap_unordered(_pool_crawl_task, websites_to_process)

            for stats in results:
                if stats:
                    try:
                        stats["crawl_date"] = datetime.now().strftime(
                            "%Y-%m-%d %H:%M"
                        )
                        reporter.add_site_to_excel(stats)
                        successful_sites += 1
                    except Exception as e:
                        print(f"[Pool] Excel write error: {e}")
                        failed_sites += 1
                else:
                    failed_sites += 1

                update_progress(num_total, successful_sites, failed_sites, "running")

    except Exception as e:
        print(f"\n[Pool] Fatal error: {e}")
        crawl_success = False

    status = "completed" if crawl_success else "error"
    update_progress(num_total, successful_sites, failed_sites, status)

    total_duration = time.time() - start_time
    print(
        f"\n[Pool] Finished in "
        f"{int(total_duration // 60)}m{int(total_duration % 60)}s  "
        f"(success={successful_sites}, failed={failed_sites})"
    )
    return successful_sites, failed_sites, crawl_success


# ---------------------------------------------------------------------------
# Queue mode helpers (from gcp_main_mpselfqueue.py)
# ---------------------------------------------------------------------------

def _worker_process_loop(
    worker_id: int,
    task_queue: Queue,
    result_queue: Queue,
    max_mem_mb: int,
):
    """
    Custom worker loop with psutil memory monitoring.
    Sends RESTART signal when RSS exceeds max_mem_mb.
    """
    if psutil is None:
        print(
            f"[Worker {worker_id}] psutil is not installed — "
            "memory monitoring disabled"
        )
        proc = None
    else:
        proc = psutil.Process(os.getpid())

    print(f"[Worker {worker_id} | PID {os.getpid()}] Started")

    while True:
        try:
            # Memory check
            if proc is not None:
                memory_mb = proc.memory_info().rss / 1024 / 1024
                if memory_mb > max_mem_mb:
                    print(
                        f"[Worker {worker_id} | PID {os.getpid()}] "
                        f"Memory {memory_mb:.1f} MB > {max_mem_mb} MB, "
                        f"requesting restart"
                    )
                    result_queue.put(("RESTART", worker_id))
                    break

            # Fetch task
            try:
                site_config = task_queue.get(timeout=5.0)
            except Empty:
                print(
                    f"[Worker {worker_id} | PID {os.getpid()}] "
                    "Queue empty, exiting"
                )
                break

            if site_config is None:
                print(
                    f"[Worker {worker_id} | PID {os.getpid()}] "
                    "Received stop signal, exiting"
                )
                break

            # Execute crawl
            try:
                stats = asyncio.run(_async_crawl_worker(site_config))
                result_queue.put(stats)
                print(
                    f"[Worker {worker_id} | PID {os.getpid()}] "
                    f"Completed: {site_config.get('name', 'N/A')}"
                )
            except Exception as e:
                print(
                    f"[Worker {worker_id} | PID {os.getpid()}] "
                    f"Task error '{site_config.get('name', 'N/A')}': {e}"
                )
                result_queue.put(("FAILED", site_config.get("name", "N/A")))

        except Exception as loop_e:
            print(
                f"[Worker {worker_id} | PID {os.getpid()}] "
                f"Loop error: {loop_e}"
            )
            result_queue.put(("RESTART", worker_id))
            break

    print(f"[Worker {worker_id} | PID {os.getpid()}] Exited")


def run_queue_mode(
    websites_to_process: list[dict],
    concurrent: int,
    max_mem_mb: int,
    reporter: ReportGenerationAgent,
) -> tuple[int, int, bool]:
    """
    Queue mode: manual Process + Queue with psutil memory monitoring.
    Returns (successful_sites, failed_sites, crawl_success).
    """
    if psutil is None:
        print(
            "[Queue mode] WARNING: psutil is not installed. "
            "Memory monitoring will be disabled."
        )

    num_total_tasks = len(websites_to_process)

    task_queue: Queue = Queue()
    result_queue: Queue = Queue()

    for cfg in websites_to_process:
        task_queue.put(cfg)
    for _ in range(concurrent):
        task_queue.put(None)  # stop sentinels

    worker_pool: dict[int, Process] = {}

    def start_new_worker(wid: int):
        p = Process(
            target=_worker_process_loop,
            args=(wid, task_queue, result_queue, max_mem_mb),
        )
        p.start()
        worker_pool[wid] = p

    print(f"\n[Queue mode] Starting {concurrent} workers (max_mem={max_mem_mb} MB)")
    start_time = time.time()

    for i in range(concurrent):
        start_new_worker(i)

    successful_sites = 0
    failed_sites = 0
    processed_count = 0
    crawl_success = True

    update_progress(num_total_tasks, 0, 0, "running")

    try:
        while processed_count < num_total_tasks:
            try:
                result = result_queue.get(timeout=600.0)

                # RESTART signal
                if isinstance(result, tuple) and result[0] == "RESTART":
                    wid = result[1]
                    print(f"[Main] Worker {wid} requested restart")
                    if wid in worker_pool:
                        old = worker_pool.pop(wid)
                        if old.is_alive():
                            old.join(timeout=10)
                            if old.is_alive():
                                old.terminate()
                                old.join()
                    start_new_worker(wid)

                # FAILED signal
                elif isinstance(result, tuple) and result[0] == "FAILED":
                    failed_sites += 1
                    processed_count += 1
                    update_progress(num_total_tasks, successful_sites, failed_sites, "running")
                    print(
                        f"[Progress] {processed_count}/{num_total_tasks} "
                        f"(ok={successful_sites}, fail={failed_sites})"
                    )

                # Success
                else:
                    if result is not None:
                        try:
                            result["crawl_date"] = datetime.now().strftime(
                                "%Y-%m-%d %H:%M"
                            )
                            reporter.add_site_to_excel(result)
                            successful_sites += 1
                        except Exception as e:
                            print(f"[Queue] Excel write error: {e}")
                            failed_sites += 1
                    else:
                        failed_sites += 1

                    processed_count += 1
                    update_progress(num_total_tasks, successful_sites, failed_sites, "running")
                    print(
                        f"[Progress] {processed_count}/{num_total_tasks} "
                        f"(ok={successful_sites}, fail={failed_sites})"
                    )

            except Empty:
                print("[Main] No results for 10 min, checking workers...")
                all_dead = True
                for wid, p in worker_pool.items():
                    if p.is_alive():
                        print(f"  Worker {wid} (PID {p.pid}) alive")
                        all_dead = False
                if all_dead:
                    print("[Main] All workers dead — aborting")
                    crawl_success = False
                    break

    except Exception as e:
        print(f"\n[Main] Fatal error: {e}")
        crawl_success = False
        for p in worker_pool.values():
            if p.is_alive():
                p.terminate()
                p.join()

    status = "completed" if crawl_success else "error"
    update_progress(num_total_tasks, successful_sites, failed_sites, status)

    total_duration = time.time() - start_time
    print(
        f"\n[Queue] Finished in "
        f"{int(total_duration // 60)}m{int(total_duration % 60)}s  "
        f"(success={successful_sites}, failed={failed_sites})"
    )
    return successful_sites, failed_sites, crawl_success


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def get_recipients_from_firestore() -> list[str] | None:
    """
    Try to read email recipients from Firestore collection `email_recipients`.
    Returns a list of email addresses, or None if Firestore is unavailable.
    Each document in the collection is expected to have an `email` field.
    """
    try:
        from google.cloud import firestore  # type: ignore

        db = firestore.Client()
        docs = db.collection("email_recipients").stream()
        recipients = []
        for doc in docs:
            data = doc.to_dict()
            email = data.get("email")
            if email:
                recipients.append(email)
        if recipients:
            print(f"[Email] Loaded {len(recipients)} recipients from Firestore")
            return recipients
        print("[Email] Firestore collection empty, falling back to .env")
        return None
    except Exception as e:
        print(f"[Email] Firestore unavailable ({e}), falling back to .env")
        return None


def pack_and_send_email(excel_report_path: str):
    """Pack the report and send via EmailReporter."""
    print("[Email] Packing and sending report...")
    try:
        # Attempt to fetch recipients from Firestore
        recipients = get_recipients_from_firestore()

        email_reporter = EmailReporter()

        # If Firestore returned recipients, override the reporter's list
        if recipients:
            email_reporter.to_emails = recipients

        success = email_reporter.pack_and_send_seperate(excel_report_path)

        if success:
            print("[Email] Report sent successfully")
        else:
            print("[Email] Sending failed, but execution continues")
    except Exception as e:
        print(f"[Email] Error: {e}")
        print("[Email] Sending failed, but execution continues")


# ---------------------------------------------------------------------------
# GCS upload
# ---------------------------------------------------------------------------

GCS_BUCKET = "doit-dic-itteam-crawler-reports"


def upload_reports_to_gcs(excel_path: str, assets_dir: str = "assets"):
    """Upload Excel report and per-site JSON/CSV files to GCS."""
    try:
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)

        # Organize by year-month
        month_prefix = datetime.now().strftime("%Y-%m")

        uploaded = 0

        # Upload Excel report
        if os.path.exists(excel_path):
            blob_name = f"{month_prefix}/{os.path.basename(excel_path)}"
            blob = bucket.blob(blob_name)
            blob.upload_from_filename(excel_path)
            uploaded += 1
            print(f"[GCS] Uploaded: {blob_name}")

        # Upload per-site JSON and CSV from assets/
        if os.path.exists(assets_dir):
            for site_folder in os.listdir(assets_dir):
                site_path = os.path.join(assets_dir, site_folder)
                if not os.path.isdir(site_path):
                    continue
                for filename in os.listdir(site_path):
                    if filename.endswith((".json", ".csv", ".txt")):
                        local_path = os.path.join(site_path, filename)
                        blob_name = f"{month_prefix}/sites/{site_folder}/{filename}"
                        blob = bucket.blob(blob_name)
                        blob.upload_from_filename(local_path)
                        uploaded += 1

        print(f"[GCS] Upload complete: {uploaded} files → gs://{GCS_BUCKET}/{month_prefix}/")

    except ImportError:
        print("[GCS] google-cloud-storage not installed, skipping upload")
    except Exception as e:
        print(f"[GCS] Upload error: {e}")


# ---------------------------------------------------------------------------
# VM shutdown
# ---------------------------------------------------------------------------

def auto_shutdown_vm(vm_name: str = "crawler-webcheck", zone: str = "asia-east1-c"):
    """Shut down the GCE VM instance."""
    try:
        print(f"[Shutdown] Stopping VM {vm_name} in {zone}")
        shutdown_cmd = (
            f"gcloud compute instances stop {vm_name} --zone={zone} --quiet"
        )
        result = subprocess.run(
            shutdown_cmd.split(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            print("[Shutdown] VM stopped successfully")
        else:
            print(f"[Shutdown] Failed: {result.stderr}")
    except Exception as e:
        print(f"[Shutdown] Error: {e} — VM will remain running")


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TCGweb Health Checker — unified multiprocessing entry point",
    )
    parser.add_argument(
        "--mode",
        choices=["pool", "queue"],
        default="pool",
        help="Execution mode: 'pool' uses multiprocessing.Pool (simpler), "
        "'queue' uses manual Process+Queue with memory monitoring (default: pool)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=2,
        help="Maximum crawl depth (default: 2)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/websites.csv",
        help="Path to website CSV config (default: config/websites.csv)",
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=2,
        help="Number of parallel workers (default: 2)",
    )
    parser.add_argument(
        "--no-save-html",
        action="store_true",
        help="Skip saving HTML files (faster, less disk usage)",
    )
    parser.add_argument(
        "--no-pagination",
        action="store_true",
        help="Disable pagination crawling (treat paginated URLs as duplicates)",
    )
    parser.add_argument(
        "--max-mem-mb",
        type=int,
        default=1024,
        help="Per-worker memory limit in MB for queue mode (default: 1024)",
    )
    parser.add_argument(
        "--vm-name",
        type=str,
        default="crawler-webcheck",
        help="GCE VM name for auto-shutdown (default: crawler-webcheck)",
    )
    parser.add_argument(
        "--no-shutdown",
        action="store_true",
        help="Disable automatic VM shutdown after completion",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Disable email sending after completion",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip Firestore sync on startup (use local CSV and .env as-is)",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    global_depth = args.depth
    global_save_html = not args.no_save_html
    global_enable_pagination = not args.no_pagination

    # --- Firestore 同步：開機時拉取最新的網站清單和收件人 ---
    if not args.no_sync:
        try:
            from utils.sync_firestore import sync_websites, sync_recipients
            print("\n[Sync] 從 Firestore 同步設定...")
            sync_websites(args.config)
            sync_recipients()
            print()
        except Exception as e:
            print(f"[Sync] 同步失敗 ({e})，使用本地檔案繼續")

    # Validate config path
    if not os.path.exists(args.config):
        print(f"Error: config file not found: {args.config}")
        sys.exit(1)

    # Load websites
    websites = load_websites(args.config)
    print(
        f"Loaded {len(websites)} websites | depth={global_depth} | "
        f"concurrent={args.concurrent} | mode={args.mode}"
    )
    print(f"  save_html={'ON' if global_save_html else 'OFF'}")
    print(f"  pagination={'ON' if global_enable_pagination else 'OFF'}")

    # Initialize reporter
    reporter = ReportGenerationAgent()
    output_path = reporter.initialize_excel_report()
    print(f"Excel report initialized: {output_path}")

    processed_urls = reporter.get_processed_urls()

    # Prepare site configs (resume-aware)
    websites_to_process = prepare_site_configs(
        websites,
        processed_urls,
        global_depth,
        global_save_html,
        global_enable_pagination,
        args.max_mem_mb,
    )

    print(
        f"Total: {len(websites)} sites, remaining: "
        f"{len(websites_to_process)} to process"
    )

    if not websites_to_process:
        print("All websites already processed!")
        reporter.finalize_excel_report()
        print(f"Report saved: {output_path}")
        if not args.no_email:
            pack_and_send_email(output_path)
        if not args.no_shutdown:
            auto_shutdown_vm(args.vm_name)
        return

    # ---- Run selected mode ----
    if args.mode == "pool":
        successful, failed, crawl_ok = run_pool_mode(
            websites_to_process, args.concurrent, reporter
        )
    else:
        successful, failed, crawl_ok = run_queue_mode(
            websites_to_process, args.concurrent, args.max_mem_mb, reporter
        )

    # ---- Finalize ----
    reporter.finalize_excel_report()
    print(f"\nReport saved: {output_path}")
    print(f"Results: {successful} succeeded, {failed} failed")

    if crawl_ok:
        upload_reports_to_gcs(output_path)
        if not args.no_email:
            pack_and_send_email(output_path)
        if not args.no_shutdown:
            auto_shutdown_vm(args.vm_name)
    else:
        # 即使失敗也上傳已有的報告，方便除錯
        upload_reports_to_gcs(output_path)
        print(
            "Errors occurred during execution — "
            "skipping email/shutdown to allow debugging"
        )


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
