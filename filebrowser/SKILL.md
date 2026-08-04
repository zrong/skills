---
name: filebrowser
description: 从 FileBrowser Quantum 下载远程文件并上传到 S3 兼容 bucket。支持多个 FileBrowser source、多个 S3 target、AWS S3/腾讯云 COS/阿里云 OSS/火山 TOS 等兼容端点、对象 key 映射、覆盖保护和 dry-run。当用户提到 FileBrowser 文件转存 S3、对象存储备份、bucket 上传或跨存储传输时使用。
---

# FileBrowser Transfer

将一个 FileBrowser 文件暂存到本地临时目录，再上传到已配置的 target。当前 target adapter 为 `s3`；新增目标类型时扩展 adapter，不要把目标逻辑写入 FileBrowser 客户端。

## 执行入口

```bash
uv run --project {SKILL_DIR}/scripts fb-transfer --non-interactive <command>
```

配置查找顺序为：当前工作目录、skill 目录、当前 Git 项目根目录、`~/.agents/agent_config.toml`。也可以用全局 `--config PATH` 显式指定；显式路径不存在时直接停止。

完整配置结构和多 S3 示例见 [references/configuration.md](references/configuration.md)。不要输出 FileBrowser token、S3 secret 或包含它们的异常详情。

## 工作流

1. 首次操作或配置变化后运行：

   ```bash
   uv run --project {SKILL_DIR}/scripts fb-transfer --config /path/agent_config.toml doctor --json
   ```

2. 上传前先 dry-run，确认 source、target 和 object key：

   ```bash
   uv run --project {SKILL_DIR}/scripts fb-transfer --config /path/agent_config.toml \
     --non-interactive upload "/项目/成片/demo.mp4" \
     --source production --target archive --dry-run --json
   ```

3. 用户确认目标后执行上传：

   ```bash
   uv run --project {SKILL_DIR}/scripts fb-transfer --config /path/agent_config.toml \
     --non-interactive upload "/项目/成片/demo.mp4" \
     --source production --target archive --json
   ```

4. 默认保留 FileBrowser 完整相对路径作为 S3 key，并在前面添加 target 的 `prefix`。需要改 key 时使用 `--key`。

## 安全规则

- 一次只上传一个文件；目录会被拒绝。
- S3 已存在同 key 对象时默认拒绝。只有用户明确要求覆盖时才添加 `--overwrite`。
- FileBrowser 路径必须是绝对路径且不能包含 `..`；S3 key 必须是相对路径且不能包含 `..`。
- 下载采用流式写入临时文件，不把大文件完整载入内存；无论成功或失败都会清理临时目录。
- `max_transfer_bytes = 0` 表示不设 skill 级大小上限；生产配置建议设置明确上限。
- dry-run 只验证配置和路径，不连接 FileBrowser 或 S3，也不解析凭据链。

## 结果

成功时报告 source、FileBrowser 路径、target、bucket、object key、字节数和可用的公开 URL。不要声称已上传，除非命令返回成功且 S3 `head_object` 的大小与本地暂存文件一致。
