# 算法选择

CLI 的图片检查只提供技术信号，Agent 在执行前还要查看图片并结合用户描述。

| 图像证据 | 首选方法 | 说明 |
| --- | --- | --- |
| 已有明显透明/半透明像素 | 保留现有 alpha | 除非用户明确要求重抠或修边 |
| 均匀绿幕/蓝幕，且 CorridorKey 可用 | `birefnet_corridorkey_refine`，其次 `corridorkey_refine` | 组合方法更重但更适合发丝、软边和溢色 |
| 暗底亮特效或亮底暗特效 | `birefnet_luma_restore` | CLI 会建议对应 `luma_polarity` |
| 普通照片、人物、产品、Logo、复杂背景 | `birefnet` | 优先 auto-quality，其次固定 HR/general/lite |
| BiRefNet 不可用，服务提供 InSPyReNet | `inspyrenet` | Base 偏质量，Fast 偏速度 |

自动方法候选和模型候选都必须与实时 `/api/capabilities` 取交集。若没有已证明兼容的组合，停止并要求显式选择，不发送试探性任务。

以下语义无法由像素统计单独确认：人像、毛发、玻璃、烟雾、液体、反射、薄纱、软阴影、多主体。用户描述或视觉检查明确包含这些内容时，优先语义抠图；纯色算法仅用于背景确实受控的素材。

CorridorKey 组合只有服务报告 `corridorkey.available=true` 时才可选。`inspyrenet` 只有服务广告该 method 和相应模型时才可选。不得因为本地源码支持或历史服务支持而跳过实时检查。
