"""
配置中心：支持命令行参数、配置文件、默认值三级配置
"""
import argparse
import yaml
from dataclasses import dataclass, field
from pathlib import Path

from utils import GAMES, AUDIO_LANG_FILES


@dataclass
class Config:
    """运行时配置"""
    # 游戏与版本
    game_id: str = 'hk4e'
    version: str = '6.1.0'

    # 路径
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent)
    data_dir: Path = field(default_factory=lambda: Path('pkg_version'))
    output_dir: Path = field(default_factory=lambda: Path('output'))

    @property
    def pkg_version_dir(self) -> Path:
        return self.data_dir / self.game_id / self.version

    @property
    def chunk_json_path(self) -> Path:
        return self.data_dir / 'chunk' / f'{self.game_id}_{self.version}.json'

    @property
    def versions_json_path(self) -> Path:
        return self.data_dir / f'{self.game_id}_versions.json'

    @property
    def manifest_cache_dir(self) -> Path:
        """manifest 缓存目录（在 output 下，不污染源码）"""
        return self.output_dir / '.manifest_cache'

    @property
    def checkpoint_path(self) -> Path:
        return self.output_dir / '.checkpoint.json'

    @property
    def verify_report_path(self) -> Path:
        return self.output_dir / 'verify_report.json'

    # 下载范围
    categories: list = field(default_factory=lambda: ['10017'])  # 默认仅游戏本体
    audio_langs: list = field(default_factory=lambda: [])         # 语音包语言

    @property
    def category_files(self) -> dict:
        """category_id -> pkg_version 文件名"""
        result = {'10017': 'pkg_version'}  # 游戏资源（所有游戏通用）
        # 语音包 category_id 因游戏而异，需要运行时从 chunk JSON 获取
        return result

    # 下载参数
    max_concurrency: int = 32
    chunk_retries: int = 3
    retry_backoff: float = 1.0
    request_timeout: int = 60
    max_speed: int = 0  # 0 = 不限速（MB/s）

    # 校验
    verify_chunks: bool = True
    verify_files: bool = True

    # 文件组装
    max_open_files: int = 256

    # 网络
    use_proxy: bool = False
    proxy: str = ''

    # 日志
    verbose: bool = False
    quiet: bool = False

    # Checkpoint
    checkpoint_interval: int = 100


def build_config() -> Config:
    """从命令行参数和配置文件构建 Config"""
    parser = argparse.ArgumentParser(
        description='Sophon Chunk 下载器 - 通过 chunk 机制下载米哈游游戏资源',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互式运行（推荐）
  python main.py

  # 下载原神 6.1.0 游戏本体 + 中文语音
  python main.py --game hk4e --version 6.1.0 --categories 10017,10018

  # 下载星穹铁道 4.2.0 仅游戏本体
  python main.py --game hkrpg --version 4.2.0

  # 指定输出目录和并发数
  python main.py --game hk4e --version 6.1.0 --output D:/games --concurrency 64

  # 使用配置文件
  python main.py --config config.yaml

  # 断点续传（直接重新运行即可，自动跳过已完成的 chunk）
  python main.py --game hk4e --version 6.1.0
        """,
    )

    # 游戏
    parser.add_argument('--game', '-g', choices=list(GAMES.keys()),
                        help='游戏 ID（hk4e=原神, hkrpg=星穹铁道, nap=绝区零, bh3=崩坏3）')
    parser.add_argument('--version', '-v', help='游戏版本号（如 6.1.0）')

    # 范围
    parser.add_argument('--categories', '-c',
                        help='下载类别 ID，逗号分隔（如 10017,10018）。留空则交互选择')
    parser.add_argument('--audio-langs', '-a',
                        help='语音包语言，逗号分隔（如 zh-cn,en-us）')

    # 输出
    parser.add_argument('--output', '-o', help='输出目录（默认 ./output）')
    parser.add_argument('--data-dir', '-d', help='pkg_version 数据目录（默认 ./pkg_version）')

    # 下载参数
    parser.add_argument('--concurrency', '-n', type=int, default=32,
                        help='最大并发下载数（默认 32）')
    parser.add_argument('--retries', type=int, default=3,
                        help='单 chunk 失败重试次数（默认 3）')
    parser.add_argument('--timeout', type=int, default=60,
                        help='请求超时秒数（默认 60）')
    parser.add_argument('--max-speed', type=int, default=0,
                        help='限速 MB/s（0=不限速）')

    # 校验
    parser.add_argument('--no-chunk-verify', action='store_true',
                        help='禁用 chunk 级 MD5 校验')
    parser.add_argument('--no-file-verify', action='store_true',
                        help='禁用文件级 MD5 校验')

    # 网络
    parser.add_argument('--proxy', help='HTTP 代理地址（如 http://127.0.0.1:7890）')

    # 日志
    parser.add_argument('--verbose', action='store_true', help='显示详细日志')
    parser.add_argument('--quiet', '-q', action='store_true', help='仅显示警告和错误')

    # 配置文件
    parser.add_argument('--config', help='YAML 配置文件路径')

    args = parser.parse_args()

    # 从配置文件加载
    file_config = {}
    if args.config:
        with open(args.config, 'r', encoding='utf-8') as f:
            file_config = yaml.safe_load(f) or {}

    # 合并配置：命令行 > 配置文件 > 默认值
    config = Config()

    if args.game or file_config.get('game'):
        config.game_id = args.game or file_config['game']
    if args.version or file_config.get('version'):
        config.version = args.version or file_config['version']
    if args.output or file_config.get('output'):
        config.output_dir = Path(args.output or file_config['output'])
    if args.data_dir or file_config.get('data_dir'):
        config.data_dir = Path(args.data_dir or file_config['data_dir'])

    if args.categories or file_config.get('categories'):
        cats = args.categories or ','.join(file_config['categories'])
        config.categories = [c.strip() for c in cats.split(',')]
    if args.audio_langs or file_config.get('audio_langs'):
        langs = args.audio_langs or ','.join(file_config['audio_langs'])
        config.audio_langs = [l.strip() for l in langs.split(',')]

    config.max_concurrency = args.concurrency or file_config.get('concurrency', 32)
    config.chunk_retries = args.retries or file_config.get('retries', 3)
    config.request_timeout = args.timeout or file_config.get('timeout', 60)
    config.max_speed = args.max_speed or file_config.get('max_speed', 0)

    if args.no_chunk_verify or file_config.get('no_chunk_verify'):
        config.verify_chunks = False
    if args.no_file_verify or file_config.get('no_file_verify'):
        config.verify_files = False

    if args.proxy or file_config.get('proxy'):
        config.use_proxy = True
        config.proxy = args.proxy or file_config['proxy']

    config.verbose = args.verbose
    config.quiet = args.quiet

    return config
