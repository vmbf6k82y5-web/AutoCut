# AutoCut

输入一部影视剧/电影的本地视频，**全自动**产出带 AI 解说配音和字幕的 MP4 解说视频。全程无需人工介入。

> 一个引擎，多种入口：CLI / 本地 Web GUI / 文件夹监听 / MCP Server，全部共用同一套代码。流水线由引擎确定性编排，LLM 只是被调用的函数。

## 功能特性

- 🎬 **全流程自动**：字幕提取 → 人名纠错 → 剧情大纲 → 画面初筛 → 画面理解 → 解说文案 + 时间轴 → 配音 → 时长对齐 → 渲染 → 质检 → 发布物料
- 🧠 **模型可切换**：DeepSeek / Gemini / OpenAI / Ollama / 各类中转，按角色分配 + fallback
- 🎙️ **AI 配音**：edge-tts（默认，免费）/ OpenAI TTS / 自部署 CosyVoice，支持情绪、读音字典、词级字幕
- 🖼️ **画面理解**：抽帧（省钱）或直接看视频（best 方案，Gemini 原生协议）
- 📱 **多画幅**：16:9 / 9:16（竖屏模糊背景填充）/ 1:1
- 🪝 **倒叙钩子**：高潮片段前置片头，悬念开场
- 🎵 **自动配乐**：按情绪选曲、交叉淡化、原声闪避
- 💰 **费用可控**：请求级缓存（重跑不重复花钱）、单集费用上限、预估费用
- 📦 **断点续跑**：每阶段落盘，崩溃/中断后从断点继续
- 🤖 **Agent 友好**：MCP Server + SKILL.md，可被外部 agent 调用

## 快速开始

### 环境要求
- Python 3.11+（用 [uv](https://docs.astral.sh/uv/) 管理）
- ffmpeg / ffprobe（**不打包进 exe**；首次启动自动检测，缺失时自动下载到 `./bin`，或在配置中指定路径）

### 安装
```bash
git clone <repo> && cd autocut
uv venv --python 3.11 && uv pip install -e ".[dev]"
```

### 配置
```bash
cp config.example.toml config.toml
# 编辑 config.toml：填入 [[models]] 的 api_key（用 env:VAR 引用环境变量）、[roles] 角色分配
```

最小配置（全 mock，零费用跑通流程）：
```toml
[[models]]
name = "mock-text"
protocol = "mock"
capabilities = ["text", "json_mode"]

[[models]]
name = "mock-vision"
protocol = "mock"
capabilities = ["text", "vision", "video", "json_mode"]

[roles]
name_fix = ["mock-text"]
outline = ["mock-text"]
scout = ["mock-text"]
vision = ["mock-vision"]
writer = ["mock-text"]
reviewer = ["mock-text"]
rewrite = ["mock-text"]
memory = ["mock-text"]
metadata = ["mock-text"]
style = ["mock-text"]
fixer = ["mock-text"]
```

### 运行
```bash
autocut run /path/to/剧集.mp4                 # 全流程
autocut gui                                    # 本地 Web 界面（exe 双击默认）
autocut estimate 电影.mp4                       # 预估费用（不花钱）
autocut rerun <job> --from write               # 从 write 阶段重跑
autocut series /剧集目录 --mode episodes        # 逐集处理
autocut watch /inbox                           # 监听文件夹自动处理
autocut mcp                                    # MCP Server
```

## 三档 profile

| profile | vision | scout 候选 | 自审 | 适用 |
|---|---|---|---|---|
| `economy` | 便宜模型 + 抽帧 | 25 | 否 | 省钱批量 |
| `balanced`（默认） | 强模型 + 抽帧 | 25 | 否 | 日常 |
| `best` | 强模型直接看 360p 短视频（需 video 能力） | 40 | reviewer 自审一轮 | 精品 |

优先级：`--role` > `--profile` > `[roles]` 默认。
```bash
autocut run 电影.mp4 --profile best --role writer=gemini-3.8f
```

## 产物
- 成片：`output/<标题>.mp4`
- 工作目录：`work/<视频名>/`（各阶段产物 + `manifest.json` + `run.log`）
- 发布物料：`work/<视频名>/12_publish/`（`cover.jpg`、`publish.json`）

## 打包 exe（Windows）
```bat
build_exe.bat
```
产物 `dist\autocut\autocut.exe`：双击打开 GUI，命令行可用 `autocut.exe run / mcp`。

## 常见问题

**找不到 ffmpeg**
安装 ffmpeg 并加入 PATH，或在 `[general] ffmpeg_path` 指定绝对路径，或放到项目 `./bin`。exe 首启会自动下载。

**能力校验报错：vision 需要图像理解能力**
`[roles] vision` 指向的模型需在 `[[models]]` 的 `capabilities` 里含 `"vision"`；`best` 方案还需要 `"video"`（走 gemini_native）。

**重跑会重复花钱吗？**
不会。所有 LLM 请求按内容哈希缓存到 `work/<视频>/cache`，命中缓存费用为 0。

**如何控制费用？**
设 `[limits] max_cost_per_episode`（USD），超限即停；先用 `autocut estimate` 预估。

**中途失败怎么办？**
每阶段写完成标记，`autocut run` 自动断点续跑；用 `autocut rerun <job> --from <阶段>` 强制从某阶段重跑。

**关闭某个可选功能？**
所有可选功能都能在配置关闭： `[story] hook=false`（倒叙钩子）、`[providers] hardsub="none"`（硬字幕处理）、`[audio] bgm_library=""`（配乐）、`[style] examples_dir=""`（风格学习）。关闭后流水线照常跑通。

## 文档
- 插件开发：[docs/PLUGINS.md](docs/PLUGINS.md)
- 设计决策：[docs/DECISIONS.md](docs/DECISIONS.md)
- 开发进度：[docs/PROGRESS.md](docs/PROGRESS.md)
- Agent 接入：[skills/autocut/SKILL.md](skills/autocut/SKILL.md)

## 许可证
MIT
