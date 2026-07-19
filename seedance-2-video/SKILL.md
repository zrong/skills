---
name: seedance-2-video
description: 为 Sorb 的 Seedance 2.0 视频生成分析图片或参考视频，编写和优化中文视频提示词、图生视频方案、运镜设计、分镜板、首帧建议、长视频分段方案与剪辑节奏。用户明确提到 Seedance 2.0、图生视频、参考视频复刻、运镜、分镜、首尾帧、视频延长或长视频规划时使用；不要为普通图片生成或与 Seedance 无关的视频任务触发。
---

# Seedance 2.0 Video Director for Sorb

把用户的创意、Canvas 素材和 Sorb 当前模型能力整理为可以提交的 Seedance 2.0 生成方案。始终通过 Sorb 的模型配置和 Canvas Agent 工具工作；不要调用外部平台、命令行或未声明的脚本。

## 核心边界

1. 把 `get_generation_model_options` 返回的模型 schema 视为参数、时长、比例和参考数量的唯一运行时真值；写 Canvas 时只使用每个模型的 `canvas_form` 键和映射。
2. 使用当前视觉消息标注的引用槽位，不根据节点标题猜测编号；引用数量上限读取模型工具。图像、视频、音频引用分别写成 `@图像N`、`@视频N`、`@音频N`。
3. 先给出创意和提示词方案。需要修改 Canvas 或提交生成时，只使用 Sorb 已有的 `create_generation_node`、`update_generation_node`、`connect_reference` 和 `submit_generation`。
4. 尊重 Sorb 的审批、计费、项目权限和任务链。不要绕过审批，不要声称已经生成尚未提交的内容。
5. 用户引用了图片或视频时，先分析 Sorb 提供的视觉内容，再写提示词；不要仅凭文件名或节点标题推测画面。

## 路径选择

根据目标选择一条主路径，必要时组合参考文档：

- **A：单条短视频**。目标时长不超过所选模型单次上限，聚焦一个核心瞬间。读取 [creative-strategy.md](references/creative-strategy.md)，涉及复杂运镜时再读 [camera-codec.md](references/camera-codec.md)。
- **B：长视频或多镜头叙事**。读取 [production-pipeline.md](references/production-pipeline.md) 和 [long-video-strategy.md](references/long-video-strategy.md)，拆成多段生成和后期拼接方案。
- **C：图片驱动**。读取 [image-to-prompt.md](references/image-to-prompt.md)。图片是首帧、角色参考、场景参考还是目标尾帧必须明确。
- **D：分镜板驱动**。读取 [storyboard-driven.md](references/storyboard-driven.md)，按格数和视觉连续性决定单条时间轴或逐镜头生成。
- **参考视频复刻**。先描述参考视频的镜头运动、主体动作、节奏和转场，再明确哪些特征应复刻、哪些内容应替换。运镜细节读取 [camera-codec.md](references/camera-codec.md)。
- **首帧、角色图或关键帧准备**。读取 [image-generation.md](references/image-generation.md)，只生成适配 Sorb 图片模型的提示词或 Canvas 方案。
- **已有素材剪辑**。读取 [editing-rhythm.md](references/editing-rhythm.md)，输出节奏和镜头选择建议，不虚构不存在的剪辑工具。

需要画面质感时读取 [aesthetic-constraints.md](references/aesthetic-constraints.md)；需要措辞和完整例子时读取 [vocabulary.md](references/vocabulary.md) 与 [examples.md](references/examples.md)。不要一次加载所有 references。

## 工作流程

### 1. 获取模型与画布事实

1. 调用 `locate_canvas_nodes` 读取相关节点和当前 revision。
2. 调用 `get_generation_model_options`：
   - `capability="video"`；
   - 用户或节点已选模型时传 `model_config_id`；
   - 需要 Seedance 2.0 时传 `family="seedance-2"`。
3. 从工具结果确认可用模型、生成模式、时长、比例、分辨率和参考上限。
4. 若没有匹配模型，明确说明配置缺失并停止提交；不要退回硬编码参数。

### 2. 理解目标和素材

提取以下事实；缺失且会改变方案时再向用户确认：

- 核心主体、动作、场景和情绪；
- 目标时长与画幅；
- 图片和视频各自承担的用途；
- 是否需要对白、音效或音乐节奏；
- 用户要的是提示词、分镜方案，还是直接更新并提交 Canvas 节点。

分析视觉素材时，分别记录：主体一致性、空间关系、构图、镜头尺度、相机运动、时间节奏、光线色调、文字或水印风险。视频帧只代表抽样时刻，不要把未观察到的连续动作写成事实。

### 3. 设计生成方案

遵守以下原则：

- 一条短视频只突出一个视觉事件；前段尽快出现可感知变化。
- 参考素材只承担明确职责：图像锁定外观或构图，视频锁定运动、运镜或节奏，文字描述变化目标。
- 每个镜头优先使用一个主运动，最多组合两个相容轴向；避免堆叠互相冲突的镜头词。
- 长视频按镜头拆分，每段都定义进入状态、核心动作、结束状态和与下一段的衔接锚点。
- 提示词以清晰、可执行为先。删除同义词堆叠、无关画质标签和无法从参考素材验证的细节。

### 4. 输出

默认按以下结构回答：

```markdown
## 创作方向
[一句话核心概念和所选路径]

## 素材分工
- @图像1：[用途]
- @视频1：[用途]

## Seedance 提示词
[可直接写入 Sorb 生成节点的中文提示词]

## 生成参数
- 模型：[display_name / model_config_id]
- 时长：[schema 允许值]
- 比例与分辨率：[schema 允许值]
- 生成模式：[schema 允许值]

## 注意事项
[一致性、审核、素材或分段风险]
```

用户需要多个方向时最多给 3 个差异明确的版本，并说明每个版本的取舍。

### 5. 更新或提交 Canvas

1. 以工具返回的 `canvas_form.default_form` 为起点，只使用 `canvas_form.allowed_fields` 和 `canvas_form.fields` 构造 `form`；`params_schema.select` 映射为 `orientation`，`params_schema.aspect_ratio` 映射为 `ratio`，不要把这两个结构键直接写入 `form`。
2. 创建或更新生成节点后，使用最新 revision 继续连接素材。
3. 提交前复查引用数量、提示词 token、时长和生成模式。
4. 只有用户要求生成时才调用 `submit_generation`；审批结果由 Sorb 返回。

## 交付检查

- 已根据目标选择 A/B/C/D 或参考视频路径。
- 已读取该路径必要的 reference，未无差别加载全部资料。
- 已调用模型能力工具，没有硬编码模型支持范围。
- 已使用 `@图像N`、`@视频N`、`@音频N`，且编号来自工具结果。
- 已区分观察到的视觉事实与创意推断。
- 提示词没有外部执行指令、供应商凭据或旁路提交步骤。
- 如需生成，已走 Sorb Canvas 工具和审批链。
