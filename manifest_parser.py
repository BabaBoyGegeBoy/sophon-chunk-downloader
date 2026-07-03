"""
Manifest 解析：ZSTD 解压 + Protobuf wire format 解码（纯 Python 手写解析器）

Protobuf Schema:
  message Manifest { repeated File files = 1; }
  message File {
    string path = 1;
    repeated Chunk chunks = 2;
    bool isFolder = 3;
    int32 size = 4;
    string checksum = 5;
  }
  message Chunk {
    string id = 1;
    string checksum = 2;
    int32 offset = 3;
    int32 compressedSize = 4;
    int32 uncompressedSize = 5;
  }
"""
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

import requests
import zstandard

from config import Config
from data_loader import ChunkManifestInfo, resolve_manifest_source

logger = logging.getLogger(__name__)


@dataclass
class ParsedChunk:
    chunk_id: str
    checksum: str
    offset: int
    compressed_size: int
    uncompressed_size: int


@dataclass
class ParsedFile:
    path: str
    chunks: list[ParsedChunk] = field(default_factory=list)
    is_folder: bool = False
    size: int = 0
    checksum: str = ''


# ===================== Protobuf Wire Format 解析器 =====================

def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("读取 varint 时数据意外结束")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos


def _parse_chunk_msg(data: bytes) -> ParsedChunk:
    pos = 0
    chunk_id = checksum = ''
    offset = compressed_size = uncompressed_size = 0

    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field_number = key >> 3
        wire_type = key & 0x7

        if wire_type == 2:
            length, pos = _read_varint(data, pos)
            value = data[pos:pos + length]
            pos += length
            if field_number == 1:
                chunk_id = value.decode('utf-8')
            elif field_number == 2:
                checksum = value.decode('utf-8')
        elif wire_type == 0:
            value, pos = _read_varint(data, pos)
            if field_number == 3:
                offset = value
            elif field_number == 4:
                compressed_size = value
            elif field_number == 5:
                uncompressed_size = value
        elif wire_type == 5:
            pos += 4
        elif wire_type == 1:
            pos += 8
        else:
            break

    return ParsedChunk(chunk_id, checksum, offset, compressed_size, uncompressed_size)


def _parse_file_msg(data: bytes) -> ParsedFile:
    pos = 0
    path = checksum = ''
    chunks = []
    is_folder = False
    size = 0

    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field_number = key >> 3
        wire_type = key & 0x7

        if wire_type == 2:
            length, pos = _read_varint(data, pos)
            value = data[pos:pos + length]
            pos += length
            if field_number == 1:
                path = value.decode('utf-8')
            elif field_number == 2:
                chunks.append(_parse_chunk_msg(value))
            elif field_number == 5:
                checksum = value.decode('utf-8')
        elif wire_type == 0:
            value, pos = _read_varint(data, pos)
            if field_number == 3:
                is_folder = bool(value)
            elif field_number == 4:
                size = value
        elif wire_type == 5:
            pos += 4
        elif wire_type == 1:
            pos += 8
        else:
            break

    return ParsedFile(path, chunks, is_folder, size, checksum)


def _parse_manifest_msg(data: bytes) -> list[ParsedFile]:
    pos = 0
    files = []

    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field_number = key >> 3
        wire_type = key & 0x7

        if wire_type == 2:
            length, pos = _read_varint(data, pos)
            value = data[pos:pos + length]
            pos += length
            if field_number == 1:
                files.append(_parse_file_msg(value))
        elif wire_type == 0:
            _, pos = _read_varint(data, pos)
        elif wire_type == 5:
            pos += 4
        elif wire_type == 1:
            pos += 8
        else:
            break

    return files


# ===================== ZSTD 解压 + Manifest 加载 =====================

_ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'


def is_zstd_compressed(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == _ZSTD_MAGIC


def decompress_zstd(data: bytes, uncompressed_size: int = 0) -> bytes:
    """ZSTD 解压，自动检测数据是否已解压"""
    if not is_zstd_compressed(data):
        return data

    dctx = zstandard.ZstdDecompressor()
    reader = dctx.stream_reader(io.BytesIO(data))
    return reader.read()


def parse_manifest_binary(data: bytes, uncompressed_size: int) -> list[ParsedFile]:
    decompressed = decompress_zstd(data, uncompressed_size)
    return _parse_manifest_msg(decompressed)


def load_manifest(info: ChunkManifestInfo, config: Config) -> list[ParsedFile]:
    """加载 manifest：本地缓存有则读，无则从 CDN 下载并缓存"""
    source, is_local = resolve_manifest_source(info, config)

    if is_local:
        logger.info(f'  [{info.category_id}] 读取本地缓存: {source}')
        with open(source, 'rb') as f:
            data = f.read()
    else:
        logger.info(f'  [{info.category_id}] 从 CDN 下载: {source}')
        proxies = None
        if config.use_proxy and config.proxy:
            proxies = {'http': config.proxy, 'https': config.proxy}
        else:
            proxies = {'http': None, 'https': None}
        resp = requests.get(source, timeout=config.request_timeout, proxies=proxies)
        resp.raise_for_status()
        data = resp.content

        cache_path = config.manifest_cache_dir / info.manifest_id
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'wb') as f:
            f.write(data)
        logger.info(f'  [{info.category_id}] 已缓存到: {cache_path}')

    files = parse_manifest_binary(data, info.manifest_uncompressed_size)
    logger.info(f'  [{info.category_id}] 解析完成: {len(files)} 个文件')
    return files
