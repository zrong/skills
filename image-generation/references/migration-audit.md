# System imagegen 迁移审计

源目录只读：`/Users/zrong/.codex/skills/.system/imagegen/`。

| 系统能力 | image-generation v26.29.14 |
|---|---|
| `generate` | 统一 `imggen generate`，由 endpoint adapter 执行 |
| `edit`、多 `--image`、`--mask` | `imggen edit`，policy 校验多参考图和 mask |
| `generate-batch` JSONL、并发、fail-fast | 同名命令，固定 endpoint、job 可切 allowlist 内模型 |
| prompt / prompt-file | positional、`--prompt`、`--prompt-file` 三选一 |
| structured augmentation | `prompting.py`，同一组 use-case/scene/subject/style 等字段 |
| size/quality/background/format/compression/moderation/input-fidelity | provider-neutral request + model capability 校验 |
| dry-run | 配置、模型、文件、参数、输出校验后打印脱敏请求，不发网络 |
| 输出命名、防覆盖、out-dir | `output.py`；写入前 preflight，格式与扩展名不一致时转码 |
| downscale | Pillow 保留原图并生成后缀副本 |
| 瞬时错误重试 | 超时、连接错误及指定 408/409/429/5xx 指数退避 |
| GPT Image 2 尺寸规则 | 配置驱动 `size_rules`，不在代码中按模型名猜测 |
| chroma key、auto-key、soft matte、despill、contract、feather | `imggen chroma-key` / `chroma.py` |
| CLI 参数与 prompting 参考 | `references/cli.md`、`prompting.md` |

新增能力：

- OpenAI/Gemini/Seedream 独立 adapter；
- endpoint 独立凭据和 exact model allowlist；
- capability matrix 与参数前置拒绝；
- Gemini 原生 generateContent 多图语义编辑；
- Seedream 生成、编辑、组图、stream、seed、watermark；
- Seedream 5.0 Pro 官方 point/bbox 标注和可恢复 session。

没有复制的宿主专属内容：

- Codex 内建图片工具的路由和网络沙箱说明；
- `agents/openai.yaml` 与 Codex UI 图标资源；
- 系统 skill 的安装位置和内建 tool 调用约定。

这些不是 CLI 功能，且继续由 Codex 系统目录管理。系统 skill 本身未修改。

## 验证记录

- 自动化回归：24 个测试覆盖配置查找优先级、endpoint/model 严格 allowlist、三类 adapter 参数映射、透明输出、批处理防覆盖、交互 session 和 chroma-key。
- 旧/新 CLI 对照：`generate`、`edit`、`generate-batch` 的 dry-run 均保留 prompt augmentation、模型、尺寸、质量、格式和目标路径语义；batch job 的 `out` 始终约束在 `--out-dir` 内。
- APIYi 实际调用：OpenAI、Gemini、Seedream 的生成与编辑均已产出有效图片；Gemini 与 Seedream 的多参考编辑已验证。
- SiliconFlow 实际调用：OpenAI-compatible adapter 已产出有效图片。
- 火山方舟实际调用：`doubao-seedream-5-0-pro-260628` 已完成两轮连续编辑；第二轮 session 明确引用第一轮输出，session 中不含凭据字段。
- 可复用技能评测题位于 `evals/evals.json`，覆盖 OpenAI mask/透明输出、Gemini 多参考语义编辑和 Seedream 5.0 Pro 连续交互。
