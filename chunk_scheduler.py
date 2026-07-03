"""
调度引擎：chunk 去重、并发下载、整合解压/校验/写入
"""
import hashlib
import logging
import threading
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from config import Config
from data_loader import ChunkManifestInfo, GameFileRecord
from manifest_parser import ParsedFile, ParsedChunk, decompress_zstd
from checkpoint import Checkpoint
from file_assembler import FileAssembler
from verifier import verify_chunk
from utils import format_size

logger = logging.getLogger(__name__)

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


@dataclass
class ChunkWriteTarget:
    file_path: str
    offset: int


@dataclass
class ChunkTask:
    chunk_id: str
    url: str
    checksum: str
    uncompressed_size: int
    compressed_size: int
    write_targets: list[ChunkWriteTarget] = field(default_factory=list)


@dataclass
class ChunkResult:
    chunk_id: str
    success: bool
    retries: int
    error: str = ''
    elapsed: float = 0.0


@dataclass
class SchedulerReport:
    total_chunks: int = 0
    completed_chunks: int = 0
    failed_chunks: int = 0
    skipped_chunks: int = 0
    total_retries: int = 0
    total_bytes_downloaded: int = 0
    total_elapsed: float = 0.0
    failed_list: list[ChunkResult] = field(default_factory=list)


_thread_local = threading.local()


def _get_session(config: Config) -> requests.Session:
    if not hasattr(_thread_local, 'session'):
        session = requests.Session()
        if config.use_proxy and config.proxy:
            session.proxies = {'http': config.proxy, 'https': config.proxy}
        else:
            session.trust_env = False
        _thread_local.session = session
    return _thread_local.session


class ChunkScheduler:
    def __init__(self, config: Config, checkpoint: Checkpoint, assembler: FileAssembler):
        self.config = config
        self.checkpoint = checkpoint
        self.assembler = assembler
        self._chunk_tasks: dict[str, ChunkTask] = {}
        self._total_compressed_size: int = 0
        self._progress_lock = threading.Lock()
        self._completed_count = 0
        self._failed_count = 0
        self._bytes_downloaded = 0
        self._start_time = 0.0

    def build_chunk_index(
        self,
        manifests_data: list[tuple[ChunkManifestInfo, list[ParsedFile]]],
        file_lists: dict[str, list[GameFileRecord]],
    ) -> None:
        path_to_category: dict[str, str] = {}
        for cat_id, records in file_lists.items():
            for record in records:
                path_to_category[record.remote_name] = cat_id

        for info, parsed_files in manifests_data:
            chunk_url_prefix = info.chunk_url_prefix
            chunk_url_suffix = info.chunk_url_suffix

            for pf in parsed_files:
                if pf.is_folder:
                    continue
                if pf.path not in path_to_category:
                    continue

                for chunk in pf.chunks:
                    if chunk.chunk_id not in self._chunk_tasks:
                        url = f"{chunk_url_prefix}/{chunk.chunk_id}{chunk_url_suffix}"
                        task = ChunkTask(
                            chunk_id=chunk.chunk_id,
                            url=url,
                            checksum=chunk.checksum,
                            uncompressed_size=chunk.uncompressed_size,
                            compressed_size=chunk.compressed_size,
                            write_targets=[],
                        )
                        self._chunk_tasks[chunk.chunk_id] = task
                        self._total_compressed_size += chunk.compressed_size

                    self._chunk_tasks[chunk.chunk_id].write_targets.append(
                        ChunkWriteTarget(file_path=pf.path, offset=chunk.offset)
                    )

        total_write_targets = sum(len(t.write_targets) for t in self._chunk_tasks.values())
        logger.info(f'  [scheduler] 索引构建完成:')
        logger.info(f'    唯一 chunk 数: {len(self._chunk_tasks)}')
        logger.info(f'    写入目标总数: {total_write_targets}')
        logger.info(f'    预计下载量: {format_size(self._total_compressed_size)} (压缩后)')

    def run(self) -> SchedulerReport:
        pending_tasks = []
        skipped = 0
        for task in self._chunk_tasks.values():
            if self.checkpoint.is_completed(task.chunk_id):
                skipped += 1
            else:
                pending_tasks.append(task)

        total = len(self._chunk_tasks)
        logger.info(f'\n  [scheduler] 开始下载:')
        logger.info(f'    总计: {total} chunks')
        logger.info(f'    已完成(checkpoint): {skipped}')
        logger.info(f'    待下载: {len(pending_tasks)}')
        logger.info(f'    并发数: {self.config.max_concurrency}')

        if not pending_tasks:
            logger.info(f'    所有 chunk 已完成，无需下载')
            return SchedulerReport(total_chunks=total, completed_chunks=total, skipped_chunks=skipped)

        self._start_time = time.time()
        report = SchedulerReport(total_chunks=total, skipped_chunks=skipped)

        if HAS_TQDM:
            pbar = tqdm(total=len(pending_tasks), desc='下载', unit='chunk')

        with ThreadPoolExecutor(max_workers=self.config.max_concurrency) as executor:
            future_map = {
                executor.submit(self._process_chunk, task): task
                for task in pending_tasks
            }

            for future in as_completed(future_map):
                task = future_map[future]
                try:
                    result = future.result()

                    if result.success:
                        with self._progress_lock:
                            self._completed_count += 1
                            self._bytes_downloaded += task.compressed_size
                            done = self._completed_count + self._failed_count

                            if done % self.config.checkpoint_interval == 0:
                                self.checkpoint.save()

                            if HAS_TQDM:
                                elapsed = time.time() - self._start_time
                                speed = self._bytes_downloaded / elapsed if elapsed > 0 else 0
                                pbar.set_postfix(
                                    speed=f'{speed/(1024**2):.1f}MB/s',
                                    fail=self._failed_count
                                )
                                pbar.update(1)
                            elif done % 50 == 0 or done == len(pending_tasks):
                                elapsed = time.time() - self._start_time
                                speed = self._bytes_downloaded / elapsed if elapsed > 0 else 0
                                pct = (done / len(pending_tasks)) * 100
                                logger.info(
                                    f'    进度: {done}/{len(pending_tasks)} ({pct:.1f}%) | '
                                    f'速度: {speed/(1024**2):.1f} MB/s | 失败: {self._failed_count}'
                                )
                    else:
                        with self._progress_lock:
                            self._failed_count += 1
                        report.failed_list.append(result)
                        report.total_retries += result.retries
                        logger.warning(f'    [失败] chunk {result.chunk_id}: {result.error}')

                        if HAS_TQDM:
                            pbar.update(1)

                except Exception as e:
                    with self._progress_lock:
                        self._failed_count += 1
                    result = ChunkResult(task.chunk_id, False, 0, str(e))
                    report.failed_list.append(result)
                    logger.error(f'    [异常] chunk {task.chunk_id}: {e}')

                    if HAS_TQDM:
                        pbar.update(1)

        if HAS_TQDM:
            pbar.close()

        self.checkpoint.save(force=True)

        elapsed = time.time() - self._start_time
        report.completed_chunks = self._completed_count
        report.failed_chunks = self._failed_count
        report.total_bytes_downloaded = self._bytes_downloaded
        report.total_elapsed = elapsed

        return report

    def _process_chunk(self, task: ChunkTask) -> ChunkResult:
        start = time.time()
        last_error = ''

        for attempt in range(self.config.chunk_retries + 1):
            try:
                session = _get_session(self.config)
                resp = session.get(task.url, timeout=self.config.request_timeout)
                resp.raise_for_status()
                compressed_data = resp.content

                decompressed = decompress_zstd(compressed_data, task.uncompressed_size)

                if self.config.verify_chunks and task.checksum:
                    if not verify_chunk(decompressed, task.checksum):
                        actual_md5 = hashlib.md5(decompressed).hexdigest()
                        last_error = f'chunk MD5 校验失败: expected={task.checksum}, actual={actual_md5}'
                        if attempt < self.config.chunk_retries:
                            time.sleep(self.config.retry_backoff * (2 ** attempt))
                        continue

                for target in task.write_targets:
                    self.assembler.write_chunk(target.file_path, decompressed, target.offset)

                self.checkpoint.mark_completed(task.chunk_id)

                return ChunkResult(task.chunk_id, True, attempt, elapsed=time.time() - start)

            except Exception as e:
                last_error = str(e)
                if attempt < self.config.chunk_retries:
                    time.sleep(self.config.retry_backoff * (2 ** attempt))

        return ChunkResult(task.chunk_id, False, self.config.chunk_retries, last_error, time.time() - start)
