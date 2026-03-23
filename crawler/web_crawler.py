import re
import asyncio
import os
import json
from collections import deque
from dataclasses import dataclass
from typing import Dict
from urllib.parse import urljoin, urlparse, parse_qs
from datetime import datetime

import httpx
from playwright.async_api import Browser, BrowserContext
from bs4 import BeautifulSoup

from analyzer.date_extraction import extract_last_updated
from utils.log_writer import LogWriter


@dataclass
class CrawlResult:
    url: str
    status: int  # HTTP status or 0 timeout
    html: str      # HTML content or error message
    last_updated: str
    link_status: Dict[str, int] # external link statuses
    depth: int = 0
    parent_url: str = ""
    page_title: str = ""  # 儲存頁面的標題
    saved_filepath: str = ""  # 儲存檔案的路徑


class WebCrawlerAgent:
    def __init__(self, timeout: int = 15, save_html_files: bool = True, enable_pagination: bool = True):
        self.timeout = timeout
        self.save_html_files = save_html_files  
        self.enable_pagination = enable_pagination  
        self.log_writer = None  # 將在 crawl_site 中初始化
        
        # 建立 httpx client
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-User': '?1',
            'Sec-Fetch-Dest': 'document',
        }

        # 並行處理的httpx連接池設定
        self.client = httpx.AsyncClient(
            timeout=self.timeout, 
            follow_redirects=True,
            verify=False,  # 忽略 SSL 憑證錯誤，解決某些政府網站連線問題
            headers=headers,  # 添加標頭以改善連線成功率和避免 403 錯誤
            limits=httpx.Limits(
                max_connections=20,        # 增加最大連接數以支持並行
                max_keepalive_connections=10,  # 保持連接以提高效率
                keepalive_expiry=30.0      # 連接保持時間
            )
        )

        # 用於追蹤頁面資訊的字典
        self.page_info_dict = {}  # {url: {"title": "中文標題", "last_updated": "2024-01-01", "filepath": "assets/xxx.html",
                                  # "status": 200, "depth": 0, "source_page": source_page_info}}

        # 追蹤已測試的外部連結及其來源頁面（每個連結只記錄一次來源頁面）
        self.external_link_results = {}  # {url: {"status": status_code, "source_page": {"title": "頁面標題", "url": "頁面URL"}}}

    def _log(self, message: str):
        """統一的日誌記錄方法，只記錄到 log，不輸出到 terminal"""
        if self.log_writer:
            self.log_writer.log_only(message)

    def _get_content_preview(self, html: str) -> str:
        """提取HTML內容的前500個純文字字元用於內容比較"""
        if not html:
            return ""
        
        try:
            # 解析HTML並提取純文字
            soup = BeautifulSoup(html, "html.parser")
            for script in soup(["script", "style"]):
                script.decompose()
            text = soup.get_text()
            text = ' '.join(text.split())
            return text[:500]  # 返回前500個字元
        except Exception:
            # 如果解析失敗，直接使用原始HTML的前500個字元
            return html[:500]

    def _compare_page_content(self, current_html: str, existing_url: str) -> bool:
        """比較當前頁面和已存在頁面的內容，如果有儲存HTML則比較前500個字元"""
        if not self.save_html_files:
            return False  # 沒有儲存HTML，無法比較內容
        
        # 從page_info_dict獲取已存在頁面的檔案路徑
        existing_info = self.page_info_dict.get(existing_url)
        if not existing_info or not existing_info.get('filepath'):
            return False
        
        existing_filepath = existing_info['filepath']
        if not os.path.exists(existing_filepath):
            return False
        
        try:
            # 讀取已存在的HTML檔案
            with open(existing_filepath, 'r', encoding='utf-8') as f:
                existing_html = f.read()
            
            # 比較兩個頁面的前500個字元
            current_preview = self._get_content_preview(current_html)
            existing_preview = self._get_content_preview(existing_html)
            
            return current_preview == existing_preview
        except Exception as e:
            self._log(f"    [Content Compare] Error comparing content: {e}")
            return False

    async def _detect_and_render_spa(self, page, depth: int) -> dict:
        """
        Detects SPA framework in browser context and applies appropriate rendering waits.
        Also checks for Frameset pages.
        Returns: {
            "type": "frameset" | "spa" | "static",
            "framework": str (for spa type),
            "links": list (for frameset type)
        }
        """
        # 步驟 1: 優先檢查 Frameset
        initial_html = await page.content()
        soup = BeautifulSoup(initial_html, "html.parser")
        frames = soup.find_all("frame")
        if frames:
            self._log(f"{'  ' * (depth+1)}-> [Legacy Site] Frameset detected.")
            frame_links = [urljoin(page.url, f.get("src")) for f in frames if f.get("src")]
            return {"type": "frameset", "links": frame_links}

        # 步驟 2: 在瀏覽器上下文中執行 JS 進行 SPA 框架檢測
        # 這是最可靠的方法，因為它檢查的是運行時的環境
        framework = await page.evaluate('''
            () => {
                // React
                if (window.React || window.__REACT_DEVTOOLS_GLOBAL_HOOK__ || document.querySelector('[data-reactroot], #__next')) {
                    return 'React';
                }
                // Vue
                if (window.Vue || window.__VUE__ || document.querySelector('[data-v-app], #__nuxt')) {
                    return 'Vue';
                }
                // Angular
                if (window.angular || document.querySelector('.ng-version, [ng-version], app-root')) {
                    return 'Angular';
                }
                // Gatsby (特殊檢測，放最後) 已註解
                //if (document.querySelector('#___gatsby') || (document.querySelector('meta[name="generator"]') && document.querySelector('meta[name="generator"]').content.startsWith('Gatsby'))) {
                //    return 'React';
                //}
                return 'Static'; // 如果都沒找到，就認為是靜態頁面
            }
        ''')

        # 步驟 3: 根據檢測結果決定是否需要額外等待
        if framework != 'Static':
            self._log(f"{'  ' * (depth+1)}-> Detected {framework} application, applying extended wait for rendering...")
            try:
                # 對於 SPA，我們給予更多的時間來渲染。
                # 'networkidle' 是個好信號，表示 AJAX 請求可能已完成。
                await page.wait_for_load_state('networkidle', timeout=5000)
                self._log(f"{'  ' * (depth+1)}-> {framework} content rendering likely complete.")
            except Exception as e:
                # 即使等待超時，我們也繼續，因為頁面可能已經部分渲染
                self._log(f"{'  ' * (depth+1)}-> {framework} network idle wait timed out or failed: {type(e).__name__}, proceeding with current content.")
            return {"type": "spa", "framework": framework}
        else:
            self._log(f"{'  ' * (depth+1)}-> Static page detected. No extra wait needed.")
            return {"type": "static"}

    async def close(self):
        """Close the httpx client"""
        await self.client.aclose()

    def clear_memory(self):
        """
        手動清除內部的大型字典和變數，以協助垃圾回收(GC)
        """
        # 就地清空字典，確保所有參考都指向空字典
        self.page_info_dict.clear()
        self.external_link_results.clear()

    def get_page_summary(self) -> dict:
        """返回爬蟲結果的摘要字典"""
        return self.page_info_dict
    
    def get_external_link_results(self) -> dict:
        """返回外部連結測試結果"""
        return self.external_link_results
    
    def save_crawl_log(self):
        """完成 log 寫入並關閉 log_writer"""
        if not self.log_writer:
            return None
            
        try:
            # 關閉 LogWriter 並獲取 log 檔案路徑
            log_path = self.log_writer.get_log_file_path()
            self.log_writer.close()
            
            return log_path
        except Exception as e:
            print(f"儲存爬蟲 log 失敗: {e}")
            return None

    def save_page_summary_to_json(self, filename: str = "page_summary.json"):
        """
        將頁面摘要字典儲存為JSON檔案，並按日期排序
        """
        page_summary = self.page_info_dict
        external_link_results = self.external_link_results
        
        # 過濾和分類項目
        items_with_date = []
        items_without_date = []
        items_no_date = []  # 專門存放標記為 [無日期] 的項目
        items_failed = []   # 新增：專門存放爬取失敗的項目
        
        for url, info in page_summary.items():
            if info.get('last_updated') == "[爬取失敗]":
                items_failed.append((url, info))
            elif info.get('last_updated') == "[無日期]":
                items_no_date.append((url, info))
            elif info.get('last_updated'):
                try:
                    # 確保日期格式正確以進行排序
                    datetime.strptime(info['last_updated'], "%Y-%m-%d")
                    items_with_date.append((url, info))
                except (ValueError, TypeError):
                    items_without_date.append((url, info))
            else:
                items_without_date.append((url, info))

        # 按日期由新到舊排序有日期的項目
        sorted_items = sorted(items_with_date, key=lambda item: item[1]['last_updated'], reverse=True)
        
        # 將所有項目按順序合併：有日期的 -> 無法解析的 -> 標記為無日期的 -> 爬取失敗的
        sorted_summary = dict(sorted_items + items_without_date + items_no_date + items_failed)
        
        # 如果有外部連結結果，加入到摘要中並排序
        final_data = {
            "page_summary": sorted_summary
        }
        if external_link_results:
            # 按狀態碼排序外部連結：正常(2xx) -> 重定向(3xx) -> 客戶端錯誤(4xx) -> 伺服器錯誤(5xx) -> 連線錯誤(0)
            sorted_external_links = dict(sorted(
                external_link_results.items(),
                key=lambda item: (
                    0 if 200 <= item[1]["status"] < 300 else  # 正常狀態優先
                    1 if 300 <= item[1]["status"] < 400 else  # 重定向其次
                    2 if 400 <= item[1]["status"] < 500 else  # 客戶端錯誤
                    3 if item[1]["status"] >= 500 else        # 伺服器錯誤
                    4,                                        # 連線錯誤 (0) 最後
                    item[0]                                   # 同狀態類型內按URL字母排序
                )
            ))
            final_data["external_links"] = sorted_external_links

        try:
            # 直接使用當前的基礎輸出目錄（與log相同的資料夾）
            os.makedirs(self.current_base_output_dir, exist_ok=True)
            full_path = os.path.join(self.current_base_output_dir, filename)
            
            with open(full_path, 'w', encoding='utf-8') as f:
                json.dump(final_data, f, ensure_ascii=False, indent=2)
            self._log(f"頁面摘要和外部連結測試結果已儲存到: {full_path}")
            return full_path
        except Exception as e:
            self._log(f"儲存失敗: {e}")
            return None

    async def check_link_status(self, link: str) -> tuple[str, int]:
        """
        Helper to check a single link's status using httpx.
        It first tries a HEAD request for efficiency. If it fails with a 404, 405, or TooManyRedirects,
        it falls back to a GET request to confirm, as some servers don't handle HEAD correctly.
        For HTTP links that fail, it automatically retries with HTTPS.
        """
        
        async def try_link_check(url: str) -> tuple[str, int]:
            """內部函數：嘗試檢查單一連結"""
            try:
                response = await self.client.head(url)
                
                # Fallback to GET to double-check.
                if response.status_code in [403, 404, 405]:
                    self._log(f"    [Link Check] HEAD for {url} returned {response.status_code}. Falling back to GET.")
                    response = await self.client.get(url)

                return url, response.status_code
            except httpx.TooManyRedirects:
                # Some servers have issues with HEAD redirects, fallback to GET
                self._log(f"    [Link Check] HEAD for {url} caused TooManyRedirects. Falling back to GET.")
                try:
                    response = await self.client.get(url)
                    return url, response.status_code
                except Exception as e:
                    self._log(f"    [Link Check Error] GET fallback failed for {url}: {type(e).__name__}")
                    raise e  # 重新拋出異常讓外層處理
            except Exception as e:
                # 所有其他異常都重新拋出讓外層處理
                raise e
        
        try:
            result_url, status = await try_link_check(link)
            return link, status  # 返回原始 URL
        except Exception as e:
            self._log(f"    [Link Check Error] {link}: {type(e).__name__}")
            
            # 如果是 HTTP 連結，嘗試 HTTPS
            if link.startswith('http://'):
                https_link = link.replace('http://', 'https://', 1)
                self._log(f"    [Link Check] HTTP failed, trying HTTPS: {https_link}")
                
                try:
                    result_url, status = await try_link_check(https_link)
                    self._log(f"    [Link Check] ✓ HTTPS connection successful for {https_link}")
                    return link, status  # 返回原始 HTTP URL，不是 HTTPS URL
                except Exception as https_e:
                    self._log(f"    [Link Check Error] HTTPS also failed for {https_link}: {type(https_e).__name__}")
            
            return link, 0

    def _find_sitemap_link(self, soup: BeautifulSoup, base_url: str, actual_url: str = None) -> str | None:
        """在頁面中尋找網站導覽(sitemap)、網頁導覽或webpage的連結"""
        # 使用實際載入的 URL 作為基準，如果沒有提供則使用原始 URL
        reference_url = actual_url if actual_url else base_url
        
        # 查找所有連結
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").lower()
            # 跳過純錨點
            if href.startswith("#"):
                continue
            title = a.get("title", "").lower()
            text = a.get_text(strip=True).lower()
            
            # 檢查關鍵字：sitemap、網站導覽、網頁導覽、webmap
            if (
                "sitemap" in href or "sitemap" in title or "sitemap" in text or
                "網站導覽" in title or "網站導覽" in text or
                "網頁導覽" in title or "網頁導覽" in text or
                "webmap" in href or "webmap" in title or "webmap" in text
            ):
                # 使用實際的頁面 URL 來組合絕對路徑
                sitemap_url = urljoin(reference_url, a["href"])
                self._log(f"    [Sitemap] Found sitemap/webpage link: {sitemap_url}")
                return sitemap_url.split('#')[0] # 移除 fragment
        return None

    def _extract_links_from_sitemap(self, html: str, sitemap_url: str) -> set[str]:
        """從 sitemap 頁面的 HTML 內容中提取主內容區域的連結"""
        self._log(f"  [Sitemap] Extracting links from sitemap HTML content")
        
        soup = BeautifulSoup(html, "html.parser")
        internal_links = set()
        
        try:
            # 按照優先級排序的選擇器策略
            main_content_selectors = [
                # 第1優先級：標準語義標籤
                'main', '[role="main"]',
                
                # 第2優先級：常見的完整匹配
                '#main', '#content', '#main-content', '#index_main',
                '.main', '.content', '.main-content', '.main_content', '.article',
                
                # 第3優先級：政府網站和CMS常見模式
                '#CCMS_Content', '.group.page-content',
                
                # 第4優先級：ID 部分匹配
                '[id*="main"]', '[id*="content"]', '[id*="index"]',
                
                # 第5優先級：Class 部分匹配
                '[class*="main"]', '[class*="content"]', '[class*="article"]'
            ]
            
            main_content = None
            
            # 按優先級順序檢查選擇器
            for selector in main_content_selectors:
                try:
                    elements = soup.select(selector)
                    if elements:
                        candidate = elements[0]
                        links = candidate.find_all("a", href=True)
                        
                        if len(links) >= 1:
                            main_content = candidate
                            self._log(f"    [Sitemap] Found main content using: {selector} ({len(links)} links)")
                            break
                except Exception:
                    continue
            
            # 如果找不到主內容區域，返回sitemap頁面本身讓正常爬取流程處理
            if not main_content:
                self._log(f"    [Sitemap] No main content found, will crawl sitemap page normally")
                return {sitemap_url}
            
            # 從主內容區域提取連結
            base_domain = urlparse(sitemap_url).netloc
            for a in main_content.find_all("a", href=True):
                href = a["href"]
                if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                    continue
                
                link = urljoin(sitemap_url, href)
                if urlparse(link).netloc == base_domain:
                    internal_links.add(link.split('#')[0])
            
            self._log(f"    [Sitemap] Extracted {len(internal_links)} links from main content")
            
            # 只要找到任何連結就返回
            if len(internal_links) == 0:
                self._log(f"    [Sitemap] No links found, will crawl sitemap page normally")
                return {sitemap_url}
            
            return internal_links
            
        except Exception as e:
            self._log(f"    [Sitemap] Error extracting links: {e}")
            return {sitemap_url}  # 錯誤時返回sitemap本身

    def _sanitize_name(self, name: str, is_dir: bool = False) -> str:
        """Sanitizes a string to be a valid filename or directory name."""
        # Replace invalid characters with an underscore
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        # Clean up multiple underscores and dashes
        name = re.sub(r'[_\-\s]+', '_', name)  # Replace sequences of _, -, spaces with single _
        # Strip leading/trailing whitespace/underscores
        name = name.strip(' _')
        # Limit length to avoid issues with long file names
        name = name[:150]
        
        if is_dir:
            return f"{name}_links"
        else:
            # If it looks like there's already an extension, don't add another.
            if '.' in name.split('/')[-1]:
                return name
            return f"{name}.html"

    def _get_save_directory(self, url: str, parent_url: str, base_output_dir: str, 
                            url_to_dir_map: Dict[str, str], url_to_title_map: Dict[str, str]) -> str:
        """Determines the save directory for a URL based on its parent's title."""
        if not self.save_html_files:
            # 如果不儲存HTML檔案，仍需要基本的資料夾結構（用於JSON和log）
            # 但只創建網站根目錄，不創建子目錄
            os.makedirs(base_output_dir, exist_ok=True)
            return base_output_dir
        
        if not parent_url:  # Root URL
            page_dir = base_output_dir
        else:
            parent_dir = url_to_dir_map.get(parent_url, base_output_dir)
            # Use parent's title for the directory name, fallback to a sanitized URL part
            parent_title = url_to_title_map.get(parent_url, urlparse(parent_url).path.split('/')[-1] or "page")
            dir_name = self._sanitize_name(parent_title, is_dir=True)
            page_dir = os.path.join(parent_dir, dir_name)
        
        os.makedirs(page_dir, exist_ok=True)
        url_to_dir_map[url] = page_dir
        return page_dir

    def _save_page_content(self, html: str, page_title: str, page_dir: str) -> str:
        """Saves page content to a file with conflict resolution."""
        if not self.save_html_files:
            # 如果不儲存HTML檔案，返回一個虛擬路徑
            return f"[未儲存] {page_title}.html"
        
        filename = self._sanitize_name(page_title)
        base_filename = filename
        counter = 1
        full_filepath = os.path.join(page_dir, filename)
        
        while os.path.exists(full_filepath):
            name_without_ext, ext = os.path.splitext(base_filename)
            filename = f"{name_without_ext}_{counter}{ext}"
            full_filepath = os.path.join(page_dir, filename)
            counter += 1
        
        with open(full_filepath, "w", encoding="utf-8") as f:
            f.write(html)
        
        return full_filepath

    def _record_page_info(self, actual_url: str, page_title: str, last_updated: str, 
                         saved_filepath: str, status: int, depth: int, 
                         parent_url: str, url_to_title_map: Dict[str, str]):
        """Records page information in the page_info_dict."""
        source_page_info = None
        if parent_url:
            parent_title = url_to_title_map.get(parent_url, "")
            source_page_info = {"title": parent_title, "url": parent_url}
        
        self.page_info_dict[actual_url] = {
            "title": page_title,
            "last_updated": last_updated,
            "filepath": saved_filepath,
            "status": status,
            "depth": depth,
            "source_page": source_page_info
        }

    async def _extract_and_check_links(self, soup: BeautifulSoup, actual_url: str, 
                                     page_title: str, url: str, depth: int) -> tuple[set[str], Dict[str, int]]:
        """Extracts internal links and checks external links, returns both."""
        base_domain = urlparse(actual_url).netloc
        internal_links = set()
        external_links = []
        
        # 提取所有連結
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                continue
            
            try:
                link = urljoin(actual_url, href)
                link_domain = urlparse(link).netloc
                clean_link = link.split('#')[0]  # Remove fragment
                
                if link_domain == base_domain:
                    internal_links.add(clean_link)
                else:
                    external_links.append(clean_link)
                    
            except Exception as e:
                # _record_page_info 記錄錯誤連結
                error_info = f"[LINK_ERROR] {href} - {type(e).__name__}: {str(e)}"
                self._log(f"{'  ' * (depth+1)}! Link parsing error: {error_info}")
                
                # 使用原始連結作為 URL
                self._record_page_info(href, error_info, "[爬取失敗]", "", 0, depth+1, url, {url: page_title})
                continue
        
        # 檢查外部連結狀態
        links_to_check = []
        unique_external_links = list(set(external_links))  # 去重複
        
        # 為所有外部連結記錄來源頁面資訊
        source_page_info_for_links = {"title": page_title, "url": url}
        
        for link in unique_external_links:
            if link not in self.external_link_results:
                links_to_check.append(link)
                self.external_link_results[link] = {"status": 0, "source_page": source_page_info_for_links}
        
        external_link_status = {}
        if links_to_check:
            self._log(f"{'  ' * (depth+1)}-> Checking {len(links_to_check)} external links (total external: {len(unique_external_links)})...")
            tasks = [self.check_link_status(link) for link in links_to_check]
            link_status_results = await asyncio.gather(*tasks)
            
            # 將結果儲存到全域外部連結結果字典
            for link, link_status in link_status_results:
                self.external_link_results[link]["status"] = link_status
            
            # 為當前頁面準備連結狀態字典
            for link in unique_external_links:
                if link in self.external_link_results:
                    external_link_status[link] = self.external_link_results[link]["status"]
        else:
            if unique_external_links:
                self._log(f"{'  ' * (depth+1)}-> Found {len(unique_external_links)} external links, all already tested")
                # 從已有結果中獲取狀態
                external_link_status = {link: self.external_link_results[link]["status"] for link in unique_external_links if link in self.external_link_results}
            else:
                self._log(f"{'  ' * (depth+1)}-> No external links found to check")
        
        return internal_links, external_link_status

    async def _crawl_single_page(self, context: BrowserContext, url: str, parent_url: str, base_output_dir: str, 
                               url_to_dir_map: Dict[str, str], url_to_title_map: Dict[str, str], 
                               depth: int) -> tuple[CrawlResult, set[str], str, str]:
        """Crawls a single page using a BrowserContext, saves it using its title, and extracts links.
        Returns: (CrawlResult, internal_links, page_title, actual_url)
        """
        
        # 檢查是否為下載檔案或媒體檔案（PDF、DOC、XLS、圖片、影片等）
        skip_extensions = {
            # 文件檔案
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ods', '.odt', '.ppt', '.pptx',
            '.zip', '.rar', '.7z', '.tar', '.gz',
            # 圖片檔案
            '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico',
            # 影片檔案
            '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv',
            # 音訊檔案
            '.mp3', '.wav', '.flac', '.aac', '.ogg',
            # 其他常見檔案
            '.txt', '.csv', '.json', '.xml'
        }
        
        # 檢查 URL 路徑和查詢參數中是否包含跳過的擴展名
        parsed_url = urlparse(url)
        url_path = parsed_url.path.lower()
        url_query = parsed_url.query.lower()
        
        path_has_skip_ext = any(url_path.endswith(ext) for ext in skip_extensions)
        
        query_has_skip_ext = any(ext in url_query for ext in skip_extensions)
        
        if path_has_skip_ext or query_has_skip_ext:
            # 對於這些檔案，不執行實際爬取，返回特殊標記
            filename = url.split('/')[-1] or "skipped_file"
            file_type = "media/download file"
            if query_has_skip_ext:
                file_type += " (detected in query params)"
            self._log("")
            self._log(f"{'  ' * depth}Skipping {file_type} (depth {depth}): {url}")
            return CrawlResult(url, 200, "[SKIPPED_FILE]", "", {}, depth, parent_url, filename, ""), set(), filename, url
   
        page = await context.new_page()
        internal_links = set()
        status = 0 
        page_title = ""
        saved_filepath = ""
        actual_url = url  # 記錄實際的 URL（重定向後）
        
        page_dir = self._get_save_directory(url, parent_url, base_output_dir, url_to_dir_map, url_to_title_map)
        
        try:
            self._log("")
            self._log(f"{'  ' * depth}Crawling (depth {depth}): {url}")
            response = await page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")
            status = response.status if response else 0
            if response == 0 or status >= 400:
                raise Exception(f"Page returned status {status}")

            # 獲取實際的 URL（重定向後）
            actual_url = page.url
            if actual_url != url:
                self._log(f"{'  ' * (depth+1)}-> Redirected to: {actual_url}")

            # SPA 檢測與渲染等待
            detect_result = await self._detect_and_render_spa(page, depth)
            
            # Frameset特殊處理
            if detect_result["type"] == "frameset":
                await page.close()
                # 返回特殊結果，從框架中提取的連結
                return (CrawlResult(url, status, "[FRAMESET_CONTAINER]", "", {}, depth, parent_url, 
                                  "Frameset Container", ""), 
                       set(detect_result["links"]), "Frameset Container", actual_url)
            
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            
            page_title = soup.title.string if soup.title else url.split('/')[-1] or "index"
            
            # 檢查標題是否已經存在於 page_info_dict 中
            for existing_url, existing_info in self.page_info_dict.items():
                if actual_url == existing_url:
                    self._log(f"{'  ' * (depth+1)}! Duplicate URL detected: {actual_url}")
                    self._log(f"{'  ' * (depth+1)}! URL already crawled, skipping duplicate")
                    await page.close()
                    # 返回一個表示跳過的結果，不儲存檔案也不提取連結
                    return CrawlResult(url, status, "[SKIPPED_DUPLICATE]", "", {}, depth, parent_url, page_title, ""), set(), page_title, actual_url
                
                if existing_info["title"] == page_title:
                    # 解析URL路徑，計算路徑段落數 - 使用實際URL而不是原始URL
                    current_path = [p for p in urlparse(actual_url).path.split('/') if p]
                    existing_path = [p for p in urlparse(existing_url).path.split('/') if p]
                    
                    current_path_count = len(current_path)
                    existing_path_count = len(existing_path)
                    
                    # 如果標題相同且URL段落數相同，檢查是否為列表分頁
                    if current_path_count == existing_path_count:
                        current_parsed = urlparse(actual_url)
                        
                        # 分頁相關的參數名
                        pagination_params = {'page', 'pagesize', 'offset', 'limit', 'start', 'count', 'p', 'pn'}
                        
                        # 檢查當前URL是否有分頁參數
                        is_pagination = False
                        if current_parsed.query:
                            current_params = parse_qs(current_parsed.query)
                            is_pagination = any(k.lower() in pagination_params for k in current_params.keys())
                        
                        if is_pagination:
                            # 是否啟用分頁爬取
                            if not self.enable_pagination:
                                # 不爬取分頁，視為重複頁面跳過
                                self._log(f"{'  ' * (depth+1)}! List pagination detected but pagination disabled: {page_title}")
                                self._log(f"{'  ' * (depth+1)}! Current URL: {actual_url} (segments: {current_path_count})")
                                self._log(f"{'  ' * (depth+1)}! Existing URL: {existing_url} (segments: {existing_path_count})")
                                self._log(f"{'  ' * (depth+1)}! Skipping as duplicate (pagination disabled)")
                                await page.close()
                                # 返回一個表示跳過的結果，不儲存檔案也不提取連結
                                return CrawlResult(url, status, "[SKIPPED_PAGINATION]", "", {}, depth, parent_url, page_title, ""), set(), page_title, actual_url
                            else:
                                # 有分頁參數且啟用分頁爬取，視為列表分頁
                                self._log(f"{'  ' * (depth+1)}! List pagination detected (query parameters): {page_title}")
                                self._log(f"{'  ' * (depth+1)}! Current URL: {actual_url} (segments: {current_path_count})")
                                self._log(f"{'  ' * (depth+1)}! Existing URL: {existing_url} (segments: {existing_path_count})")
                                self._log(f"{'  ' * (depth+1)}! Extracting links but not saving page")

                                # 提取頁面中的連結，但不儲存頁面
                                base_domain = urlparse(actual_url).netloc
                                for a in soup.find_all("a", href=True):
                                    href = a["href"]
                                    if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                                        continue
                                    
                                    link = urljoin(actual_url, href)
                                    if urlparse(link).netloc == base_domain:
                                        internal_links.add(link.split('#')[0]) # Remove fragment
                                
                                await page.close()
                                # 返回一個表示分頁的結果，包含連結但不儲存檔案
                                return CrawlResult(url, status, "[LIST_PAGINATION]", "", {}, depth, parent_url, page_title, ""), internal_links, page_title, actual_url
                        else:
                            # 沒有分頁參數，視為不同頁面，繼續正常處理
                            self._log(f"{'  ' * (depth+1)}! Same title but different content (no pagination params): {page_title}")
                            self._log(f"{'  ' * (depth+1)}! Current URL: {actual_url}")
                            self._log(f"{'  ' * (depth+1)}! Existing URL: {existing_url}")
                            self._log(f"{'  ' * (depth+1)}! Treating as separate page")
                            break  # 跳出循環，繼續正常處理這個頁面
                    
                    # 如果標題相同且URL段落數不同，視為重複頁面（如首頁的不同表示形式）
                    elif current_path_count != existing_path_count:
                        # 如果有儲存HTML檔案，進行內容比較
                        if self.save_html_files:
                            content_is_same = self._compare_page_content(html, existing_url)
                            if content_is_same:
                                self._log(f"{'  ' * (depth+1)}! Duplicate page detected (same title, different path segments, same content): {page_title}")
                                self._log(f"{'  ' * (depth+1)}! Current URL: {actual_url} (segments: {current_path_count})")
                                self._log(f"{'  ' * (depth+1)}! Existing URL: {existing_url} (segments: {existing_path_count})")
                                self._log(f"{'  ' * (depth+1)}! Content comparison: IDENTICAL - Skipping duplicate page")
                                await page.close()
                                # 返回一個表示跳過的結果，但不儲存檔案
                                return CrawlResult(url, status, "[SKIPPED_DUPLICATE]", "", {}, depth, parent_url, page_title, ""), set(), page_title, actual_url
                            else:
                                self._log(f"{'  ' * (depth+1)}! Same title, different path segments, but different content: {page_title}")
                                self._log(f"{'  ' * (depth+1)}! Current URL: {actual_url} (segments: {current_path_count})")
                                self._log(f"{'  ' * (depth+1)}! Existing URL: {existing_url} (segments: {existing_path_count})")
                                self._log(f"{'  ' * (depth+1)}! Content comparison: DIFFERENT - Treating as separate page")
                                break  # 跳出循環，繼續正常處理這個頁面
                        else:
                            # 沒有儲存HTML，無法比較內容，按原邏輯視為重複頁面
                            self._log(f"{'  ' * (depth+1)}! Duplicate page detected (same title, different path segments): {page_title}")
                            self._log(f"{'  ' * (depth+1)}! Current URL: {actual_url} (segments: {current_path_count})")
                            self._log(f"{'  ' * (depth+1)}! Existing URL: {existing_url} (segments: {existing_path_count})")
                            self._log(f"{'  ' * (depth+1)}! No HTML saved - cannot compare content, skipping as duplicate")
                            await page.close()
                            # 返回一個表示跳過的結果，但不儲存檔案
                            return CrawlResult(url, status, "[SKIPPED_DUPLICATE]", "", {}, depth, parent_url, page_title, ""), set(), page_title, actual_url
            
            filename = self._sanitize_name(page_title)
            
            # 儲存頁面
            saved_filepath = self._save_page_content(html, page_title, page_dir)

        except Exception as e:
            error_message = f"Error crawling {url}: {e}"
            self._log(f"{'  ' * (depth+1)}! {error_message}")
            
            # 如果是 HTTP 連結，嘗試轉換為 HTTPS
            if url.startswith('http://'):
                https_url = url.replace('http://', 'https://', 1)
                self._log(f"{'  ' * (depth+1)}! HTTP failed, trying HTTPS: {https_url}")
                
                # 關閉舊頁面並創建新頁面以避免導航衝突
                await page.close()
                page = await context.new_page()
                
                try:
                    response = await page.goto(https_url, timeout=self.timeout * 1000, wait_until="domcontentloaded")
                    status = response.status if response else 0
                    if response == 0 or status >= 400:
                        raise Exception(f"Page returned status {status}")
                    
                    self._log(f"{'  ' * (depth+1)}✓ HTTPS connection successful!")
                    
                    # 更新 URL 和實際 URL
                    url = https_url
                    actual_url = page.url
                    if actual_url != url:
                        self._log(f"{'  ' * (depth+1)}-> Redirected to: {actual_url}")
                    
                    # SPA 檢測與渲染等待
                    detect_result = await self._detect_and_render_spa(page, depth)
                    
                    # Frameset特殊處理
                    if detect_result["type"] == "frameset":
                        await page.close()
                        return (CrawlResult(url, status, "[FRAMESET_CONTAINER]", "", {}, depth, parent_url, 
                                          "Frameset Container", ""), 
                               set(detect_result["links"]), "Frameset Container", actual_url)
                    
                    # 繼續正常的頁面處理流程
                    html = await page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    
                    page_title = soup.title.string if soup.title else url.split('/')[-1] or "index"
                    
                    saved_filepath = self._save_page_content(html, page_title, page_dir)
                    
                    # 提取最後更新日期
                    last_updated = extract_last_updated(soup, self._log)
                    
                    # 記錄頁面資訊
                    self._record_page_info(actual_url, page_title, last_updated, saved_filepath, 
                                         status, depth, parent_url, url_to_title_map)
                    
                    # 提取連結和檢查外部連結
                    internal_links, external_link_status = await self._extract_and_check_links(
                        soup, actual_url, page_title, url, depth)
                    
                    await page.close()
                    return CrawlResult(url, status, html, last_updated, external_link_status, depth, parent_url, page_title, saved_filepath), internal_links, page_title, actual_url
                    
                except Exception as https_e:
                    await page.close()
                    self._log(f"{'  ' * (depth+1)}! HTTPS also failed: {type(https_e).__name__}: {https_e}")
            
            # 為失敗的頁面記錄來源頁面資訊
            self._record_page_info(actual_url, "", "[爬取失敗]", "", 
                                 status, depth, parent_url, url_to_title_map)
            
            await page.close()
            return CrawlResult(url, status, error_message, "", {}, depth, parent_url, "", ""), internal_links, "", actual_url

        # 提取最後更新日期
        last_updated = extract_last_updated(soup, self._log)
        
        # 記錄頁面資訊
        self._record_page_info(actual_url, page_title, last_updated, saved_filepath, 
                             status, depth, parent_url, url_to_title_map)

        # 提取連結和檢查外部連結
        internal_links, external_link_status = await self._extract_and_check_links(
            soup, actual_url, page_title, url, depth)
        
        await page.close()
        return CrawlResult(url, status, html, last_updated, external_link_status, depth, parent_url, page_title, saved_filepath), internal_links, page_title, actual_url

    async def crawl_site(self, browser: Browser, url: str, name: str = "", max_depth: int = 1) -> list[int]:
        """Crawls an entire site, starting from a URL, up to a max depth.
        If sitemap is found, starts from sitemap but also saves homepage info.
        Returns a list of HTTP status codes for processed pages.
        """
        
        if not name:
            name = urlparse(url).netloc.replace(".", "_")

        base_output_dir = os.path.join("assets", name)
        self.current_base_output_dir = base_output_dir
        
        # 在這裡創建 log_writer，將 log 儲存到網站資料夾中
        log_path = os.path.join(base_output_dir, "crawlog.txt")
        self.log_writer = LogWriter(custom_log_path=log_path)
        
        self.page_info_dict = {}
        self.external_link_results = {}
        
        url_to_dir_map = {}
        url_to_title_map = {} # Map URL to its title
        visited = set()
        all_results = []

        # 建立乾淨的 BrowserContext 用於整個網站的爬取
        context = await browser.new_context()

        # 首先爬取並保存主頁，同時檢查是否有 sitemap 連結
        self._log(f"Processing homepage and checking for sitemap: {url}")
        homepage_result, homepage_links, homepage_title, homepage_actual_url = await self._crawl_single_page(
            context, url, "", base_output_dir, url_to_dir_map, url_to_title_map, 0
        )
        
        # 建立合法的domain列表：原始URL + 重定向後的URL
        allowed_domains = {urlparse(url).netloc}
        if homepage_actual_url != url:
            allowed_domains.add(urlparse(homepage_actual_url).netloc)
        
        self._log(f"Allowed domains for this crawl: {allowed_domains}")
        
        # 將主頁加入已訪問和狀態結果
        visited.add(url)
        # 如果有重定向，也將實際URL加入visited
        if homepage_actual_url != url:
            visited.add(homepage_actual_url)
        all_results.append(homepage_result.status)
        if homepage_title:
            url_to_title_map[url] = homepage_title

        # 從主頁的HTML中尋找 sitemap 連結
        sitemap_url = None
        if homepage_result.html and homepage_result.status < 400:
            soup = BeautifulSoup(homepage_result.html, "html.parser")
            sitemap_url = self._find_sitemap_link(soup, url, homepage_actual_url)
            
        # 決定後續爬取策略
        if sitemap_url and sitemap_url not in visited:
            self._log(f"✓ Found sitemap! Will extract links from sitemap: {sitemap_url}")
            
            # 首先將sitemap頁面本身作為depth 0的頁面爬取並保存
            sitemap_result, sitemap_page_links, sitemap_title, sitemap_actual_url = await self._crawl_single_page(
                context, sitemap_url, "", base_output_dir, url_to_dir_map, url_to_title_map, 0
            )
            
            visited.add(sitemap_url)
            if sitemap_actual_url != sitemap_url:
                visited.add(sitemap_actual_url)
            all_results.append(sitemap_result.status)
            if sitemap_title:
                url_to_title_map[sitemap_url] = sitemap_title
            
            # 如果sitemap頁面成功爬取，從其HTML中提取主要內容的連結
            sitemap_links = set()
            if sitemap_result.html and sitemap_result.status < 400:
                sitemap_links = self._extract_links_from_sitemap(sitemap_result.html, sitemap_url)
                
                # 如果返回的連結集合包含sitemap本身，說明連結提取失敗，改用頁面中的所有連結
                if sitemap_url in sitemap_links:
                    self._log(f"    [Sitemap] Link extraction failed, using all page links instead")
                    sitemap_links = sitemap_page_links
                else:
                    self._log(f"    [Sitemap] Extracted {len(sitemap_links)} links from sitemap content")
            
            # 將 sitemap 連結加入隊列（深度1，父頁面為主頁）
            queue = deque()
            for link in sitemap_links:
                if link not in visited:
                    queue.append((link, url, 1))
            
            # 檢查 queue 是否為空，如果為空則回退到主頁連結
            if len(queue) > 0:
                self._log(f"✓ Added {len(queue)} links from sitemap to crawl queue")
            else:
                self._log(f"✗ No valid links found from sitemap, falling back to homepage links...")
                for link in homepage_links:
                    if link not in visited:
                        queue.append((link, url, 1))
                self._log(f"✓ Added {len(queue)} links from homepage to crawl queue")
        else:
            if sitemap_url:
                self._log("✓ Found sitemap, but it's same as homepage or already visited")
            else:
                self._log("✗ No sitemap found")
            # 從主頁的連結繼續爬取
            self._log("Continuing with homepage links...")
            queue = deque()
            for link in homepage_links:
                if link not in visited:
                    queue.append((link, url, 1))

        while queue:
            current_url, parent_url, current_depth = queue.popleft()
            
            # 檢查當前URL是否屬於允許的domain
            current_domain = urlparse(current_url).netloc
            if current_domain not in allowed_domains:
                continue
            
            if current_url in visited or current_depth > max_depth:
                continue

            visited.add(current_url)
            
            result, new_links, page_title, actual_url = await self._crawl_single_page(
                context, current_url, parent_url, base_output_dir, 
                url_to_dir_map, url_to_title_map, current_depth
            )
            
            # 如果有重定向，也將實際URL加入visited
            if actual_url != current_url:
                visited.add(actual_url)
            
            # 如果是重複頁面，跳過後續處理
            if result.html == "[SKIPPED_DUPLICATE]":
                self._log(f"{'  ' * (current_depth+1)}-> Skipped duplicate, not adding links to queue")
                continue  # 跳過後續處理，不加入連結到 queue
            
            # 如果是跳過的分頁（因為禁用分頁爬取），記錄但不進一步處理連結
            if result.html == "[SKIPPED_PAGINATION]":
                self._log(f"{'  ' * (current_depth+1)}-> Skipped pagination (pagination disabled), not processing links")
                if page_title:
                    url_to_title_map[current_url] = page_title
                all_results.append(result.status)
                continue  # 不加入連結到 queue
            
            # 如果是跳過的檔案（下載檔案、媒體檔案），記錄但不進一步處理連結
            if result.html == "[SKIPPED_FILE]":
                self._log(f"{'  ' * (current_depth+1)}-> Skipped file detected, not processing links")
                if page_title:
                    url_to_title_map[current_url] = page_title
                all_results.append(result.status)
                continue  # 不加入連結到 queue
            
            # 如果是列表分頁，提取連結但不儲存頁面
            if result.html == "[LIST_PAGINATION]":
                self._log(f"{'  ' * (current_depth+1)}-> List pagination, adding {len(new_links)} links to queue")
                # 分頁列表的連結以原始父頁面作為 parent 保持同一層級
                if current_depth < max_depth:
                    for link in new_links:
                        if link not in visited:
                            queue.append((link, parent_url, current_depth))
                continue  # 不將該頁面加入結果，但繼續處理其連結
            
            if page_title:
                url_to_title_map[current_url] = page_title

            # 只記錄狀態碼到 all_results
            all_results.append(result.status)

            if current_depth < max_depth:
                for link in new_links:
                    if link not in visited:
                        queue.append((link, current_url, current_depth + 1))
        
        await context.close()

        # 清理局部變數以協助垃圾回收
        url_to_dir_map.clear()
        url_to_title_map.clear() 
        visited.clear()
        queue.clear()
        
        # 爬取完成，所有結果都存在 page_info_dict 和 external_link_results 中
        return all_results