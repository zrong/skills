---
name: upscale
description: 通过独立 upscale-api 对本地或 FileBrowser 中的图片、视频执行超分、AI增强、尺寸缩放或RIFE补帧，轮询异步任务并提供下载地址。支持最快、标准、高质量三档预设，也支持精确模型名和基于实时能力的模型建议；FileBrowser 输入会复用 filebrowser skill 下载，并把结果回传到原文件同目录。用户提到超分、放大图片、提升视频分辨率、高清修复、视频补帧、24fps转60fps、upscale、FileBrowser 文件超分或超分后回传时使用。
---

# Upscale

通过非交互 CLI 调用独立 `upscale-api`。服务状态、模型、输入限制和提交端点以本次实时探测结果为准，不把历史部署信息当作当前能力。

## 执行入口

```bash
uv run --project {SKILL_DIR}/scripts upscale --non-interactive <command>
```

配置查找顺序为：当前工作目录、skill 目录、当前 Git 项目根目录、`~/.agents/agent_config.toml`。也可用 `--config PATH` 或 `UPSCALE_CONFIG` 显式指定；显式路径不存在时停止。配置字段见 [references/configuration.md](references/configuration.md)。

## 必须遵守

1. 每次推荐或提交前读取 `/health`、`/api/status` 和 `/api/capabilities`；`/health` 成功只证明进程存活，不证明 GPU 空闲或模型权重可用。
2. 按下文的模型选择优先级确定模型；无论来自精确名称、快捷档还是建议，都必须确认模型存在于实时能力中且支持当前媒体类型，并在提交时显式传入。
3. 本地输出和 FileBrowser 远端输出默认拒绝覆盖；只有用户明确要求替换时才加 `--force`。
4. 任务只有到达 `completed`、结果成功下载并通过媒体校验后才算超分完成。排队、HTTP 202 或可访问的下载端点都不等于完成。
5. FileBrowser 传输必须调用独立 `filebrowser` skill 的 `get`/`put`，不得在本 skill 内实现 FileBrowser HTTP。
6. 不输出认证 header、API key、FileBrowser token 或环境变量值。

## 查看服务与模型

```bash
uv run --project {SKILL_DIR}/scripts upscale --non-interactive status
uv run --project {SKILL_DIR}/scripts upscale --non-interactive models
```

## 取消任务

只有用户明确要求取消指定任务时，才执行取消操作。先读取任务状态，再调用 API：

```bash
# 发送取消请求并返回当前状态
uv run --project {SKILL_DIR}/scripts upscale --non-interactive cancel <task-id>

# 等待取消或任务完成的终态
uv run --project {SKILL_DIR}/scripts upscale --non-interactive cancel <task-id> --wait
```

排队任务会直接取消；运行中的任务先进入 `cancelling`，服务停止推理进程后才会变为 `cancelled`。取消和完成可能并发，因此 `--wait` 也可能返回 `completed` 或 `failed`。终态任务不重复发起取消。详见 [API合同](references/api-contract.md)。

## 模型选择

按以下优先级选择，不能用低优先级规则覆盖用户更明确的选择：

1. 用户直接给出支持的模型 ID 或名称时，精确选择该模型。
2. 用户给出快捷档时，使用固定映射：

   | 用户意图 | 方案 | 模型 ID |
   | --- | --- | --- |
   | 最快、速度优先、快速方案 | 最快方案 | `realcugan-pro-x2`（Real-CUGAN Pro x2） |
   | 标准、均衡、默认方案 | 标准方案 | `realesrgan-x2plus`（Real-ESRGAN x2plus） |
   | 高质量、质量优先 | 高质量方案 | `seedvr2-3b`（SeedVR2 3B） |

3. 用户没有表达模型或档位偏好时，默认使用标准方案 `realesrgan-x2plus`，不要直接采用 API 的 `submission_defaults`。
4. 用户要求推荐、素材需求明显特殊，或想比较模型时，读取 `/api/capabilities` 的 `models`、`selection_profiles`、`recommendation`、`cautions` 与 benchmark，结合媒体类型、动漫/真人、内容一致性、速度、显存和是否接受生成式细节给出建议。

快捷档是本 skill 的固定操作约定，不代表脱离具体素材的普遍质量排名。尤其 `seedvr2-3b` 可能生成或改变细节；角色、Logo、文字、小图案需要严格一致时必须提示风险。预设或精确指定的模型若未被当前 API 广告，或不支持当前媒体类型，应停止并说明，同时可基于实时能力列出替代建议；不得静默换模型。

详细建议方法见 [references/model-selection.md](references/model-selection.md)。

## 本地文件超分

省略 `--output` 时，结果下载到输入文件同目录，并按实际输出信息命名：

```bash
uv run --project {SKILL_DIR}/scripts upscale --non-interactive run \
  --input ./source.png --model realesrgan-x2plus --scale 2
```

例如输入`source.png`实际输出2048×1536时，默认文件名为`source_2048x1536_realesrgan-x2plus.png`。显式提供`--output`时使用调用方给出的文件名。

最快或高质量方案只需替换 `--model`：

```bash
# 最快方案
uv run --project {SKILL_DIR}/scripts upscale --non-interactive run \
  --input ./source.png --model realcugan-pro-x2 --scale 2

# 高质量方案
uv run --project {SKILL_DIR}/scripts upscale --non-interactive run \
  --input ./source.png --model seedvr2-3b --scale 2
```

同时下载到本地：

```bash
uv run --project {SKILL_DIR}/scripts upscale --non-interactive run \
  --input ./source.png --output ./source_upscaled.png --scale 2
```

视频用 `--mode` 明确处理语义：

| 模式 | 行为 | 省略尺寸时 |
| --- | --- | --- |
| `upscale` | AI 超分且结果必须变大；`--scale` 只接受 2 或 4 | 短边低于1080时到1080；1080及以上朝2×放大并受4K上限约束 |
| `enhance` | AI 处理后允许保持、缩小或放大 | 保持原尺寸 |
| `resize` | 只用 Lanczos 缩放，不加载超分模型 | 不允许省略，必须给倍率或目标尺寸 |

`--scale` 与 `--target-width`/`--target-height` 互斥；enhance/resize 的倍率范围为0.25到4。`--fit contain` 保留完整画面，`--fit cover` 必须同时提供宽高并允许居中裁切。视频仍可使用 `--start`/`--duration`。图片只支持upscale，接受大于1且不超过4的 `--scale`，不接受目标尺寸、fit、时间或补帧参数。输出格式由服务合同固定为图片 PNG、视频 MP4。

```bash
# 480p 视频严格放大 2 倍
uv run --project {SKILL_DIR}/scripts upscale --non-interactive run \
  --input ./source-480.mp4 --scale 2

# 保持比例放大到 1080 高度
uv run --project {SKILL_DIR}/scripts upscale --non-interactive run \
  --input ./source-480.mp4 --target-height 1080

# 1080p 原尺寸 AI 增强
uv run --project {SKILL_DIR}/scripts upscale --non-interactive run \
  --input ./source-1080.mp4 --mode enhance

# 只把 1080p 缩到 720p
uv run --project {SKILL_DIR}/scripts upscale --non-interactive run \
  --input ./source-1080.mp4 --mode resize --target-height 720

# 同时超分到1080p并从24fps补到60fps
uv run --project {SKILL_DIR}/scripts upscale --non-interactive run \
  --input ./source-720-24fps.mp4 --target-height 1080 --target-fps 60 \
  --interpolation-model rife-v4.25
```

`--target-fps` 只接受高于输入帧率、不超过120且倍率不超过4的目标，也可写成 `60000/1001`。省略 `--interpolation-model` 时显式采用默认的 `rife-v4.25`。`rife-v4.25-lite` 在hc88的20秒人物样本中少占约0.12 GiB补帧显存，但没有表现出稳定速度优势；不要仅凭Lite名称承诺更快，业务侧应先复测典型片段。详细合同见 [references/api-contract.md](references/api-contract.md)。

高成本任务可先加 `--dry-run`。本地模式会读取实时状态与能力并校验输入和模型；FileBrowser 模式只验证远端路径规划、FileBrowser 配置与实时模型，因不下载文件体而不能证明远端媒体可解码。两种模式都不会上传、提交任务或写结果。

## FileBrowser 文件超分并回传

```bash
uv run --project {SKILL_DIR}/scripts upscale --non-interactive filebrowser \
  --path "/项目/素材/shot.png" --source production --scale 2
```

默认回传到输入文件同目录，并在任务完成后根据实际结果生成名称：视频为`输入文件名_短边p_实际fps_模型名称.mp4`，例如`shot_1080p_60fps_realcugan-pro-x2.mp4`；图像为`输入文件名_WxH_模型名称.png`。可用 `--output` 指定另一个 FileBrowser 绝对路径，但仍必须是与媒体类型匹配的 `.png` 或 `.mp4`。流程为：

1. 用 `filebrowser get` 下载输入到隔离临时目录；
2. 调用 upscale-api、轮询终态并下载校验结果；
3. 用 `filebrowser put` 回传；
4. 返回 API `download_url`、FileBrowser source、远端输出路径和字节数。

`--local-output PATH` 可额外保留一份本地结果。未指定时本地临时文件会清理。`--force` 同时允许覆盖显式本地输出和远端结果。

## 交付

完成后报告：

- API 服务版本、task ID、实际模型和媒体类型；
- 任务终态与 API 下载地址；
- 若下载到本地：输出绝对路径、字节数和媒体校验结果；
- 若来自 FileBrowser：source、输入路径、同目录输出路径和回传字节数；
- 是否发生覆盖、显式模型/参数覆盖或失败。

API 请求与终态字段见 [references/api-contract.md](references/api-contract.md)。API 下载地址可能仍受所在网络和网关认证限制；不要把“已生成地址”表述为公网匿名可访问。
