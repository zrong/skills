---
name: dns-manager
description: |
  DNS 解析记录管理工具。用于查询、新增、修改、删除域名解析记录（A / CNAME / TXT / MX / AAAA 等），
  当前支持腾讯云 DNSPod，操作幂等（重复执行不产生重复记录）。当用户提到 DNS、域名解析、解析记录、
  添加/修改/删除 A 记录或 CNAME、DNSPod、腾讯云 DNS、把域名指向某个 IP、查某个域名的解析时使用。
---

# DNS Manager Skill

## 执行入口

```bash
uv run --project {SKILL_DIR}/scripts dns-manager --non-interactive <command> --json
```

配置查找顺序为：当前工作目录、skill 目录、当前 Git 项目根目录、
`~/.agents/agent_config.toml`。也可以通过 `--config PATH` 显式指定；
显式路径不存在时应停止并提示用户修正。完整配置说明见
[references/configuration.md](references/configuration.md)，模板见
[agent_config.example.toml](agent_config.example.toml)。

## 命令

| 命令 | 作用 |
|---|---|
| `doctor` | 校验配置（不联网），报告命中的配置文件与凭据是否就绪 |
| `list <domain> [--subdomain X] [--type A]` | 查询解析记录 |
| `upsert <domain> --subdomain X --type A --value IP [--ttl 600] [--line 默认]` | 幂等新增或更新记录 |
| `delete <domain> --subdomain X --type A [--value IP]` | 幂等删除匹配记录 |
| （所有写命令均支持 `--dry-run`） | 只展示将要执行的操作，不落盘 |

## 工作流

1. 首次使用先检查配置：

   ```bash
   dns-manager doctor --json
   ```

2. 操作前先查询现状，确认没有冲突记录：

   ```bash
   dns-manager list example.com --subdomain www --json
   ```

3. 写操作先用 dry-run 确认，再实际执行：

   ```bash
   dns-manager upsert example.com --subdomain www --type A --value 1.2.3.4 --dry-run --json
   dns-manager upsert example.com --subdomain www --type A --value 1.2.3.4 --json
   dns-manager delete example.com --subdomain old --type A --dry-run --json
   ```

4. 执行后重新 `list` 验证结果，向用户报告 `action` 字段：
   - `upsert` → `created` / `updated` / `unchanged`（unchanged 表示记录已存在且完全一致，未做任何写操作）
   - `delete` → `deleted` / `skipped`（skipped 表示记录本就不存在）

## 规则

- **删除是不可逆操作**：执行 `delete` 前必须先 `--dry-run` 并把将删除的记录清单告知用户确认；
  只有用户明确同意后才真正执行。
- 幂等性依赖 upsert/delete 的现有记录检查，不要绕过它们直接拼 API 调用。
- 凭据（secret_id/secret_key）只能来自配置或环境变量，任何输出（doctor、日志、错误信息）
  不得包含凭据值。
- DNSPod 的线路参数（RecordLine）默认值为中文 `默认`；这是 API 要求，不要改成英文 "Default"。
- 改动 TTL 或记录值后，解析生效受 TTL 与运营商缓存影响，提醒用户最长需等待 TTL 时间。

## 扩展新 DNS 服务商

目前支持 `tencentcloud`（DNSPod）。新增服务商（如 aliyun、cloudflare）的步骤见
[references/configuration.md](references/configuration.md) 的"新增 Provider"章节：
实现 `DnsProvider` 子类、注册到 registry、补充配置示例，无需改动 CLI。
