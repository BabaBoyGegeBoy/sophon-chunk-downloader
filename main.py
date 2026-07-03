"""
Sophon Chunk 下载器 - 主入口

用法:
    python main.py                          # 交互式
    python main.py --game hk4e --version 6.1.0 --categories 10017,10018
    python main.py --config config.yaml
"""
import json
import sys
import time
import logging

from config import build_config, Config
from utils import setup_logging, GAMES, format_size, format_time
from data_loader import (
    load_chunk_json, load_file_list, ensure_data_available,
    ChunkManifestInfo, GameFileRecord,
)
from manifest_parser import load_manifest
from checkpoint import Checkpoint
from file_assembler import FileAssembler
from verifier import verify_all_files
from chunk_scheduler import ChunkScheduler

logger = logging.getLogger(__name__)


def interactive_select(config: Config) -> Config:
    """交互式选择游戏、版本、下载范围"""
    print('=' * 60)
    print('  Sophon Chunk 下载器')
    print('=' * 60)

    # 选择游戏
    if not config.game_id:
        print('\n选择游戏:')
        games = list(GAMES.keys())
        for i, gid in enumerate(games):
            print(f'  {i + 1}. {GAMES[gid]["name"]} ({gid})')
        choice = input('\n请输入序号 (1): ').strip()
        idx = int(choice) - 1 if choice else 0
        config.game_id = games[idx]

    # 选择版本
    if not config.version:
        print(f'\n游戏: {GAMES[config.game_id]["name"]}')
        version = input('请输入版本号 (如 6.1.0): ').strip()
        if not version:
            print('错误: 必须指定版本号')
            sys.exit(1)
        config.version = version

    # 选择下载范围
    if not config.categories:
        print(f'\n版本: {config.version}')
        print('下载范围:')
        print('  1. 仅游戏本体')
        print('  2. 游戏本体 + 中文语音')
        print('  3. 游戏本体 + 所有语音')
        print('  4. 自定义 (输入 category ID)')
        choice = input('\n请选择 (2): ').strip() or '2'

        if choice == '1':
            config.categories = ['10017']
        elif choice == '2':
            config.categories = ['10017', '10018']
        elif choice == '3':
            config.categories = []  # 运行时从 chunk JSON 获取全部
        elif choice == '4':
            cats = input('输入 category ID (逗号分隔): ').strip()
            config.categories = [c.strip() for c in cats.split(',')]
        else:
            config.categories = ['10017', '10018']

    return config


def main():
    config = build_config()
    setup_logging(verbose=config.verbose, quiet=config.quiet)

    # 无完整参数时进入交互模式
    if not config.game_id or not config.version:
        config = interactive_select(config)

    # 确保 categories 非空
    if not config.categories:
        config.categories = ['10017', '10018']

    game_name = GAMES.get(config.game_id, {}).get('name', config.game_id)
    logger.info('=' * 60)
    logger.info(f'游戏: {game_name} ({config.game_id})')
    logger.info(f'版本: {config.version}')
    logger.info(f'范围: categories={config.categories}')
    logger.info(f'并发: {config.max_concurrency}')
    logger.info(f'输出: {config.output_dir}')
    logger.info(f'断点续传: 启用 ({config.checkpoint_path})')
    logger.info(f'校验: chunk级={config.verify_chunks}, 文件级={config.verify_files}')
    logger.info('=' * 60)

    total_start = time.time()

    # Phase A: 数据加载
    logger.info('\n[Phase A] 数据加载...')

    logger.info('  A1. 检查数据文件...')
    if not ensure_data_available(config):
        logger.error('数据文件不可用，无法继续')
        sys.exit(1)

    logger.info('  A2. 读取 chunk JSON...')
    manifest_infos = load_chunk_json(config)

    # 如果 categories 为空或需要补全，从 chunk JSON 获取全部
    if not config.categories or config.categories == []:
        config.categories = [m.category_id for m in manifest_infos]
        logger.info(f'    自动检测到 categories: {config.categories}')

    for info in manifest_infos:
        logger.info(f'    [{info.category_id}] {info.category_name}')
        logger.info(f'      manifest_id: {info.manifest_id}')

    logger.info('  A3. 读取文件清单...')
    file_lists = {}
    for info in manifest_infos:
        records = load_file_list(config, info.category_id, info)
        file_lists[info.category_id] = records
        total_size = sum(r.file_size for r in records)
        logger.info(f'    [{info.category_id}] {info.category_name}: '
                     f'{len(records)} 文件, {format_size(total_size)}')

    logger.info('  A4. 加载 manifest...')
    manifests_data = []
    for info in manifest_infos:
        parsed_files = load_manifest(info, config)
        manifests_data.append((info, parsed_files))

    # Phase B: 索引构建
    logger.info('\n[Phase B] 索引构建与去重...')

    checkpoint = Checkpoint(config)
    checkpoint.load()

    assembler = FileAssembler(config)
    scheduler = ChunkScheduler(config, checkpoint, assembler)
    scheduler.build_chunk_index(manifests_data, file_lists)

    # Phase C: 下载与写入
    logger.info('\n[Phase C] 下载与写入...')

    logger.info('  C1. 预分配文件空间...')
    all_records = []
    for records in file_lists.values():
        all_records.extend(records)
    assembler.preallocate(all_records)

    logger.info('  C2. 开始并发下载...')
    report = scheduler.run()

    logger.info('  C3. 刷盘...')
    assembler.finalize_all()

    logger.info(f'\n  下载报告:')
    logger.info(f'    总 chunk 数: {report.total_chunks}')
    logger.info(f'    成功: {report.completed_chunks}')
    logger.info(f'    失败: {report.failed_chunks}')
    logger.info(f'    跳过(checkpoint): {report.skipped_chunks}')
    logger.info(f'    下载量: {format_size(report.total_bytes_downloaded)}')
    logger.info(f'    耗时: {format_time(report.total_elapsed)}')
    if report.total_elapsed > 0:
        avg_speed = report.total_bytes_downloaded / report.total_elapsed
        logger.info(f'    平均速度: {avg_speed / (1024**2):.1f} MB/s')

    if report.failed_chunks > 0:
        logger.warning(f'\n  {report.failed_chunks} 个 chunk 下载失败:')
        for r in report.failed_list[:10]:
            logger.warning(f'    {r.chunk_id}: {r.error}')
        if len(report.failed_list) > 10:
            logger.warning(f'    ... 共 {len(report.failed_list)} 个失败')
        logger.info(f'\n  可重新运行程序继续下载（断点续传会跳过已完成的 chunk）')

    # Phase D: 文件级校验
    logger.info('\n[Phase D] 文件级 MD5 校验...')

    file_results = []
    if config.verify_files and report.failed_chunks == 0:
        file_results = verify_all_files(all_records, config)
        file_pass = sum(1 for r in file_results if r.passed)
        file_fail = sum(1 for r in file_results if not r.passed)
        logger.info(f'  校验结果: 通过 {file_pass}, 失败 {file_fail}, 总计 {len(file_results)}')

        if file_fail > 0:
            logger.warning(f'\n  {file_fail} 个文件校验失败:')
            for r in file_results:
                if not r.passed:
                    logger.warning(f'    {r.file_path}')
                    logger.warning(f'      expected: {r.expected}')
                    logger.warning(f'      actual:   {r.actual}')
    elif config.verify_files and report.failed_chunks > 0:
        logger.info(f'  跳过文件校验（有 {report.failed_chunks} 个 chunk 下载失败）')
    else:
        logger.info('  文件级校验已禁用')

    # 保存报告
    total_elapsed = time.time() - total_start
    final_report = {
        'config': {
            'game_id': config.game_id,
            'version': config.version,
            'categories': config.categories,
        },
        'download': {
            'total_chunks': report.total_chunks,
            'completed_chunks': report.completed_chunks,
            'failed_chunks': report.failed_chunks,
            'skipped_chunks': report.skipped_chunks,
            'total_bytes_downloaded': report.total_bytes_downloaded,
            'download_elapsed': round(report.total_elapsed, 1),
        },
        'verify': {
            'file_total': len(file_results),
            'file_passed': sum(1 for r in file_results if r.passed),
            'file_failed': sum(1 for r in file_results if not r.passed),
        },
        'total_elapsed': round(total_elapsed, 1),
    }

    config.output_dir.mkdir(parents=True, exist_ok=True)
    with open(config.verify_report_path, 'w', encoding='utf-8') as f:
        json.dump(final_report, f, ensure_ascii=False, indent=2)

    logger.info(f'\n{"=" * 60}')
    logger.info(f'完成! 总耗时: {format_time(total_elapsed)}')
    logger.info(f'校验报告: {config.verify_report_path}')
    logger.info(f'输出目录: {config.output_dir}')

    if report.failed_chunks > 0:
        logger.info(f'\n有 {report.failed_chunks} 个 chunk 失败，请重新运行程序继续下载。')
        sys.exit(1)

    logger.info(f'\n所有文件下载并校验完成!')
    logger.info('=' * 60)


if __name__ == '__main__':
    main()
