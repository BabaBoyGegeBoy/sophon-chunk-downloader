"""校验模块：chunk 级 + 文件级双重 MD5 校验"""
import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from config import Config
from data_loader import GameFileRecord

logger = logging.getLogger(__name__)

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


@dataclass
class FileVerifyResult:
    file_path: str
    passed: bool
    expected: str
    actual: str


def verify_chunk(data: bytes, expected_checksum: str) -> bool:
    if not expected_checksum:
        return True
    actual = hashlib.md5(data).hexdigest()
    return actual == expected_checksum.lower()


def compute_file_md5(file_path: Path) -> str:
    md5 = hashlib.md5()
    with open(file_path, 'rb') as f:
        while True:
            buf = f.read(8 * 1024 * 1024)
            if not buf:
                break
            md5.update(buf)
    return md5.hexdigest()


def verify_file(file_path: Path, expected_md5: str) -> tuple[bool, str]:
    if not expected_md5:
        return True, ''
    if not file_path.exists():
        return False, '<文件不存在>'
    actual = compute_file_md5(file_path)
    return (actual == expected_md5.lower()), actual


def verify_all_files(file_records: list[GameFileRecord],
                     config: Config,
                     max_workers: int = 8) -> list[FileVerifyResult]:
    output_dir = config.output_dir
    results = []
    total = len(file_records)

    iterator = range(total)
    if HAS_TQDM:
        pbar = tqdm(total=total, desc='文件校验', unit='file')

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for record in file_records:
            file_path = output_dir / record.remote_name
            future = executor.submit(verify_file, file_path, record.md5)
            future_map[future] = record

        for i, future in enumerate(as_completed(future_map)):
            record = future_map[future]
            passed, actual_md5 = future.result()
            results.append(FileVerifyResult(
                file_path=record.remote_name,
                passed=passed,
                expected=record.md5,
                actual=actual_md5,
            ))

            if HAS_TQDM:
                pbar.update(1)
            elif (i + 1) % 100 == 0:
                logger.info(f'  [verify] 文件校验进度: {i + 1}/{total}')

    if HAS_TQDM:
        pbar.close()

    return results
