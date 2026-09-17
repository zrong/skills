# Upscale API 合同

CLI 使用以下端点：

| 方法与路径 | 用途 |
| --- | --- |
| `GET /health` | 进程状态与版本 |
| `GET /api/status` | GPU/队列/任务实时状态 |
| `GET /api/capabilities` | 模型、媒体类型、输入限制、默认模型与提交合同 |
| `POST /api/tasks/upload` | multipart 上传并提交，文件字段为 `file` |
| `GET /api/tasks/{id}` | 查询任务与进度 |
| `GET /api/tasks/{id}/download` | completed 后下载 PNG 或 MP4 |

图片字段：`media_type=image`、`model`、`scale`。视频字段：`media_type=video`、`model`、`scale`、`target_width`、`target_height`、`fit`、`start`、`duration`。`scale` 仅允许2或4，并与目标宽高互斥；`fit=cover` 必须同时给出宽高。省略尺寸字段时默认把短边放大到1080。输入短边至少64、总像素不超过16MP；有效倍率不超过4，输出不超过3840边长或4K总像素。CLI 不使用 `input_path` 合同，因为调用方不能假设与服务共享文件系统。

已知非终态为 `queued`、`running`、`cancelling`；成功终态为 `completed`；失败终态为 `failed`、`cancelled`。未知状态按协议错误处理，不无限等待。

完成后的绝对下载地址为：

```text
{base_url}/api/tasks/{task_id}/download
```

地址是否能从用户所在网络访问、是否需要网关认证，取决于部署环境。CLI 返回地址但不会把它声称为公网匿名链接。
