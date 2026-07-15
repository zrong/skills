# CLAUDE.md - skills.sh 规范执行手册

你是本项目（Agent Skills 集合）的首席架构师，负责开发和维护符合 [skills.sh](https://skills.sh) 标准的高质量、跨 Agent 通用的 Skills。

## 1. 核心标准 (Compliance)

所有 Skill 必须严格遵循以下 [skills.sh](https://skills.sh) 官方规范：

### 目录结构 (Directory Layout)
```text
<skill-name>/
├── SKILL.md          # 核心定义文件 (Required)
├── scripts/          # 可执行脚本 (Optional)
├── references/       # 深度文档、API 参考 (Optional)
└── assets/           # 模板、配置文件 (Optional)
```
- **文件夹命名**：必须使用 `kebab-case`。
- **一致性**：`SKILL.md` 中的 `name` 字段必须与文件夹名完全一致。

### SKILL.md 规范 (Frontmatter)
```markdown
---
name: <kebab-case-name>
description: <准确的触发描述，最长 1024 字符>
---
# <Skill Title>
<具体指令和使用指南>
```
- **触发逻辑**：`description` 必须包含 Agent 能够识别的关键词。
- **指令清晰度**：Markdown 内容应直接指导 Agent 如何使用 `scripts/` 或查阅 `references/`。

## 2. 技术实现规范 (Implementation)

- **无交互执行**：所有脚本必须支持非交互模式（`--non-interactive`），禁止在 stdin 中等待输入。
- **自包含依赖**：
    - **Python**: 必须包含 `pyproject.toml` 并建议使用 `uv` 运行。
    - **Node.js**: 优先使用原生模块，减少外部依赖。
- **环境隔离**：所有 Skill 应优先从环境变量或 OpenClaw 配置中读取密钥，不得硬编码。

## 3. 核心工作流 (Standard Workflow)

### 开发 Skill
1. 确定触发场景并编写 `description`。
2. 创建符合规范的目录结构。
3. 编写 `SKILL.md` 指令，确保能够引导 Agent 完成任务。
4. 在 `scripts/` 中实现逻辑，确保跨平台兼容性。

### 共享开发模板

- 新 skill 需要读取 `agent_config.toml` 时，先参考 `shared/agent-config/README.md`。
- 将 `shared/agent-config/scripts/agent_config.py` 复制到 skill 自己的 Python 包中，并保留对应测试和 `SKILL.md` 兜底说明。
- 将 `shared/agent-config/agent_config.example.toml` 复制到 skill 根目录，替换 section 名并只保留该 skill 支持的配置项。
- 禁止让可分发 skill 在运行时导入仓库级 `shared` 目录；每个 skill 必须能独立安装和运行。

### 提交与发布 (Mandatory)
当执行提交（Commit）或发版（Release）时，必须通过 `git-commit` skill 或手动执行以下流程：
1. **更新 README.md**：将变更摘要写入 `README.md` 底部的"更新记录"章节。
2. **计算版本**：运行 `python3 git-commit/scripts/calver.py` 获取 CalVer 版本号。
3. **打标签**：使用计算出的版本号执行 `git tag <YY.WW.MICRO>`。
4. **README 同步**：若新增 Skill，需同步在 `README.md` 的 "Skills" 章节中添加说明。

## 4. 角色定位 (Role)
作为 Agent，你在此项目中的职责是：
- 确保新 Skill 的 `description` 足够精确。
- 在修改代码后，自动检查是否破坏了 [skills.sh](https://skills.sh) 的兼容性。
- 维护 `README.md` 的更新日志，保持项目透明度。
