# Upscale API 合同

CLI 使用以下端点：

| 方法与路径 | 用途 |
| --- | --- |
| `GET /health` | 进程状态与版本 |
| `GET /api/status` | GPU/队列/任务实时状态 |
| `GET /api/capabilities` | 模型、媒体类型、输入限制、默认模型与提交合同 |
| `POST /api/tasks/upload` | multipart 上传并提交，文件字段为 `file` |
| `GET /api/tasks/{id}` | 查询任务与进度 |
| `POST /api/tasks/{id}/cancel` | 取消排队任务，或请求终止运行中的任务 |
| `GET /api/tasks/{id}/download` | completed 后下载 PNG 或 MP4 |

图片字段：`media_type=image`、`model`、`scale`。视频字段：`media_type=video`、`model`、`mode`、`scale`、`target_width`、`target_height`、`fit`、`start`、`duration`、`target_fps`、`interpolation_model`。CLI 不使用 `input_path` 合同，因为调用方不能假设与服务共享文件系统。

## 视频模式与尺寸

- `mode=upscale`：AI超分且输出必须变大；倍率仅允许2或4。省略尺寸时，短边低于1080则到1080，1080及以上朝2×放大，受4K边界约束。
- `mode=enhance`：执行AI处理，最终尺寸可保持、缩小或放大；省略尺寸时保持输入尺寸。倍率范围0.25到4。
- `mode=resize`：只执行Lanczos缩放；必须提供倍率或至少一个目标边长。倍率范围0.25到4。
- 倍率与目标宽高互斥；`fit=cover` 必须同时给出宽高。输入短边至少64、总像素不超过16MP；输出边长不超过3840且总像素不超过4K。

## 补帧

`target_fps` 可用数字或有理数字符串，例如 `60`、`60000/1001`；它必须高于输入帧率、不超过120，且补帧倍率不超过4。`interpolation_model` 可选 `rife-v4.25` 或 `rife-v4.25-lite`，省略时使用前者。只提供模型而没有目标帧率是合同错误。补帧可与三个视频模式组合，音轨会重封装到最终MP4。

hc88 RTX 3090 的20秒720p24→1080p60组合链路中，RIFE 4.25补帧58.43秒、峰值保留显存2.04 GiB；Lite补帧59.54秒、1.92 GiB。该单样本没有证明Lite更快，调用方不能从模型名推导速度承诺。

已知非终态为 `queued`、`running`、`cancelling`；成功终态为 `completed`；失败终态为 `failed`、`cancelled`。未知状态按协议错误处理，不无限等待。

取消只能针对非终态任务。排队任务会直接变为 `cancelled`；运行中的任务先变为 `cancelling`，再由服务停止推理子进程。CLI 的 `cancel <task_id>` 只请求取消并返回即时状态；加 `--wait` 会轮询到 `cancelled`、`completed` 或 `failed`，以处理取消与任务完成并发时的竞态。

完成后的绝对下载地址为：

```text
{base_url}/api/tasks/{task_id}/download
```

地址是否能从用户所在网络访问、是否需要网关认证，取决于部署环境。CLI 返回地址但不会把它声称为公网匿名链接。

下载响应和CLI默认保存使用实际输出信息命名：

- 视频：`输入文件名_短边p_实际fps_模型ID.mp4`，例如`shot_1080p_60fps_realcugan-pro-x2.mp4`。
- 图像：`输入文件名_实际宽x实际高_模型ID.png`，例如`photo_2048x1536_realesrgan-x2plus.png`。

竖屏视频同样使用短边作为p值，因此1080×1920输出标记为`1080p`。非整数帧率最多保留三位小数，例如60000/1001写为`59.94fps`。显式指定本地或FileBrowser输出路径时保留调用方文件名。
