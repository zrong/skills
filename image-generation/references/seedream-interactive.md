# Seedream 5.0 Pro 交互编辑

## 官方坐标协议

Seedream 5.0 Pro 用 prompt 内标记表达局部目标：

```text
<point>x y</point>
<bbox>x1 y1 x2 y2</bbox>
```

坐标范围为 `0..999`，左上角 `0,0`，右下角 `999,999`。点由模型判断影响范围；bbox 明确给出左上和右下。

从展示区域像素坐标换算时，CLI 使用官方公式：

```text
normalized_x = clamp(round(x_px / displayed_width  * 1000), 0, 999)
normalized_y = clamp(round(y_px / displayed_height * 1000), 0, 999)
```

传 `--canvas-size WIDTHxHEIGHT` 时，`--point/--bbox` 被视为展示像素坐标；不传时视为已经归一化。

## Prompt 发送形式

```text
编辑位置：<point>500 500</point> <bbox>120 180 640 760</bbox>
编辑指令：把框内人物换成机器人，点位置增加红色指示灯
```

坐标不代替自然语言：必须同时说明替换、保持或移动什么。当前 interactive 会话每轮使用一张最新参考图；普通 `imggen edit` 仍可向支持的 Seedream 模型提供多张参考图。

## 会话状态

session JSON 包含：

- 固定的 provider、endpoint、model；
- 初始参考图；
- 每轮原 prompt、渲染后 prompt、points/bboxes；
- 该轮请求参数和实际参考图；
- `pending/completed/failed` 状态、输出和错误。

写入使用同目录临时文件后原子替换。执行顺序是先记录 pending，再调用 API，最后写 completed 或 failed。因此进程中断后可检查并恢复。

下一次 `interactive edit` 只使用最后一个 completed turn 的第一张输出；不存在成功轮次时使用 initial reference。引用文件丢失会停止，不会回退到其他图片。

`interactive retry` 重放最后一个 pending/failed turn 的 prompt、引用图和请求参数。可以另行指定输出路径；模型与 endpoint 不可变。

## 模型 policy

交互命令同时要求：

- endpoint `adapter = "seedream"`；
- exact model policy 允许 `edit`；
- capabilities 包含 `interactive_edit`；
- 引用图和输出数量不超过 policy。

配置不满足时在网络请求前拒绝。

官方文档：[Doubao Seedream 5.0 Pro 实现交互编辑指南](https://docs.volcengine.com/docs/82379/2582775?lang=zh)。
