# 设计决策记录

> 按「遇到不确定的设计，选最简单可行的方案」原则记录。

## D1. 流水线编排：确定性而非 LLM 驱动
LLM 只作为各阶段的被调函数（返回 JSON），流程先后由引擎 `Pipeline` 按 `STAGE_ORDER` 确定性编排。
**理由**：可断点续跑、可测试、可控成本；LLM 不决定流程，避免不可预测。

## D2. 字幕优先用内嵌/文件，ASR 兜底
subtitle 的 `auto` 顺序：embedded → file → openai_asr → local_whisper。
**理由**：内嵌字幕最准且零成本；ASR 是最后手段。

## D3. vision 默认抽帧，best 才看视频
抽帧成本低且覆盖多数场景；`best` 用 gemini_native 上传 360p 短视频换质量。
**理由**：成本与质量的分层，economy/balanced 用抽帧即可。

## D4. TTS 默认 edge-tts
免费、稳定、带词级 WordBoundary 时间戳（字幕对齐必需）。openai_tts/custom_http 为可替换项。
**理由**：零成本满足默认需求，词级时间戳是 ASS 字幕的关键。

## D5. mock 模型用 __role__ 标记路由
mock 模型无法靠内容关键字可靠区分角色（提示词互相包含关键字），改为 ModelCaller 注入 `{"__role__": role}` system 标记。
**理由**：确定性、零歧义，测试可靠。

## D6. 渲染用逐段切片 + concat 而非单次 filtergraph
每段单独重编码（帧精确、可控 speed/混音），再 concat demuxer 拼接。
**理由**：单个超长 filtergraph 难调试且易内存溢出；分段可并行、可断点。

## D7. ffmpeg 不打包进 exe
首启检测 → ./bin → PATH → 自动下载静态构建。
**理由**：ffmpeg 体积大且有许可考量；自动下载对用户最省事。

## D8. 时长对齐优先吸附镜头/字幕边界，speed 微调兜底
V>T 收缩到镜头边界；V<T 延长避冲突；仍不齐则 speed 0.85–1.2；再不行 rewrite。
**理由**：吸附边界最自然，speed 是小幅度补救，rewrite 是最后手段（花钱）。

## D9. 评测裁判复用 reviewer 角色
eval 的裁判直接用 reviewer（带评分 schema），不新增角色。
**理由**：职责一致，复用配置与 fallback。

## D10. GUI 用 FastAPI + 原生 HTML/JS
不引前端框架，静态页 + REST + WebSocket。
**理由**：打包小、无构建步骤、PyInstaller 友好。

## D11. 配置 TOML 序列化用递归发射器
`dump_config_toml` 递归处理嵌套表/表数组，中文键加引号，保证 GUI 写回可被 `load_config` 无损读回。
**理由**：GUI 设置页与 CLI 共用同一份 config.toml。

## D12. estimate 用经验系数而非真实调用
各角色按字幕字数 × 经验系数估 token，只跑 probe + 字幕统计。
**理由**：估算必须零成本；mock 实测偏差在验收容差内。
