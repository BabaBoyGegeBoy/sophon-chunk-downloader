"""
工具函数：日志配置、格式化、游戏定义
"""
import logging
import sys

# 支持的游戏列表
GAMES = {
    'hk4e':  {'name': '原神',           'repo': 'hk4e',  'audio_langs': ['zh-cn', 'en-us', 'ja-jp', 'ko-kr']},
    'hkrpg': {'name': '崩坏：星穹铁道',  'repo': 'hkrpg', 'audio_langs': []},
    'nap':   {'name': '绝区零',          'repo': 'nap',   'audio_langs': ['zh-cn', 'en-us', 'ja-jp', 'ko-kr']},
    'bh3':   {'name': '崩坏3',           'repo': 'bh3',   'audio_langs': []},
}

# 语音包文件名映射
AUDIO_LANG_FILES = {
    'zh-cn': 'Audio_Chinese_pkg_version',
    'en-us': 'Audio_English(US)_pkg_version',
    'ja-jp': 'Audio_Japanese_pkg_version',
    'ko-kr': 'Audio_Korean_pkg_version',
}

# pkg_version 仓库的基础 URL（用于自动下载数据文件）
PKG_VERSION_REPO = 'https://github.com/orilights/pkg_version'


def setup_logging(verbose: bool = False, quiet: bool = False):
    """配置日志系统"""
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)


def format_size(size_bytes: int) -> str:
    """将字节数格式化为易读字符串"""
    if size_bytes == 0:
        return '0 B'
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f'{size:.2f} {units[i]}'


def format_time(seconds: float) -> str:
    """将秒数格式化为易读时间"""
    if seconds < 60:
        return f'{seconds:.1f}s'
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f'{minutes}m {secs:.0f}s'
    hours = int(minutes // 60)
    mins = minutes % 60
    return f'{hours}h {mins}m'
