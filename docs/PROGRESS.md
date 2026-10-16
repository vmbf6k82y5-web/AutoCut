# AutoCut 开发进度

> 每个里程碑完成后更新。状态：✅ 完成 / 🚧 进行中 / ⬜ 未开始 / ⛔ 阻塞

## 里程碑总览

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 项目骨架、配置加载、provider 注册表、mock providers、测试素材、日志脱敏 | ✅ |
| M2 | 手写 Timeline → ffmpeg_mp4 出 MP4 | ⬜ |
| M3 | probe/hardsub/subtitle/shots/loudness | ⬜ |
| M4 | ModelClient + LLM 阶段（name_fix/outline/scout/vision/writer/reviewer）+ 风格学习 | ⬜ |
| M5 | tts + align（原创度约束） | ⬜ |
| M6 | CLI 全流程 + 断点续跑 + 并发 + qc + manifest + estimate | ⬜ |
| M7 | 剧集记忆 + metadata 发布物料 | ⬜ |
| M8 | MCP Server + SKILL.md | ⬜ |
| M9 | GUI + watch 模式 | ⬜ |
| M10 | eval 评测 + GitHub Actions | ⬜ |
| M11 | 多集合集 + 跨语言 + jianying_draft | ⬜ |
| M12 | PyInstaller 打包 + ffmpeg 首启检测 + README + PLUGINS.md | ⬜ |

## 环境说明
- 本机为 Linux 沙箱（无系统 ffmpeg、Python 3.10）；已通过 uv 安装 Python 3.11，ffmpeg 7.0.2 静态构建置于 `~/tools/ffmpeg`（测试经 `conftest.py` 注入）。
- Windows/macOS 上走标准 PATH 或 `./bin` 检测。

## 阻塞
（暂无）

## M1 完成内容（2025-10-16）
- pyproject.toml（uv + hatchling，含 entry points 声明、dev extras）
- `config.py`：pydantic v2 全配置模型（[[models]]/[roles]/[profiles]/各功能段）、
  角色-能力校验（中文报错）、优先级 CLI>profile>默认、`env:XXX` 密钥引用、
  配置 TOML 序列化/读回（GUI 写回用）、manifest 脱敏快照
- `providers/`：注册表 + entry points 发现 + 内置 mock provider（subtitle/shots/loudness/hardsub/tts/renderer）
- `logging_utils.py`：脱敏 filter（Bearer/key=参数/api_key JSON/sk- 前缀/运行期注册密钥）
- `utils.py`：ffmpeg/ffprobe 定位（配置→override→./bin→PATH）、SRT 解析/写、哈希
- `contracts.py`：Timeline/Segment/Outline/Candidate/QC 等核心契约（含钩子复用 ≤2 校验）
- `pipeline.py`：确定性编排器（断点续跑、--from 失效）
- `cli.py`：全部子命令骨架（run/rerun/estimate/series/eval/models/gui/watch/mcp/gen-test-media）
- `scripts/make_test_media.py`：ffmpeg 生成 60s 视频/SRT/内嵌字幕/硬字幕/镜头切换/风格范例
- 20 个测试全部通过，ruff 全过
