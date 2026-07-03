"""
数据输入层：加载 chunk JSON、pkg_version 文件清单
支持自动从 GitHub 下载数据文件
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import requests

from config import Config
from utils import GAMES, AUDIO_LANG_FILES, PKG_VERSION_REPO

logger = logging.getLogger(__name__)


@dataclass
class ChunkManifestInfo:
    """从 chunk JSON 中提取的 manifest 元信息"""
    category_id: str
    category_name: str
    manifest_id: str
    manifest_checksum: str
    manifest_compressed_size: int
    manifest_uncompressed_size: int
    chunk_url_prefix: str
    chunk_url_suffix: str
    manifest_url_prefix: str
    manifest_url_suffix: str
    matching_field: str


@dataclass
class GameFileRecord:
    """pkg_version 中每行一个的文件记录"""
    remote_name: str
    md5: str
    file_size: int


def _get_proxies(config: Config) -> dict | None:
    if config.use_proxy and config.proxy:
        return {'http': config.proxy, 'https': config.proxy}
    return {'http': None, 'https': None}


def ensure_data_available(config: Config) -> bool:
    """
    检查 pkg_version 数据是否可用，不可用则提示用户。
    返回 True 表示数据可用。
    """
    if config.chunk_json_path.exists():
        return True

    logger.warning(f'未找到 chunk JSON: {config.chunk_json_path}')
    logger.info(f'请先克隆数据仓库:')
    logger.info(f'  git clone {PKG_VERSION_REPO}.git "{config.data_dir}"')
    logger.info(f'或指定数据目录:')
    logger.info(f'  python main.py --data-dir /path/to/pkg_version')

    # 尝试自动下载单个文件
    choice = input('\n是否自动下载所需数据文件？(y/n): ').strip().lower()
    if choice != 'y':
        return False

    return _download_data_file(config)


def _download_data_file(config: Config) -> bool:
    """从 GitHub Raw 下载所需的 chunk JSON"""
    raw_base = f'https://raw.githubusercontent.com/orilights/pkg_version/main'
    rel_path = f'chunk/{config.game_id}_{config.version}.json'
    url = f'{raw_base}/{rel_path}'

    logger.info(f'下载: {url}')
    try:
        resp = requests.get(url, timeout=60, proxies=_get_proxies(config))
        resp.raise_for_status()
        config.chunk_json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config.chunk_json_path, 'wb') as f:
            f.write(resp.content)
        logger.info(f'已保存: {config.chunk_json_path}')
        return True
    except Exception as e:
        logger.error(f'下载失败: {e}')
        return False


def load_chunk_json(config: Config) -> list[ChunkManifestInfo]:
    """读取 chunk JSON，提取目标 category 的 manifest 信息"""
    with open(config.chunk_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    manifests = data['data']['manifests']
    result = []
    for m in manifests:
        cat_id = m['category_id']
        if cat_id not in config.categories:
            continue

        result.append(ChunkManifestInfo(
            category_id=cat_id,
            category_name=m['category_name'],
            manifest_id=m['manifest']['id'],
            manifest_checksum=m['manifest']['checksum'],
            manifest_compressed_size=int(m['manifest']['compressed_size']),
            manifest_uncompressed_size=int(m['manifest']['uncompressed_size']),
            chunk_url_prefix=m['chunk_download']['url_prefix'],
            chunk_url_suffix=m['chunk_download'].get('url_suffix', ''),
            manifest_url_prefix=m['manifest_download']['url_prefix'],
            manifest_url_suffix=m['manifest_download'].get('url_suffix', ''),
            matching_field=m['matching_field'],
        ))

    return result


def load_file_list(config: Config, category_id: str, info: ChunkManifestInfo = None) -> list[GameFileRecord]:
    """读取 pkg_version JSONL 文件"""
    # 确定文件名
    if category_id == '10017':
        filename = 'pkg_version'
    elif info and '语音' in info.category_name:
        # 语音包：根据 category_name 中的语言信息或 config.audio_langs 推断
        filename = _guess_audio_filename(info.category_name, config)
    else:
        # 通用回退
        filename = 'pkg_version'

    path = config.pkg_version_dir / filename

    if not path.exists():
        # 尝试自动下载
        raw_base = f'https://raw.githubusercontent.com/orilights/pkg_version/main'
        rel_path = f'{config.game_id}/{config.version}/{filename}'
        url = f'{raw_base}/{rel_path}'
        logger.info(f'下载文件清单: {url}')
        resp = requests.get(url, timeout=60, proxies=_get_proxies(config))
        resp.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(resp.content)

    records = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            records.append(GameFileRecord(
                remote_name=obj['remoteName'],
                md5=obj['md5'],
                file_size=obj.get('fileSize', 0),
            ))

    return records


def _guess_audio_filename(category_name: str, config: Config) -> str:
    """根据 category_name 猜测语音包文件名"""
    for lang, fname in AUDIO_LANG_FILES.items():
        lang_cn = {'zh-cn': '中文', 'en-us': '英语', 'ja-jp': '日语', 'ko-kr': '韩语'}
        if lang_cn.get(lang, '') in category_name:
            return fname
    # 回退到中文
    return AUDIO_LANG_FILES['zh-cn']


def resolve_manifest_source(info: ChunkManifestInfo, config: Config) -> tuple[str, bool]:
    """判断 manifest 是本地缓存还是需要从 CDN 下载"""
    cache_path = config.manifest_cache_dir / info.manifest_id
    if cache_path.exists():
        return str(cache_path), True

    url = f"{info.manifest_url_prefix}/{info.manifest_id}{info.manifest_url_suffix}"
    return url, False
