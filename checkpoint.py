"""断点续传：持久化已完成 chunkId 集合"""
import json
import logging
import os
import threading
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)


class Checkpoint:
    def __init__(self, config: Config):
        self.path = config.checkpoint_path
        self.completed: set[str] = set()
        self._lock = threading.Lock()
        self._dirty_count = 0

    def load(self):
        if self.path.exists():
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.completed = set(data.get('completed_chunks', []))
            logger.info(f'  [checkpoint] 已加载 {len(self.completed)} 个已完成的 chunk')
        else:
            logger.info(f'  [checkpoint] 未找到 checkpoint，从头开始')

    def is_completed(self, chunk_id: str) -> bool:
        with self._lock:
            return chunk_id in self.completed

    def mark_completed(self, chunk_id: str):
        with self._lock:
            self.completed.add(chunk_id)
            self._dirty_count += 1

    def save(self, force: bool = False):
        with self._lock:
            if not force and self._dirty_count == 0:
                return
            data_to_write = {
                'completed_chunks': sorted(self.completed),
                'total_completed': len(self.completed),
            }

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix('.json.tmp')

        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data_to_write, f, ensure_ascii=False)

        os.replace(tmp_path, self.path)

        with self._lock:
            self._dirty_count = 0

    def save_if_needed(self, threshold: int = 100):
        with self._lock:
            if self._dirty_count < threshold:
                return
        self.save()

    def stats(self) -> dict:
        with self._lock:
            return {'completed': len(self.completed), 'pending_save': self._dirty_count}
