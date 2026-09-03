---
name: image-generation
description: AI 图片生成、编辑与透明背景后处理。使用统一 CLI 调用 OpenAI Images、Google Gemini 原生图片 API、火山方舟 Seedream，支持多参考图、mask、批量生成、Seedream 5.0 Pro 点选/框选连续编辑，并在配置可用时调用独立 matting skill 自动选择算法抠图；未配置或服务不可用时回退现有 chroma-key。用户提到生成图片、画图、封面图、配图、AI 生图、改图、修图、去背景、透明底、参考图编辑、Gemini 生图、Seedream 或交互编辑时使用。
allowed-tools: Bash(uv run *), Read, Grep, Glob, Edit
metadata:
  version: "26.36.56"
---

# Image Generation

通过一个严格配置的 CLI 生成或编辑位图。不要按模型名称猜协议，不要绕过配置 allowlist，也不要把远端 `/models` 返回的未配置模型视为可调用。

## 路径

- `{SKILL_DIR}`：本文件所在目录
- `{SCRIPTS_DIR}`：`{SKILL_DIR}/scripts`
- CLI 前缀：`uv run --project {SCRIPTS_DIR} imggen`

## 必须遵守

1. 优先使用本 skill 的 CLI 路径完成图片生成与编辑。
2. adapter 仅由 `provider/endpoint` 配置决定，只能是 `openai`、`gemini`、`seedream`。
3. 调用模型前，必须在所选 endpoint 的 `models.<exact-model-id>` 表中命中；未命中立即停止。
4. 每个 endpoint 独立配置 `adapter`、`base_url`、`api_key`/`api_key_env` 和模型 allowlist。禁止跨 endpoint 借用凭据或静默回退。
5. 只发送模型 policy 中明确声明的 capability。CLI 的显式参数不受支持时必须报错，不能丢弃。
6. `imggen models` 仅用于诊断远端可见性；远端可见但本地未配置的模型仍然被阻止。
7. 不输出、记录或回显 key。需要新 key、base URL 或模型授权时，请用户修改配置。

配置结构和迁移方式见 [references/configuration.md](references/configuration.md)，adapter/模型差异见 [references/capability-matrix.md](references/capability-matrix.md)。

## 工作流

### 1. 判断操作

- 纯文字新建图片：`generate`
- 一张或多张参考图上的语义修改：`edit`
- OpenAI alpha mask 局部修改：`edit --mask`
- Seedream 5.0 Pro 点选/框选并连续迭代：`interactive`
- 多个独立生成任务：`generate-batch`
- 一般去背景/透明底：`remove-background`（优先 matting，配置不可用时回退 `chroma-key`）
- 明确的纯色背景转透明或需要手调关键色：`chroma-key`

编辑前先查看参考图，确认用户指的是哪一侧、哪个主体或哪块区域。若目标清楚，直接执行；只有会实质改变结果的缺失信息才需要询问。

### 2. 选择 endpoint 和模型

```bash
uv run --project {SCRIPTS_DIR} imggen list
uv run --project {SCRIPTS_DIR} imggen models -p primary -e seedream
```

用户未指定时使用配置默认值。若默认模型没有本次操作或参数所需 capability，选择同一 endpoint allowlist 中明确支持的模型；不能自行切换 endpoint。

### 3. 准备 prompt

保留用户意图、人物身份和必须不变的内容。编辑 prompt 要同时写清：

- 要改变什么；
- 在哪里改变；
- 哪些内容必须保持不变；
- 构图、相机、光线、材质和文字要求。

CLI 默认把 prompt 和 `--scene/--subject/--style/...` 组织成结构化段落；原样发送时使用 `--no-augment`。完整字段见 [references/prompting.md](references/prompting.md)。

### 4. 先验证，再调用

高成本或复杂请求可先 `--dry-run`。dry-run 仍会执行配置、模型、文件和 capability 校验，但不会发网络请求或写图片。

```bash
uv run --project {SCRIPTS_DIR} imggen generate \
  --prompt "夜雨中的末日都市屋顶" \
  -p primary -e openai -m gpt-image-1.5 \
  --size 1536x1024 --quality high --output-format png \
  --out ./roof.png --dry-run
```

### 5. 生成或编辑

```bash
# 生成
uv run --project {SCRIPTS_DIR} imggen generate "电影感末日城市" \
  -p primary -e openai -m gpt-image-1.5 \
  --size 1536x1024 --quality high -o ./city.png

# 多参考图语义编辑；--image 可重复
uv run --project {SCRIPTS_DIR} imggen edit \
  --prompt "保留人物身份和服装，只调整腿部姿态" \
  --image ./scene.png --image ./character-sheet.png \
  -p primary -e gemini -m gemini-3-pro-image-preview \
  --aspect-ratio 9:16 --image-size 2K -o ./edited.png

# OpenAI mask 编辑
uv run --project {SCRIPTS_DIR} imggen edit \
  --prompt "只替换透明 mask 区域" --image ./input.png --mask ./mask.png \
  -p primary -e openai -m gpt-image-1.5 -o ./masked.png
```

`--out` 已存在时默认拒绝覆盖；明确覆盖才加 `--force`。`--downscale-max-dim` 会在原图之外生成带 `-small` 后缀的缩略副本。

### 6. Seedream 5.0 Pro 交互编辑

交互坐标必须是官方 `0–999` 坐标。已经是归一化坐标时直接传 `--point X,Y` 或 `--bbox X1,Y1,X2,Y2`；从展示像素坐标转换时同时传 `--canvas-size WIDTHxHEIGHT`。

```bash
# 创建会话并执行第一轮
uv run --project {SCRIPTS_DIR} imggen interactive start \
  --session ./outputs/edit-session.json --image ./input.png \
  --bbox 320,480,760,900 --canvas-size 1080x1440 \
  --prompt "将框内手提包替换为黑色手枪套" \
  -p primary -e seedream -m doubao-seedream-5-0-pro-260628

# 下一轮自动以上一轮成功产物作为参考图
uv run --project {SCRIPTS_DIR} imggen interactive edit \
  --session ./outputs/edit-session.json --point 520,640 --canvas-size 1080x1440 \
  --prompt "把此处金属扣改成暗红色"

# 状态查看与失败恢复
uv run --project {SCRIPTS_DIR} imggen interactive show --session ./outputs/edit-session.json
uv run --project {SCRIPTS_DIR} imggen interactive retry --session ./outputs/edit-session.json
```

可先在 `interactive start` 末尾追加 `--dry-run` 验证坐标、模型与 capability；dry-run 不创建 session。

会话固定 provider/endpoint/model，记录每轮输入、标注、引用图、参数、输出与失败信息；禁止中途换 endpoint。详细协议见 [references/seedream-interactive.md](references/seedream-interactive.md)。

### 7. 批量与透明背景

```bash
uv run --project {SCRIPTS_DIR} imggen generate-batch \
  --input ./jobs.jsonl --out-dir ./outputs --concurrency 3 \
  -p primary -e openai -m gpt-image-1.5

uv run --project {SCRIPTS_DIR} imggen chroma-key \
  --input ./green.png --out ./transparent.png \
  --auto-key corners --soft-matte --despill
```

普通去背景优先使用统一入口：

```bash
uv run --project {SCRIPTS_DIR} imggen remove-background \
  --input ./generated.png --out ./transparent.png
```

该命令先调用独立 `matting` skill 的实时状态与算法探测；只有 `[matting]` 配置有效且服务可用时才提交任务。配置缺失、无效、服务不可达或未安装 matting skill 时，会在结果 JSON 中写明 `fallback_reason`，再使用现有 `chroma-key`。一旦探测已成功，后续任务提交、轮询或下载失败会直接报错，不静默回退。算法选择与配置见 `../matting/SKILL.md`；语义复杂的人物、发丝、玻璃、烟雾等素材在执行前仍要查看原图。

JSONL、重试、输出与全部参数见 [references/cli.md](references/cli.md)。

## 交付

完成后报告 adapter、endpoint、模型、输出绝对路径和已执行的关键约束。若生成了图片，在支持本地媒体展示的客户端中显示最终图片。不要泄露凭据，也不要声称未实际验证的 provider 功能可用。

技术实现索引见 [reference.md](reference.md)。
