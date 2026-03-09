# yt_download

一个基于 `yt-dlp` 的命令行下载工具，提供更友好的终端交互界面（TUI），用于下载单个视频、音频和播放列表内容。

当前仓库包含两部分：

- `yt-dlp-tui.py`：你自己的交互式下载脚本
- `yt-dlp/`：内置的 `yt-dlp` 源码目录，方便本地直接使用和二次修改

## 功能特性

- 交互式输入视频链接
- 自动读取并展示可下载格式
- 支持选择最佳画质、最差画质、仅音频、1080p、720p 等模式
- 支持播放列表/合集识别与批量下载
- 支持播放列表条目交互勾选
- 支持浏览器 Cookies 登录方案
- 支持中断后继续下载
- 支持清理 `.part`、`.aria2`、`.ytdl` 等临时文件
- 可选 `aria2c` 多线程下载加速

## 默认行为

- 默认下载目录：`/Users/menggq/Downloads`
- 主入口脚本：`yt-dlp-tui.py`
- 登录页打开方式：调用 macOS 的 `open` 命令
- 浏览器 Cookies：优先尝试 `chrome`，也支持 `safari`、`firefox`

这意味着当前版本更偏向 **macOS 本机环境**。如果你要在其他机器上运行，建议先修改脚本里的下载目录和系统调用逻辑。

## 环境要求

建议环境：

- Python 3.10+
- `yt-dlp`
- `prompt_toolkit`
- `rich`
- `readchar`
- 可选：`aria2c`

如果需要合并音视频，系统里还建议安装：

- `ffmpeg`

## 安装依赖

### 方式一：使用系统里的 `yt-dlp`

```bash
python3 -m pip install prompt_toolkit rich readchar yt-dlp
```

可选安装加速与转码工具：

```bash
brew install aria2 ffmpeg
```

### 方式二：使用仓库内置的 `yt-dlp` 源码

如果你希望优先使用仓库里的 `yt-dlp/`，可以在仓库根目录下运行：

```bash
python3 -m pip install prompt_toolkit rich readchar
PYTHONPATH=./yt-dlp python3 yt-dlp-tui.py
```

如果系统已经安装了 `yt-dlp` 命令，直接运行脚本也可以。

## 快速开始

在仓库根目录执行：

```bash
python3 yt-dlp-tui.py
```

启动后你可以：

- 输入视频 URL 下载单个视频
- 输入播放列表 URL 批量下载
- 输入 `clean` 清理未完成下载的临时文件
- 输入 `q` / `quit` / `exit` 退出程序

## 典型使用流程

### 下载单个视频

1. 启动脚本
2. 输入视频链接
3. 等待脚本读取视频信息
4. 选择目标格式
5. 选择普通下载或多线程下载
6. 文件保存到 `~/Downloads`

### 下载播放列表/合集

1. 输入播放列表链接
2. 脚本识别为合集后拉取条目
3. 在交互界面中勾选要下载的视频
4. 选择格式策略
5. 批量下载到以播放列表标题命名的目录

## 支持的下载能力

脚本内置了这些常见选项：

- `best`：最佳质量
- `worst`：最低质量
- `bestaudio`：仅最佳音频
- `best[height<=1080]`：最高到 1080p
- `best[height<=720]`：最高到 720p

同时脚本会展示 `yt-dlp` 返回的格式信息，包括：

- 分辨率
- FPS
- 文件大小或估算大小
- 视频/音频编码情况
- 是否为音视频合并格式

## 登录与受限内容

某些视频可能需要登录，例如：

- 年龄限制视频
- 私有视频
- 会员内容

脚本会在检测到需要登录时：

- 尝试提示登录
- 打开相关页面
- 使用浏览器 Cookies 再次请求

如果浏览器 Cookies 没有生效，请确认：

- 本机浏览器已经登录目标网站
- 浏览器允许本地读取 Cookies
- `yt-dlp` 本身支持该站点的 Cookies 提取方式

## 临时文件清理

脚本支持扫描并清理这些未完成下载文件：

- `*.aria2`
- `*.part`
- `*.part-*`
- `*.temp`
- `*.ytdl`
- HLS 片段文件

你可以：

- 启动后输入 `clean` 手动清理
- 在取消下载后根据提示选择是否清理

## 项目结构

```text
yt_download/
├── README.md
├── .gitignore
├── yt-dlp-tui.py
└── yt-dlp/
```

## 自定义配置

如果你要改成本机通用版本，优先改这几个地方：

- `yt-dlp-tui.py:24`：修改默认下载目录 `DOWNLOAD_PATH`
- `yt-dlp-tui.py:834` 附近：调整登录页打开方式
- `yt-dlp-tui.py` 中的浏览器 Cookies 选择逻辑：改成你的常用浏览器

例如，把下载目录改成当前用户目录下的 `Downloads`，可以把固定路径改为基于 `os.path.expanduser()` 的写法。

## 已知说明

- 当前脚本以本地桌面使用为主，不是一个 Web 服务
- 某些网站是否可下载，取决于 `yt-dlp` 的站点支持情况
- 多线程下载依赖 `aria2c`，未安装时请使用普通模式
- 合并音视频通常依赖 `ffmpeg`

## 常见问题

### 1. 运行时报找不到 `yt-dlp`

说明系统环境中没有可执行的 `yt-dlp`。可先执行：

```bash
python3 -m pip install yt-dlp
```

### 2. 下载失败或部分站点无法解析

先更新 `yt-dlp`，很多站点问题来自规则过旧：

```bash
python3 -m pip install -U yt-dlp
```

### 3. `aria2c` 不存在

直接使用普通下载模式，或者安装：

```bash
brew install aria2
```

### 4. 音视频没有自动合并

通常是因为本机缺少 `ffmpeg`：

```bash
brew install ffmpeg
```

## 后续可优化方向

- 增加 `requirements.txt`
- 增加启动参数支持
- 自动识别跨平台下载目录
- 增加 Windows / Linux 兼容逻辑
- 将脚本封装为可安装 CLI 工具

## License

本仓库中的 `yt-dlp/` 目录保留其上游项目各自的许可证与说明。

你自己的 `yt-dlp-tui.py` 和仓库组织方式，可根据你的发布需求再补充独立许可证。
