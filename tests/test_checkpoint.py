"""
checkpoint 单元测试

覆盖：
  - test_save_and_load    : 保存后重新加载验证一致性
  - test_mark_completed   : 标记完成后验证 is_completed 返回 True
  - test_save_if_needed   : 阈值刷盘逻辑
"""
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，以便直接 import 项目模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Config
from checkpoint import Checkpoint


def _make_config(tmp_path: Path) -> Config:
    """构造一个 output_dir 指向临时目录的 Config。"""
    config = Config()
    config.output_dir = tmp_path
    return config


def test_save_and_load(tmp_path):
    config = _make_config(tmp_path)

    # 写入端：标记若干 chunk 并强制刷盘
    cp = Checkpoint(config)
    cp.mark_completed('chunk_a')
    cp.mark_completed('chunk_b')
    cp.mark_completed('chunk_c')
    cp.save(force=True)

    # 读取端：新建实例从磁盘加载
    cp2 = Checkpoint(config)
    cp2.load()

    assert cp2.is_completed('chunk_a') is True
    assert cp2.is_completed('chunk_b') is True
    assert cp2.is_completed('chunk_c') is True
    assert cp2.is_completed('chunk_x') is False
    assert cp2.completed == {'chunk_a', 'chunk_b', 'chunk_c'}


def test_mark_completed(tmp_path):
    config = _make_config(tmp_path)
    cp = Checkpoint(config)

    # 初始状态：无已完成 chunk
    assert cp.is_completed('chunk_1') is False

    # 标记完成后应命中
    cp.mark_completed('chunk_1')
    assert cp.is_completed('chunk_1') is True
    assert cp.is_completed('chunk_2') is False

    # 重复标记应幂等
    cp.mark_completed('chunk_1')
    assert cp.is_completed('chunk_1') is True
    assert cp.stats()['completed'] == 1


def test_save_if_needed(tmp_path):
    config = _make_config(tmp_path)
    cp = Checkpoint(config)
    path = config.checkpoint_path

    # 标记数 < 阈值：不应触发落盘
    for i in range(99):
        cp.mark_completed(f'chunk_{i}')
    cp.save_if_needed(threshold=100)
    assert not path.exists(), 'dirty_count 低于阈值时不应写入 checkpoint 文件'

    # 再标记 1 个 -> 达到阈值：应触发落盘
    cp.mark_completed('chunk_99')
    cp.save_if_needed(threshold=100)
    assert path.exists(), 'dirty_count 达到阈值时应写入 checkpoint 文件'

    # 校验落盘内容可被重新加载，且数量一致
    cp2 = Checkpoint(config)
    cp2.load()
    assert cp2.stats()['completed'] == 100

    # 落盘后 dirty_count 归零：无新增标记时再次调用不应重写
    mtime_before = path.stat().st_mtime_ns
    cp.save_if_needed(threshold=100)
    assert path.stat().st_mtime_ns == mtime_before, '无新增 dirty 时不应重写文件'
