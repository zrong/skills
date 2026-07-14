# ghcr.io 镜像加速 & Immich v3 数据库迁移

本机 ghcr.io 直连不稳定（大 blob TLS handshake timeout / short read），
即使通过 Clash 代理也经常中断。以下是可靠的工作方案。

## ghcr.io 镜像加速（南大镜像站）

南大镜像站 `ghcr.nju.edu.cn` 可直接替代 `ghcr.io`，速度稳定。

### 方法：临时替换 compose.yaml 中的镜像地址

```bash
# 1. 备份 compose.yaml
cp compose.yaml compose.yaml.bak

# 2. 替换 ghcr.io 为南大镜像
sed -i 's|ghcr.io/|ghcr.nju.edu.cn/|g' compose.yaml

# 3. 拉取
docker compose pull

# 4. 恢复 compose.yaml
cp compose.yaml.bak compose.yaml && rm compose.yaml.bak

# 5. 将南大镜像 tag 回 ghcr.io 原始名称（compose up 需要原始名称）
docker tag ghcr.nju.edu.cn/immich-app/immich-server:release ghcr.io/immich-app/immich-server:release
docker tag ghcr.nju.edu.cn/immich-app/immich-machine-learning:release ghcr.io/immich-app/immich-machine-learning:release

# 6. 启动
docker compose up -d
```

### 其他可用镜像站

| 镜像站 | 地址 | 测试方法 |
|--------|------|---------|
| 南大 | `ghcr.nju.edu.cn` | `curl -sI https://ghcr.nju.edu.cn/v2/` → 200 |
| 1ms | `ghcr.1ms.run` | `curl -sI https://ghcr.1ms.run/v2/` → 401（可用） |

**注意：** Docker daemon 本身不走 `HTTP_PROXY` 环境变量，CLI 设的代理
只影响 CLI 进程，不影响后台 daemon 拉取。要给 daemon 配代理需要改
`/etc/systemd/system/docker.service.d/proxy.conf`。

## Immich v3.x 数据库迁移（pgvecto-rs → VectorChord）

Immich v3.x 从旧的 `pgvecto-rs` 迁移到 `VectorChord`。旧版
`tensorchord/pgvecto-rs:pg14-v0.2.0` 的实例升级到 v3.x 后，启动报错：

```
Error: No vector extension found. Available extensions: vchord, vector
```

### 迁移步骤（参考 https://docs.immich.app/install/upgrading）

1. **备份数据库（必须）：**

   ```bash
   mkdir -p backup
   docker exec immich_postgres pg_dump --username=postgres --dbname=immich --format=plain > backup/immich_$(date +%Y%m%d).sql
   ```

2. **修改 compose.yaml database 部分：**

   ```diff
    database:
      container_name: immich_postgres
   -  image: docker.io/tensorchord/pgvecto-rs:pg14-v0.2.0@sha256:90724186...
   +  image: ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0
      environment:
        POSTGRES_PASSWORD: ${DB_PASSWORD}
        POSTGRES_USER: ${DB_USERNAME}
        POSTGRES_DB: ${DB_DATABASE_NAME}
        POSTGRES_INITDB_ARGS: '--data-checksums'
      volumes:
        - ${DB_DATA_LOCATION}:/var/lib/postgresql/data
   -  healthcheck: ...  # 删除整个 healthcheck 块
   -  command: ...      # 删除整个 command 块
   +  shm_size: 128mb
      restart: always
   ```

   关键：删除旧的 `healthcheck` 和 `command` 配置，新增 `shm_size: 128mb`。

3. **拉取新数据库镜像（用南大镜像加速）：**

   ```bash
   docker pull ghcr.nju.edu.cn/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0
   docker tag ghcr.nju.edu.cn/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0 ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0
   ```

4. **重启：**

   ```bash
   docker compose up -d
   ```

5. **观察迁移日志（不要中途重启！）：**

   ```bash
   docker logs immich_server -f
   ```

   会看到：
   - `Creating VectorChord extension`
   - `Reindexing face_index (This may take a while, do not restart)`
   - `Reindexing clip_index (This may take a while, do not restart)`

   索引重建可能需要几分钟到几十分钟（取决于数据量），完成后服务器正常启动。

### 回滚（如果迁移失败）

```bash
# 恢复旧 compose.yaml
# 删除新数据库数据，用备份恢复
docker exec -i immich_postgres psql --username=postgres --dbname=immich < backup/immich_YYYYMMDD.sql
```
