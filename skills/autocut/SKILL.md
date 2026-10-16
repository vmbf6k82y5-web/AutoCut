---
name: autocut
description: 输入影视剧/电影本地视频路径，全自动产出带 AI 解说配音和字幕的 MP4 解说视频。用于影视解说二创、批量剧集处理、无人值守产出。
---

# AutoCut Skill

教外部 agent 如何安装、配置和调用 autocut，把一个影视剧视频全自动变成带 AI 解说配音和字幕的解说视频。

## 何时使用
- 用户给你一个本地影视剧/电影视频，想要「解说视频」「影视剪辑带配音」「二创解说」。
- 需要批量处理一部剧的多集，或把多集合成一个合集解说。
- 需要以 MCP 工具形式被调用（`autocut mcp`）。

## 安装
```bash
# 需要 Python 3.11+ 与 ffmpeg（ffmpeg 不在 exe 内，首启自动检测/下载到 ./bin）
git clone <repo> && cd autocut
uv pip install -e ".[dev]"        # 或 uv sync
# 可选能力：
uv pip install "autocut-ai[whisper]"   # 本地转写
uv pip install "autocut-ai[gemini]"    # Gemini 原生视频理解（best 方案）
uv pip install "autocut-ai[jianying]"  # 剪映草稿导出
```

## 配置（config.toml）
复制 `config.example.toml` 为 `config.toml`，最少要配：
1. `[[models]]` 至少一个文本模型（writer/outline 等）和一个 vision 模型（画面理解）。
   - `api_key` 用 `env:VAR_NAME` 引用环境变量，勿明文提交。
2. `[roles]` 把角色映射到模型名（列表 = fallback 顺序）。
3. `[providers]` 选择 subtitle/tts/renderer 等 provider。
4. `[tts] voice` 选音色（默认 edge-tts 的 zh-CN-YunxiNeural）。
5. `[limits] max_cost_per_episode` 设单集费用上限。

无真实 API key 时可全用 `mock` 模型跑通流程（用于测试与演示）。

## 调用方式

### 命令行
```bash
autocut run /path/to/剧集.mp4                      # 全流程
autocut run 电影.mp4 --profile best               # 用 best 方案（vision 看视频 + 自审）
autocut run 电影.mp4 --role writer=gemini-3.8f    # 临时改 writer 角色模型
autocut rerun <job> --from write                   # 从 write 阶段重跑
autocut estimate 电影.mp4 --profile balanced       # 预估 token 与费用（不花钱）
autocut series /剧集目录 --mode episodes           # 逐集
autocut series /剧集目录 --mode compilation        # 多集合集
autocut models list                                # 列出模型
autocut models test deepseek-4.1f                  # 测试连通性
autocut gui                                        # 本地 Web 界面
autocut watch /inbox                               # 监听文件夹自动处理
```

### MCP（stdio）
```bash
autocut mcp
```
暴露工具：`autocut_run(video_path, config_overrides?)`、`autocut_status(job_id)`、
`autocut_result(job_id)`、`autocut_estimate(video_path, profile?)`、`autocut_get_config`、
`autocut_list_providers`、`autocut_list_models`、`autocut_test_model`、`autocut_set_role`、
`autocut_list_profiles`。

典型 agent 流程：
1. `autocut_estimate` 预估费用，确认在预算内。
2. `autocut_run` 启动任务得到 job_id。
3. 轮询 `autocut_status` 直到 done。
4. `autocut_result` 取输出路径、QC、费用与发布物料。

## 产物
- 成片：`output/<标题>.mp4`
- 工作目录：`work/<视频名>/`（各阶段产物 + manifest.json + run.log）
- 发布物料：`work/<视频名>/12_publish/`（cover.jpg、publish.json）

## 三档 profile
- `economy`：便宜模型 + 抽帧，候选 25，省钱。
- `balanced`（默认）：强 vision + 抽帧，候选 25。
- `best`：vision 直接看 360p 短视频（需 video 能力，走 gemini_native），候选 40，启用 reviewer 自审一轮。

## 常见问题
- **找不到 ffmpeg**：安装 ffmpeg 或在 `[general] ffmpeg_path` 指定，或放 `./bin`。
- **能力校验报错**：vision 角色需要带 `vision` 能力的模型；best 方案需要 `video` 能力。
- **断点续跑**：每阶段写完成标记，重跑自动跳过；`--from <阶段>` 强制重跑。
- **控制费用**：所有请求按内容哈希缓存；设 `max_cost_per_episode` 超限即停。

## 许可证
MIT。
