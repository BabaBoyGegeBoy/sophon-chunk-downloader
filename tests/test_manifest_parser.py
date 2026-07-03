"""
manifest_parser 单元测试

覆盖：
  - _read_varint          : varint 解码
  - _parse_chunk_msg      : Chunk message 解码
  - _parse_file_msg       : File message 解码
  - is_zstd_compressed    : ZSTD 魔数检测
  - decompress_zstd       : 非 ZSTD 数据原样返回 / ZSTD 数据正确解压
"""
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，以便直接 import 项目模块
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from manifest_parser import (
    _read_varint,
    _parse_chunk_msg,
    _parse_file_msg,
    is_zstd_compressed,
    decompress_zstd,
)


# ===================== Protobuf 编码辅助函数（仅用于构造测试输入） =====================

def encode_varint(value: int) -> bytes:
    """编码无符号整数为 protobuf varint。"""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def encode_field_varint(field_number: int, value: int) -> bytes:
    """编码 wire type 0 (varint) 字段。"""
    key = (field_number << 3) | 0
    return encode_varint(key) + encode_varint(value)


def encode_field_string(field_number: int, text: str) -> bytes:
    """编码 wire type 2 (length-delimited) 字符串字段。"""
    key = (field_number << 3) | 2
    data = text.encode('utf-8')
    return encode_varint(key) + encode_varint(len(data)) + data


def encode_field_bytes(field_number: int, data: bytes) -> bytes:
    """编码 wire type 2 (length-delimited) 嵌套 message 字段。"""
    key = (field_number << 3) | 2
    return encode_varint(key) + encode_varint(len(data)) + data


# ===================== _read_varint =====================

def test_read_varint():
    # 单字节：0 / 1 / 127
    assert _read_varint(b'\x00', 0) == (0, 1)
    assert _read_varint(b'\x01', 0) == (1, 1)
    assert _read_varint(b'\x7f', 0) == (127, 1)

    # 多字节：128 -> 0x80 0x01
    assert _read_varint(b'\x80\x01', 0) == (128, 2)

    # 300 -> 0xac 0x02
    assert _read_varint(b'\xac\x02', 0) == (300, 2)

    # 16384 -> 0x80 0x80 0x01
    assert _read_varint(b'\x80\x80\x01', 0) == (16384, 3)

    # 从非零偏移开始读取，且返回更新后的 pos
    assert _read_varint(b'\xff\x01\xac\x02', 2) == (300, 4)

    # 数据意外结束（只有 continuation bit）应抛错
    with pytest.raises(ValueError):
        _read_varint(b'\x80', 0)
    with pytest.raises(ValueError):
        _read_varint(b'', 0)


# ===================== _parse_chunk_msg =====================

def test_parse_chunk_msg():
    chunk_id = 'chunk_001'
    checksum = 'abc123def456'
    offset = 1024
    compressed_size = 512
    uncompressed_size = 2048

    data = b''
    data += encode_field_string(1, chunk_id)
    data += encode_field_string(2, checksum)
    data += encode_field_varint(3, offset)
    data += encode_field_varint(4, compressed_size)
    data += encode_field_varint(5, uncompressed_size)

    chunk = _parse_chunk_msg(data)

    assert chunk.chunk_id == chunk_id
    assert chunk.checksum == checksum
    assert chunk.offset == offset
    assert chunk.compressed_size == compressed_size
    assert chunk.uncompressed_size == uncompressed_size


def test_parse_chunk_msg_partial_fields():
    # 仅提供部分字段，其余应保持默认值
    data = encode_field_string(1, 'only_id') + encode_field_varint(4, 999)
    chunk = _parse_chunk_msg(data)

    assert chunk.chunk_id == 'only_id'
    assert chunk.checksum == ''
    assert chunk.offset == 0
    assert chunk.compressed_size == 999
    assert chunk.uncompressed_size == 0


# ===================== _parse_file_msg =====================

def test_parse_file_msg():
    # 构造一个嵌套的 Chunk sub-message
    chunk_data = b''
    chunk_data += encode_field_string(1, 'c1')
    chunk_data += encode_field_string(2, 'chunk_md5')
    chunk_data += encode_field_varint(3, 0)
    chunk_data += encode_field_varint(4, 100)
    chunk_data += encode_field_varint(5, 200)

    path = 'assets/textures/file.tex'
    file_checksum = 'file_md5_hash'
    size = 1000

    data = b''
    data += encode_field_string(1, path)
    data += encode_field_bytes(2, chunk_data)   # field 2 = repeated Chunk
    data += encode_field_varint(3, 1)           # isFolder = True
    data += encode_field_varint(4, size)
    data += encode_field_string(5, file_checksum)

    f = _parse_file_msg(data)

    assert f.path == path
    assert f.is_folder is True
    assert f.size == size
    assert f.checksum == file_checksum
    assert len(f.chunks) == 1

    chunk = f.chunks[0]
    assert chunk.chunk_id == 'c1'
    assert chunk.checksum == 'chunk_md5'
    assert chunk.offset == 0
    assert chunk.compressed_size == 100
    assert chunk.uncompressed_size == 200


def test_parse_file_msg_multiple_chunks_and_folder_false():
    def make_chunk(cid: str) -> bytes:
        d = encode_field_string(1, cid)
        d += encode_field_varint(4, 10)
        d += encode_field_varint(5, 20)
        return d

    data = b''
    data += encode_field_string(1, 'a/b.bin')
    data += encode_field_bytes(2, make_chunk('c1'))
    data += encode_field_bytes(2, make_chunk('c2'))
    data += encode_field_varint(3, 0)   # isFolder = False
    data += encode_field_varint(4, 40)

    f = _parse_file_msg(data)

    assert f.path == 'a/b.bin'
    assert f.is_folder is False
    assert f.size == 40
    assert f.checksum == ''
    assert [c.chunk_id for c in f.chunks] == ['c1', 'c2']
    assert all(c.compressed_size == 10 and c.uncompressed_size == 20 for c in f.chunks)


# ===================== is_zstd_compressed =====================

def test_is_zstd_compressed():
    magic = b'\x28\xb5\x2f\xfd'

    # 标准 ZSTD 魔数前缀
    assert is_zstd_compressed(magic + b'payload') is True
    assert is_zstd_compressed(magic) is True

    # 非魔数前缀
    assert is_zstd_compressed(b'hello world') is False
    assert is_zstd_compressed(b'\x00\x00\x00\x00') is False
    # 最后一个字节不同
    assert is_zstd_compressed(b'\x28\xb5\x2f\xfc') is False

    # 长度不足 4 字节
    assert is_zstd_compressed(b'\x28\xb5\x2f') is False
    assert is_zstd_compressed(b'\x00') is False
    assert is_zstd_compressed(b'') is False


# ===================== decompress_zstd =====================

def test_decompress_zstd_unchanged():
    # 非 ZSTD 数据应原样返回（内容一致）
    raw = b'plain text data, not compressed'
    result = decompress_zstd(raw)
    assert result == raw


def test_decompress_zstd_roundtrip():
    # 真实 ZSTD 压缩 -> 解压 往返测试
    import zstandard

    raw = (b'sophon chunk manifest payload ' * 50)
    cctx = zstandard.ZstdCompressor()
    compressed = cctx.compress(raw)

    assert is_zstd_compressed(compressed) is True
    assert decompress_zstd(compressed) == raw
