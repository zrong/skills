# matting-api 契约

CLI 使用以下端点：

- `GET /api/status`：服务、模型、队列状态；
- `GET /api/capabilities`：本次可用 methods、models 及可选能力；
- `POST /api/matting/generate`：multipart 字段 `image`、`method`、`model_key`、JSON 字符串 `parameters`；
- `GET /api/matting/tasks/{task_id}`：轮询 `pending|processing|completed|failed`；
- `GET /api/matting/download/{task_id}`：下载单图 PNG。

响应可为直接对象，或 Sorb 当前服务使用的包裹结构：

```json
{"success": true, "code": 2000, "message": "OK", "data": {}}
```

即使 HTTP 状态是 200，`success=false` 仍是业务失败。完成判定要求任务状态为 `completed`、下载内容可被 Pillow 解码、结果包含 alpha 通道，同时既有透明像素也保留了可见主体；单独的 health 或提交成功不代表最终成功。

CLI 兼容旧服务只返回 `methods`/`models` 的能力结构，也接受新版可选的 `model_methods` 映射。自动选择只使用已证明兼容的组合。
