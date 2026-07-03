# Sophon Chunk Downloader

通过 Sophon chunk 机制下载米哈游游戏资源（原神 / 星穹铁道 / 绝区零 / 崩坏3）的 Python 工具。

本工具实现了 Sophon 分发系统的客户端解析与下载流程：从 chunk JSON 钥匙文件出发，解析 manifest protobuf 描述，下载并解压 ZSTD 压缩的 chunk 块，最终在本地拼装还原为完整的游戏文件。

## 功能特性

- **chunk 全局去重**：跨文件、跨 category 的 chunk 按 `chunk_id` 全局去重，相同 chunk 只下载一次，可写入多个目标位置，显著减少重复下载量。
- **32 路并发**：基于线程池的并发下载引擎，默认 32 路并发，可通过 `--concurrency` 调整，充分利用带宽。
- **流式写入**：chunk 解压后按 `offset` 直接写入预分配的目标文件对应位置，避免全量缓存内存爆炸。
- **断点续传**：基于 checkpoint 机制记录已完成 chunk，中断后重新运行会自动跳过已完成部分，继续下载剩余 chunk。
- **双重 MD5 校验**：
  - chunk 级校验：每个 chunk 解压后立即校验 MD5，确保单块数据完整。
  - 文件级校验：所有 chunk 写入完成后，对最终文件做整体 MD5 校验，与 `pkg_version` 清单中的期望值比对。

## 原理说明

Sophon 分发采用三层数据结构，本工具按顺序逐层解析：

1. **chunk JSON 钥匙文件**（`pkg_version/chunk/<game>_<version>.json`）
   - 入口文件，记录该版本所有 category 的元信息。
   - 包含每个 category 的 `manifest_id`、`chunk_url_prefix` / `chunk_url_suffix`（拼出 chunk 下载 URL）、`category_name` 等。
   - 是后续定位 manifest 和 chunk 的“钥匙”。

2. **manifest protobuf**（按 `manifest_id` 拉取）
   - 一个 protobuf 编码的二进制描述文件，定义该 category 下所有文件的组成。
   - Schema 概要：
     ```protobuf
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
     ```
   - 本工具内置纯 Python 手写的 protobuf wire format 解析器，无需 `protoc` 或生成代码。
   - manifest 描述了“每个文件由哪些 chunk、按什么偏移拼装而成”。

3. **chunk ZSTD 压缩块**（从 CDN 下载）
   - 每个 chunk 是一个独立的 ZSTD 压缩二进制块，有自己的 `checksum`（MD5）。
   - 下载后用 `zstandard` 解压，校验 MD5，再按 `offset` 写入对应文件。
   - 同一个 chunk 可能被多个文件引用（去重的核心）。

整体流程：`chunk JSON` → 定位 `manifest` → 解析得到文件/chunk 映射 → 下载去重后的 chunk → 解压校验 → 按偏移写入 → 文件级 MD5 校验。

## 安装步骤

### 环境要求

- Python 3.10 及以上
- 网络可访问米哈游 CDN（如需使用代理，参见 `--proxy` 参数）

### 安装依赖

```bash
cd sophon-chunk-downloader
pip install -r requirements.txt
```

### 准备 pkg_version 数据仓库

本工具依赖 [orilights/pkg_version](https://github.com/orilights/pkg_version) 仓库提供的 manifest / CDN 地址等元数据。请将该仓库克隆到本工具的 `data_dir`（默认 `./pkg_version`）目录：

```bash
# 在 sophon-chunk-downloader 目录下
git clone https://github.com/orilights/pkg_version.git pkg_version
```

克隆后目录结构应为：

```
pkg_version/
├── bh3/                # 崩坏3 各版本 pkg_version 清单
├── hk4e/               # 原神 各版本 pkg_version 清单
├── hkrpg/              # 星穹铁道 各版本 pkg_version 清单
├── nap/                # 绝区零 各版本 pkg_version 清单
├── chunk/              # chunk JSON 钥匙文件（<game>_<version>.json）
├── usm/                # usm 相关历史/密钥
├── bh3_versions.json
├── hk4e_versions.json
├── hkrpg_versions.json
└── nap_versions.json
```

如不克隆到默认位置，可通过 `--data-dir` 指定其他路径。

## 使用方法

本工具支持交互式和命令行两种使用方式。

### 方式一：交互式（推荐首次使用）

直接运行，按提示选择游戏、版本、下载范围：

```bash
python main.py
```

交互流程会依次询问：
1. 游戏序号（原神 / 星穹铁道 / 绝区零 / 崩坏3）
2. 版本号（如 `6.1.0`）
3. 下载范围（仅本体 / 本体+中文语音 / 本体+所有语音 / 自定义 category）

### 方式二：命令行参数

```bash
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
```

### 命令行参数说明

| 参数 | 简写 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--game` | `-g` | — | 游戏 ID：`hk4e`=原神, `hkrpg`=星穹铁道, `nap`=绝区零, `bh3`=崩坏3 |
| `--version` | `-v` | — | 游戏版本号，如 `6.1.0` |
| `--categories` | `-c` | `10017` | 下载类别 ID，逗号分隔（如 `10017,10018`）。留空则交互选择 |
| `--audio-langs` | `-a` | — | 语音包语言，逗号分隔（如 `zh-cn,en-us`） |
| `--output` | `-o` | `./output` | 输出目录 |
| `--data-dir` | `-d` | `./pkg_version` | pkg_version 数据目录 |
| `--concurrency` | `-n` | `32` | 最大并发下载数 |
| `--retries` | — | `3` | 单 chunk 失败重试次数 |
| `--timeout` | — | `60` | 请求超时秒数 |
| `--max-speed` | — | `0` | 限速 MB/s（`0`=不限速） |
| `--no-chunk-verify` | — | — | 禁用 chunk 级 MD5 校验 |
| `--no-file-verify` | — | — | 禁用文件级 MD5 校验 |
| `--proxy` | — | — | HTTP 代理地址（如 `http://127.0.0.1:7890`） |
| `--verbose` | — | — | 显示详细日志 |
| `--quiet` | `-q` | — | 仅显示警告和错误 |
| `--config` | — | — | YAML 配置文件路径 |

配置优先级：**命令行参数 > 配置文件 > 默认值**。

### 使用配置文件

将 `config.example.yaml` 复制为 `config.yaml` 并按需修改，然后通过 `--config config.yaml` 加载。

## 目录结构

```
sophon-chunk-downloader/
├── main.py                 # 主入口：编排 A 数据加载 / B 索引构建 / C 下载写入 / D 文件校验 四个阶段
├── config.py               # 配置中心：命令行参数 + 配置文件 + 默认值三级合并
├── utils.py                # 工具函数：日志、格式化、支持的游戏列表定义
├── data_loader.py          # 数据加载：读取 chunk JSON、文件清单、数据可用性检查
├── manifest_parser.py      # Manifest 解析：ZSTD 解压 + 纯 Python protobuf wire format 解码
├── chunk_scheduler.py      # 调度引擎：chunk 去重索引、并发下载、整合解压/校验/写入
├── file_assembler.py       # 文件组装：预分配空间、按偏移写入 chunk、刷盘
├── checkpoint.py           # 断点续传：记录/加载已完成 chunk 集合
├── verifier.py             # 校验：chunk 级与文件级 MD5 校验
├── requirements.txt        # Python 依赖
├── config.example.yaml     # 配置文件示例
├── Dockerfile              # 容器化构建文件
├── LICENSE                 # MIT 许可证
└── README.md               # 项目说明（本文件）
```

运行后会在 `output` 目录下生成：
- 下载的游戏文件
- `.checkpoint.json`：断点续传记录
- `.manifest_cache/`：manifest 缓存
- `verify_report.json`：下载与校验报告

## 免责声明

本项目**仅供学习与研究 Sophon 分发机制**使用。

- 用户**必须拥有合法的游戏授权**方可下载和使用相关游戏资源。
- 米哈游（miHoYo / HoYoverse）保留所有游戏资源的版权与商标。
- 本项目**不得用于任何商业用途或非法分发**。
- 使用本工具下载的所有内容由使用者自行承担责任，作者不对任何因使用本工具而产生的直接或间接后果负责。
- 请在下载后 24 小时内删除，并购买/使用正版游戏。

## 数据来源说明

本工具所需的 manifest 地址、CDN 地址以及各版本 `pkg_version` 清单均来自开源仓库 [orilights/pkg_version](https://github.com/orilights/pkg_version)。该仓库整理并公开了米哈游各游戏版本的元数据信息，本工具仅在此基础上实现客户端解析与下载逻辑。

## License

本项目基于 [MIT License](./LICENSE) 开源，版权所有 © 2026 Contributors。
