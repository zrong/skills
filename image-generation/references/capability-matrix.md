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
