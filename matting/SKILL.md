---
name: matting
description: 通过独立 matting-api 为本地图片抠图、去背景并输出透明 PNG。每次先读取服务状态、队列、实时算法和模型能力，再检查输入图的 alpha、边框颜色、均匀度与亮度特征，自动选择可用的 BiRefNet、InSPyReNet、Luma 或 CorridorKey 组合；也支持显式算法/模型。用户提到抠图、去背景、透明底、绿幕/蓝幕、人物/产品/Logo/特效分离，或 image-generation 生成图需要透明化时都应使用。配置缺失或服务不可用时，由调用方明确回退到现有本地方法，不能伪装成 matting-api 成功。
allowed-tools: Bash(uv run *), Read, Grep, Glob
metadata:
  version: "26.36.56"
---

# Matting

通过严格、非交互的 CLI 调用独立 `matting-api`。算法名、模型名和运行状态以本次服务探测结果为准；不要把 Sorb 数据库配置、历史文档或模型名称猜测当作实时能力。

## 路径

- `{SKILL_DIR}`：本文件所在目录
- `{SCRIPTS_DIR}`：`{SKILL_DIR}/scripts`
- CLI 前缀：`uv run --project {SCRIPTS_DIR} matting`

## 必须遵守

1. 处理图片前先查看原图，确认主体、背景、半透明边缘和用户要求。
2. 每次推荐或执行前都调用实时状态与能力端点；只从返回的算法和模型交集中选择。
3. 自动推荐是技术启发式：它能判断已有 alpha、纯色边框、绿/蓝幕和亮度型素材，不能仅凭统计可靠识别人像、发丝、玻璃或烟雾。语义证据更强时可显式覆盖算法，但仍要通过能力校验。
4. 配置缺失、无效、服务不可达或能力为空时明确报告不可用。不得声称已使用 matting-api；由上层流程决定是否回退。
5. 服务已经通过探测后，任务提交、轮询或下载失败必须如实失败，不能静默换算法或换成本地处理。
6. 输出只写 `.png`，默认拒绝覆盖；用户明确要求覆盖时才使用 `--force`。
7. 不输出认证 header、API key 或环境变量值。

## 工作流

### 1. 检查配置与实时状态

```bash
uv run --project {SCRIPTS_DIR} matting status
uv run --project {SCRIPTS_DIR} matting algorithms
```

`status` 同时请求 `/api/status` 与 `/api/capabilities`。`algorithms` 会列出服务本次广告的 method/model，以及 CLI 能证明兼容的组合。若服务只返回独立列表而没有 model-method 映射，CLI 只对已知协议或配置中显式声明的组合做自动选择。

配置查找顺序为：当前工作目录、skill 目录、当前 Git 项目根目录、`~/.agents/agent_config.toml`。也可用 `--config PATH` 或 `MATTING_CONFIG` 显式指定；显式路径不存在时停止。配置字段见 [references/configuration.md](references/configuration.md)。

### 2. 检查图片并获取推荐

```bash
uv run --project {SCRIPTS_DIR} matting inspect --input ./source.png
```

结果包含服务状态、实时能力、图像技术统计、推荐 method/model、理由和默认参数。自动选择规则见 [references/algorithm-selection.md](references/algorithm-selection.md)。

如果图片语义与统计冲突，使用显式覆盖：

```bash
uv run --project {SCRIPTS_DIR} matting inspect \
  --input ./portrait.png \
  --method birefnet --model birefnet-auto-quality --reprocess
```

### 3. 执行抠图

```bash
uv run --project {SCRIPTS_DIR} matting remove \
  --input ./source.png --out ./source-transparent.png
```

CLI 会再次实时探测，检查输入，选择算法和模型，提交异步任务，轮询终态，下载并验证透明 PNG，再原子写入目标文件。已有有效 alpha 时默认保留并规范化为 PNG；明确要重抠时加 `--reprocess`。

参数覆盖使用 JSON 对象：

```bash
uv run --project {SCRIPTS_DIR} matting remove \
  --input ./green-screen.png --out ./subject.png \
  --parameters-json '{"halo_pixels":1,"despill_strength":0.85}'
```

也可用 `--parameters-file ./params.json`。显式 `--method` 与 `--model` 必须成对兼容且在实时能力中存在。

高成本请求可先 `--dry-run`；它会完成配置、服务、能力、输入和选择校验，但不提交任务、不写输出。

## 与 image-generation 配合

当生成图需要去背景时，优先使用 image-generation 的统一入口：

```bash
uv run --project /path/to/image-generation/scripts imggen remove-background \
  --input ./generated.png --out ./generated-transparent.png
```

该入口只有在 `[matting]` 配置有效且实时探测成功时才调用本 skill。配置缺失或服务不可用时，它会明确报告回退原因，并使用 image-generation 现有的 `chroma-key`；服务已可用但任务执行失败时不会静默回退。

## 交付

完成后报告：

- 实际 backend（`matting-api`、`existing-alpha` 或调用方回退）；
- 服务版本和状态；
- method、model、选择理由；
- 输入和输出绝对路径；
- 是否发生覆盖、回退或显式参数覆盖。

不要把 health/HTTP 200 单独说成抠图成功；只有下载结果通过图片与 alpha 校验并写入目标路径才算完成。API 结构见 [references/api-contract.md](references/api-contract.md)。
