---
name: email
description: |
  邮件处理工具（基于 IMAP）。搜索、读取、下载附件、移动邮件。

  触发场景：
  - 用户需要查看、搜索、管理邮件
  - 用户需要下载邮件附件
  - 用户需要移动、归档邮件
  - 用户提到"邮件"、"邮箱"、"收件箱"、"附件"
---

# 邮件处理 Skill

基于 IMAP 协议的邮件 CLI 工具。Python 3.13+ 必需。

## 首次使用

运行 `init` 命令交互式创建配置：

```bash
python3 scripts/email_tool.py init
```

会依次询问：
1. 配置文件保存路径（默认 `agent_config.toml`，保存在当前项目目录）
2. 账户名（默认 `qqmail`）
3. 邮箱地址、IMAP/SMTP 服务器信息
4. 密码/授权码

结果：
- 配置（含密码）写入 `agent_config.toml`（与其他 skill 共用同一配置文件）
- 密码同时写入 `.env`（优先级更高）
- 自动将 `.env` 加入 `.gitignore`
- 自动测试 IMAP 连接

## 配置说明

配置文件为 `agent_config.toml`（多 skill 共用，按 `[skill名]` 分区）。
查找策略：当前目录 → skill 目录 → git 根目录。
模板位于 skill 目录的 `agent_config.example.toml`。

**agent_config.toml**（`[email]` 区块）：
```toml
[email.accounts.qqmail]
email = "user@example.com"
imap_host = "imap.qq.com"
imap_port = 993
smtp_host = "smtp.qq.com"
smtp_port = 465
password = "your_app_password"
```

**密码优先级**（从高到低）：
1. `.env` 文件中的 `EMAIL_{ACCOUNT}_PASSWORD` 环境变量
2. `agent_config.toml` 中账户的 `password` 字段

环境变量命名规则：`EMAIL_{ACCOUNT}_PASSWORD`，ACCOUNT 为账户名大写。

## 脚本路径

相对于 skill 安装目录：`scripts/email_tool.py`

所有命令在**项目目录**下执行（配置文件通过三位置发现策略自动查找）。

## 命令速查

| 命令 | 用途 | 示例 |
|------|------|------|
| `init` | 交互式初始化配置 | `email_tool.py init` |
| `folders` | 列出所有文件夹 | `email_tool.py folders` |
| `list` | 搜索列出邮件 | `email_tool.py list --subject "发票" --last 5` |
| `read` | 读取邮件内容 | `email_tool.py read --uid 12345` |
| `download` | 下载附件 | `email_tool.py download --subject "发票" --ext pdf --output-dir /tmp` |
| `move` | 移动邮件 | `email_tool.py move --subject "账单" --target-folder "账单" --yes` |
| `search-links` | 从 HTML 提取链接 | `email_tool.py search-links --subject "京东"` |

## 通用参数

- `--config PATH`：配置文件路径（默认使用 agent_config.toml 三位置发现策略）
- `--account NAME`：账户名（默认 qqmail）
- `--subject TEXT`：主题关键词搜索
- `--sender TEXT`：发件人搜索
- `--since DD-Mon-YYYY`：起始日期（如 `01-Jan-2026`）
- `--before DD-Mon-YYYY`：截止日期
- `--last N`：只处理最新 N 封

## 命令详情

### init - 初始化配置

```bash
email_tool.py init
# 或指定配置路径
email_tool.py --config /path/to/agent_config.toml init
```

交互式询问账户信息，自动：
- 创建或追加到 `agent_config.toml`（`[email.accounts.{name}]` 区块）
- 将密码同时写入 `.env`（不覆盖已有内容）
- 将 `.env` 加入 `.gitignore`
- 测试 IMAP 连接

### list - 列出邮件

```bash
email_tool.py list --subject "发票"
email_tool.py list --sender "jd.com" --last 3
email_tool.py list --subject "报告" --folder "工作"
```

输出 JSON 包含 `uid`、`subject`、`from`、`date`。

### read - 读取邮件内容

```bash
email_tool.py read --uid 12345 12346
email_tool.py read --subject "发票" --last 1
email_tool.py read --uid 12345 --html
```

输出 JSON 包含 `body`、`content_type`、`attachments`（附件列表）。

### download - 下载附件

```bash
email_tool.py download --uid 12345 --output-dir /tmp/attachments
email_tool.py download --subject "发票" --ext pdf --output-dir /tmp/invoices
email_tool.py download --subject "报销" --last 1 --output-dir .
```

### move - 移动邮件

```bash
# dry run（默认，只列出不移动）
email_tool.py move --subject "账单" --target-folder "账单"
# 执行移动
email_tool.py move --subject "账单" --target-folder "账单" --yes
# 只移动最新一封
email_tool.py move --subject "通知" --target-folder "归档" --last 1 --yes
```

文件夹名支持中文，自动处理 IMAP modified UTF-7 编码。优先使用 MOVE 命令，不支持时降级 COPY+DELETE。

### search-links - 提取链接

```bash
email_tool.py search-links --subject "京东"
email_tool.py search-links --sender "didifapiao"
```

输出 JSON 包含 `pdf_links`、`xml_links`、`invoice_numbers`。

## 注意事项

1. **Python 版本**：必须 3.13+，无第三方依赖
2. **QQ 邮箱**需要使用**授权码**（非登录密码），在 QQ 邮箱设置 → 账户 → IMAP 服务中生成
3. **密码安全**：密码可存放在 `agent_config.toml` 的 `password` 字段中，或通过环境变量 `EMAIL_{ACCOUNT}_PASSWORD` 读取（环境变量优先）
4. IMAP 搜索支持中文（自动使用 UTF-8 charset）
5. **QQ Mail BEFORE bug**：QQ 邮箱 IMAP 在 CHARSET UTF-8 模式下 `BEFORE` 关键词返回空结果。工具已自动处理：服务端只发 `SINCE`，`BEFORE` 在本地按 Date 头过滤
6. `move` 命令默认 dry run，必须加 `--yes` 才执行
7. `download` 的 `--ext` 参数支持逗号分隔多个扩展名（如 `pdf,xml`）
8. 日期格式为 IMAP 标准格式：`DD-Mon-YYYY`（如 `15-Mar-2026`）
