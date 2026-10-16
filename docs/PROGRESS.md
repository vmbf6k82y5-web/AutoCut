# AutoCut 开发进度

> 每个里程碑完成后更新。状态：✅ 完成 / 🚧 进行中 / ⬜ 未开始 / ⛔ 阻塞

## 里程碑总览

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M1 | 项目骨架、配置加载、provider 注册表、mock providers、测试素材、日志脱敏 | ✅ |
| M2 | 手写 Timeline → ffmpeg_mp4 出 MP4 | ✅ |
| M3 | probe/hardsub/subtitle/shots/loudness | ✅ |
| M4 | ModelClient + LLM 阶段（name_fix/outline/scout/vision/writer/reviewer）+ 风格学习 | ✅ |
| M5 | tts + align（原创度约束） | ✅ |
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

## M5 完成内容（2025-10-16）
- `stages/tts_providers.py`：edge_tts（词级 WordBoundary 时间戳 + 情绪 rate/pitch 映射）/
  openai_tts / custom_http（URL 模板）+ 读音字典加载与应用 + 字均分兜底
- `stages/tts_stage.py`：并行合成所有 narration 段，回写 tts_duration/audio_file
- `stages/align.py`：V>T 收缩吸附镜头边界 / V<T 延长避冲突 / speed 0.85–1.2 微调 /
  SRT 句边界吸附（0.3s 容差）/ 原创度三约束校验
- 11 个 M5 测试全部通过


## M4 完成内容（2025-10-16）
- `model_client.py`：统一 ModelClient 协议（chat_json/chat_with_images/chat_with_video）
  - openai 协议（OpenAI SDK，覆盖 DeepSeek/Gemini 兼容/OpenAI/Ollama/中转）
  - gemini_native 协议（google-genai，视频上传，仅 video 能力）
  - mock 协议（按 __role__ 标记返回确定性假数据，模拟 token）
  - ModelCaller：fallback 顺序、429/5xx 指数退避、max_concurrency 信号量限流、
    pydantic JSON 校验（失败回传重试 3 次 → fixer 修复 → 报错）、
    请求按内容哈希缓存（重跑不重复花钱）、CostTracker 计费 + max_cost 熔断
  - token 估算（中文 1 字 1 token）、上下文缓存命中 token 记录
- `prompt.py`：提示词加载（front-matter 版本、custom_dir 覆盖、哈希、缓存友好固定前缀）
- 11 个提示词（name_fix/outline/scout/vision/writer/reviewer/rewrite/memory/metadata/style/fixer）
- 阶段：name_fix（人名纠错+说话人映射）、outline（剧情节点）、scout（LLM+规则兜底）、
  frames/clips（抽帧/360p 短视频）、vision（批量抽帧/逐段视频）、write（含倒叙钩子+风格指南）、
  review（best 模式自审改稿）
- `style.py`：风格学习（范例哈希缓存 style_guide）
- 12 个 M4 测试全部通过（fallback/缓存/预算熔断/JSON 校验/fixer/并发/提示词版本/端到端）
- 全量 57 tests passing


## M3 完成内容（2025-10-16）
- `stages/probe.py`：ffprobe 元信息（时长/分辨率/帧率/音轨/字幕轨）→ 00_probe.json
- `stages/subtitle_providers.py`：embedded（抽内嵌软字幕 mov_text→srt）/file（用户 srt + 可选 ffsubsync）/
  openai_asr（OpenAI 兼容转写 + 说话人分离字段）/local_whisper（faster-whisper 可选）/auto（链式回退）
- `stages/shot_providers.py`：pyscenedetect（360p 代理分析）+ fixed 兜底；scenedetect 失败自动回退
- `stages/loudness_providers.py`：ffmpeg ebur128 + volumedetect 分窗，高能区间提取与归一化打分
- `stages/hardsub_providers.py`：opencv 底部 25% 区域 Canny 边缘密度 + 对比度检测（不用 OCR）+ none
- 修复 provider 注册表惰性重建（_ensure_builtins），解决 reset 后丢注册
- 15 个测试全部通过（含真实 ffmpeg 的镜头检测、内嵌字幕抽取、硬字幕正负例）


## M2 完成内容（2025-10-16）
- `render/ass.py`：词级时间戳 → ASS 字幕（断句 ≤max_chars、时间偏移、样式、Translation 样式）
- `render/ffmpeg_mp4.py`：完整渲染器
  - 逐段帧精确切片（并行，受 ffmpeg_concurrency 限制）
  - 9:16 模糊背景填充、16:9/1:1 letterbox
  - speed≠1 时 setpts/atempo 变速
  - narration 段原声×orig_volume 与 TTS 混音 + 首尾 afade
  - 自动配乐（BGM 循环铺满 + 音量压低）
  - concat demuxer 拼接
  - ASS 字幕烧录；硬字幕三模式（move_up 上移/bar 半透明底条/blur 模糊）
  - 编码器自动检测 nvenc→qsv→amf→libx264
- 10 个渲染测试全部通过（含 9:16、钩子乱序、硬字幕三模式、配乐、变速、编码器探测）


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
