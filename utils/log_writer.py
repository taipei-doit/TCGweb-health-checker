import os
from datetime import datetime
from typing import Optional, TextIO

class LogWriter:
    """
    用於同時向 terminal 和檔案寫入 log 的工具類別
    支援緩衝機制以提升性能
    """
    
    def __init__(self, log_dir: str = "output", log_prefix: str = "crawl_log", custom_log_path: str = None, buffer_size: int = 500):
        if custom_log_path:
            # 使用自定義路徑
            self.log_file_path = custom_log_path
            log_dir = os.path.dirname(custom_log_path)
        else:
            # 使用原來的邏輯
            self.log_dir = log_dir
            os.makedirs(log_dir, exist_ok=True)
            
            # 生成檔案名稱，包含時間戳
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.log_file_path = os.path.join(log_dir, f"{log_prefix}_{timestamp}.txt")
        
        # 確保 log 檔案的目錄存在
        os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
        
        # 緩衝機制相關設定
        self.buffer_size = buffer_size  # 累積多少條訊息後寫入
        self.log_buffer = []  # 訊息緩衝區
        
        # 開啟 log 檔案
        self.log_file: Optional[TextIO] = None
        self._open_log_file()
        
    def _open_log_file(self):
        """開啟 log 檔案"""
        try:
            self.log_file = open(self.log_file_path, 'w', encoding='utf-8')
            self._write_log_header()
        except Exception as e:
            print(f"警告：無法開啟 log 檔案 {self.log_file_path}: {e}")
            self.log_file = None
    
    def _write_log_header(self):
        """寫入 log 檔案標頭"""
        if self.log_file:
            header = f"網站爬蟲 Log 檔案\n生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'-'*50}\n\n"
            self.log_file.write(header)
    
    def _flush_buffer(self):
        """將緩衝區的內容寫入檔案"""
        if self.log_file and self.log_buffer:
            # 批次寫入所有緩衝的訊息
            self.log_file.write('\n'.join(self.log_buffer) + '\n')
            self.log_buffer.clear()  # 清空緩衝區
    
    def log_only(self, message: str):
        """只寫入到 log 檔案，不輸出到 terminal（使用緩衝機制）"""
        if self.log_file:
            # 直接將訊息加入緩衝區，不添加時間戳
            self.log_buffer.append(message)
            
            # 當緩衝區達到指定大小時，批次寫入
            if len(self.log_buffer) >= self.buffer_size:
                self._flush_buffer()
    
    def close(self):
        """關閉 log 檔案"""
        if self.log_file:
            # 在關閉前確保緩衝區內容都寫入
            self._flush_buffer()
            
            self.log_file.write(f"\n{'-'*50}\nLog 結束時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            self.log_file.close()
            self.log_file = None
    
    def get_log_file_path(self) -> str:
        """取得 log 檔案路徑"""
        return self.log_file_path
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()