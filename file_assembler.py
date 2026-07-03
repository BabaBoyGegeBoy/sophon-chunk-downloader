"""文件组装：预分配文件空间 + 按 offset 流式写入 chunk 数据"""
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path

from config import Config
from data_loader import GameFileRecord

logger = logging.getLogger(__name__)


class FileAssembler:
    def __init__(self, config: Config):
        self.output_dir = config.output_dir
        self.max_open_files = config.max_open_files
        self._handles: OrderedDict[str, object] = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()

    def preallocate(self, file_records: list[GameFileRecord]):
        self.output_dir.mkdir(parents=True, exist_ok=True)

        for record in file_records:
            file_path = self.output_dir / record.remote_name
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'wb') as f:
                if record.file_size > 0:
                    f.truncate(record.file_size)

        logger.info(f'  [assembler] 已预分配 {len(file_records)} 个文件')

    def _get_handle(self, rel_path: str):
        with self._meta_lock:
            if rel_path in self._handles:
                self._handles.move_to_end(rel_path)
                return self._handles[rel_path], self._locks[rel_path]

            while len(self._handles) >= self.max_open_files:
                _, old_f = self._handles.popitem(last=False)
                try:
                    old_f.flush()
                except (OSError, IOError):
                    pass
                old_f.close()

            abs_path = self.output_dir / rel_path
            f = open(abs_path, 'r+b')
            self._handles[rel_path] = f
            lock = threading.Lock()
            self._locks[rel_path] = lock
            return f, lock

    def write_chunk(self, rel_path: str, data: bytes, offset: int):
        f, lock = self._get_handle(rel_path)
        with lock:
            f.seek(offset)
            f.write(data)

    def finalize_all(self):
        with self._meta_lock:
            for path, f in self._handles.items():
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except (OSError, IOError):
                    pass
                f.close()
            self._handles.clear()
            self._locks.clear()
        logger.info(f'  [assembler] 所有文件已刷盘完成')

    def close(self):
        self.finalize_all()
