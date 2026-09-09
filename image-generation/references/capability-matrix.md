# Adapter 与 capability 矩阵

配置 policy 是最终约束。下表描述 adapter 能表达的协议，不代表任一 key 自动获准使用全部功能。

| 能力 | OpenAI | Gemini native | Seedream |
|---|---|---|---|
| 文生图 | Images generations | `generateContent` | Ark images generations |
| 参考图编辑 | Images edits | prompt + `inlineData` 语义编辑 | generations 请求中的 `image` |
| 多参考图 | `multi_reference` | `multi_reference` | `multi_reference` |
| alpha mask | `mask` | 不发送 | 不发送 |
| size/quality | `size`, `quality` | 使用 `aspect_ratio`, `image_size` | `size` |
| 背景/输出压缩 | `background`, `output_format`, `output_compression` | 不发送 | 由 endpoint 固定 response format；CLI 不伪装成 OpenAI 参数 |
| seed/watermark | 不发送 | 不发送 | `seed`, `watermark` |
| 组图/流式 | 不发送 | `n` 通过独立请求实现 | `sequential`, `stream`，仅模型明确支持时 |
| 点选/框选编辑 | 不发送 | 可用自然语言语义编辑，不使用 Seedream 标签 | `interactive_edit`，仅 Seedream 5.0 Pro policy |

## capability 名称

- 通用：`multi_reference`, `size`
- OpenAI：`mask`, `quality`, `background`, `output_format`, `output_compression`, `moderation`, `input_fidelity`
- Gemini：`aspect_ratio`, `image_size`
- Seedream：`seed`, `stream`, `watermark`, `sequential`, `interactive_edit`

`max_references` 和 `max_outputs` 始终生效。即使存在 capability，超过数量也会拒绝。

## 原生透明背景（background）支持矩阵

`--background transparent` 只对 policy 声明了 `background` capability 的模型可用；其余模型会在请求前被拦截，去背景改用 `imggen remove-background`。

| 模型 | 原生透明底 | 说明 |
|---|---|---|
| 官方 gpt-image-2 | ✅（preview） | 官方 Images API 支持 `background=transparent`，仅 png/webp 输出带 alpha |
| 官方 gpt-image-1.5 | ✅ | 同上 |
| API易 gpt-image-2-vip | ❌ | 文档未提供 background 参数；同时拒绝 quality 与 n |
| API易 grok-imagine-image(-quality) | ❌ | 文档无背景参数，且不支持 mask |
| Nano Banana / Gemini 原生 | ❌ | 协议无背景参数 |
| Seedream 全系列 | ❌ | 由 endpoint 固定 response format |

透明背景输出只允许 PNG/WebP；`--output-format` 缺省时 Images API 默认 png，同样满足要求（jpeg 会被 CLI 拒绝）。

## API易网关模型差异（来自官方文档实测）

- **gpt-image-2-vip**：multipart 编辑，`image` 字段重复传多图（顺序对应 prompt「图1/图2/图3」）；`size` 传 `auto` 跟随 prompt 点名要修改的那张图比例，或 30 档固定尺寸锁定；固定单张输出（policy 用 `send_n = false` + `max_outputs = 1` 表达）；`b64_json` 历史上曾带 `data:` 前缀，adapter 已兼容两种形态。
- **grok-imagine-image / -quality**：编辑仅接受 multipart 文件上传（不支持 URL/base64 输入）；`image[]` 重复传 1–4 张，**第一张决定输出画幅**；`resolution`/`aspect_ratio` 传入不报错也不生效，因此 policy 不声明 size 类 capability；`n` 1–10 有效；不支持 mask；响应不返回 `revised_prompt`。
- **Nano Banana（gemini-3-pro-image-preview）**：走 Gemini 原生 `generateContent` 协议（JSON，非 multipart），结构为 1 个 text part（指令，放第一位）+ N 个 inlineData part（每张图一个 base64）；`imageConfig.aspectRatio`（10 种，默认 1:1）与 `imageSize`（1K/2K/4K，默认 1K）生效。

## 模型特点

### OpenAI Images

- generation 和 edit 使用不同路径；多参考图在 multipart 中重复 image field。
- mask 是 alpha mask 文件，不等于自然语言指定区域。
- 透明背景只允许 PNG/WebP；CLI 会在请求前验证。
- 代理站的固定字段可放 `model.options.payload`，协议仍由 endpoint 的 `adapter=openai` 决定。

### Gemini native

- 文字和每张参考图都作为 `contents[].parts[]` 发送。
- 编辑是语义编辑，可多图组合；当前 CLI 不把 mask、OpenAI quality 或压缩字段静默映射过去。
- `--aspect-ratio` 和 `--image-size` 映射到 `generationConfig.imageConfig`。
- `n > 1` 通过多次独立 generateContent 请求实现，并受 `max_outputs` 限制。

### Seedream

- generation/edit 使用同一个 Ark images generation 路径；有参考图时发送 `image` 或 image 数组。
- `seed`, `watermark`, `size` 按模型 policy 开启。
- 组图使用 `sequential_image_generation` 和 `sequential_image_generation_options.max_images`；不能给不支持组图的 Seedream 5.0 Pro 自动添加。
- 流式只在 policy 声明 `stream` 时发送，并解析 SSE data events。
- Seedream 5.0 Pro 的交互编辑以 prompt 中的归一化坐标标记实现，不是 mask API。会话链是 CLI 的可恢复编排层。

官方参考：

- [Gemini image generation and editing](https://ai.google.dev/gemini-api/docs/image-generation)
- [Volcengine ImageGenerations API](https://api.volcengine.com/api-docs/view?action=ImageGenerations&serviceCode=ark&version=2024-01-01)
- [Seedream 5.0 Pro 交互编辑指南](https://docs.volcengine.com/docs/82379/2582775?lang=zh)
- [OpenAI image generation 指南（gpt-image-2 透明背景 preview）](https://developers.openai.com/api/docs/guides/image-generation)
- [API易 gpt-image-2-vip 图片编辑](https://docs.apiyi.com/api-capabilities/gpt-image-2-vip/image-edit)
- [API易 Nano Banana 图片编辑](https://docs.apiyi.com/api-capabilities/nano-banana-image/image-edit)
- [API易 Grok Imagine 图片编辑](https://docs.apiyi.com/api-capabilities/grok-imagine-image/image-edit)
