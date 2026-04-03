# Immich API 参考

## 认证

使用 `x-api-key` header：

```
x-api-key: your-api-key
```

## Base URL

```
{base_url}/api
```

## 主要 Endpoints

### Assets

| 操作 | Endpoint | 方法 |
|------|----------|------|
| 上传资源 | `/api/assets` | POST |
| 获取资源 | `/api/assets/{id}` | GET |

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

file: <binary data>
```

响应：
```json
{
  "id": "asset-uuid",
  "status": "created"
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
