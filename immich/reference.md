# Immich API 参考

## 认证

使用 `x-api-key` header：

```
x-api-key: your-api-key
```

## Base URL

配置中的 `base_url` 不包含 `/api`，客户端会自动添加：

```
{base_url}/api
```

## 主要 Endpoints

### Assets

| 操作 | Endpoint | 方法 |
|------|----------|------|
| 上传资源 | `/api/assets` | POST |
| 获取资源 | `/api/assets/{id}` | GET |
| 更新资源时间或描述 | `/api/assets/{id}` | PATCH |

### Albums

| 操作 | Endpoint | 方法 |
|------|----------|------|
| 获取 Albums | `/api/albums` | GET |
| 创建 Album | `/api/albums` | POST |
| 获取 Album | `/api/albums/{id}` | GET |
| 删除 Album | `/api/albums/{id}` | DELETE |
| 添加资源到 Album | `/api/albums/{id}/assets` | PUT |
| 移除资源 | `/api/albums/{id}/assets` | DELETE |

## 上传请求格式

```
POST /api/assets
Content-Type: multipart/form-data

assetData: <binary data>
deviceAssetId: <unique client id>
deviceId: <client name>
fileCreatedAt: <ISO 8601 timestamp with timezone>
fileModifiedAt: <ISO 8601 timestamp with timezone>
```

响应：
```json
{
  "id": "asset-uuid",
  "status": "created"
}
```

Immich 会异步提取媒体内嵌时间。需要按上传时间排列时，等待资源返回
`hasMetadata=true`，再 PATCH `dateTimeOriginal`；skill 默认自动执行：

```json
{
  "dateTimeOriginal": "2026-07-15T08:30:00Z",
  "description": "原标题 #话题"
}
```

## 创建 Album 请求格式

```
POST /api/albums
Content-Type: application/json

{
  "albumName": "Album Name"
}
```

## 添加资源到 Album 请求格式

```
PUT /api/albums/{albumId}/assets
Content-Type: application/json

{
  "ids": ["asset-id-1", "asset-id-2"]
}
```

## 参考链接

- API 文档：https://api.immich.app/
- 官方文档：https://docs.immich.app/
