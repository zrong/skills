# agent_config.toml 共享模板

这是供仓库内新 skill 复用的配置发现模板，不是可单独安装的 skill，也不是运行时公共包。

本目录提供两类可复制资源：

- `scripts/agent_config.py`：配置发现与 TOML 加载实现
- `agent_config.example.toml`：新 skill 的配置示例骨架

## 查找顺序

未显式指定配置文件时，`scripts/agent_config.py` 按以下顺序查找，命中第一个文件后停止：

1. 当前工作目录的 `agent_config.toml`
2. skill 根目录的 `agent_config.toml`
3. 从当前工作目录向上找到的最近 Git 根目录中的 `agent_config.toml`
4. 全局 `~/.agents/agent_config.toml`

显式传入 `path` 时只读取该文件。相对路径基于 `cwd`（默认为当前工作目录）解析；文件不存在时直接报错，避免静默忽略拼写错误。

## 接入新 skill

将模板复制到新 skill 自己的 Python 包中，例如：

```text
my-skill/
└── scripts/
    ├── my_skill/
    │   ├── __init__.py
    │   ├── agent_config.py   # 从本目录 scripts/agent_config.py 复制
    │   └── cli.py
    └── pyproject.toml
```

不要在 skill 运行时代码中直接导入仓库的 `shared` 目录。skill 可能被单独复制或安装到其他电脑，自带实现才能保持可移植。

同时将 `agent_config.example.toml` 复制到 skill 根目录，将 `[example-skill]` 替换为实际 skill 名称，并删除不适用的示例字段。这个文件只描述该 skill 支持的 section；用户可以将 section 合并进包含其他 skill 配置的项目级或全局文件。

典型调用：

```python
from pathlib import Path

from .agent_config import load_section

SKILL_DIR = Path(__file__).resolve().parents[2]

config, config_path = load_section(
    "my-skill",
    SKILL_DIR,
    missing="raise",
)
```

配置可选时使用默认的 `missing="empty"`，没有找到文件会返回 `({}, None)`。配置是运行前提时使用 `missing="raise"`，并由 CLI 将 `ConfigNotFoundError` 转换为清晰的用户提示。

如果 CLI 提供 `--config`，将其值作为 `path` 传入。日志或 `doctor --json` 应报告实际命中的 `config_path`，但不得输出密钥值。

## SKILL.md 兜底说明

新 skill 的 `SKILL.md` 应包含类似说明：

```markdown
配置查找顺序为：当前工作目录、skill 目录、当前 Git 项目根目录、
`~/.agents/agent_config.toml`。也可以通过 `--config PATH` 显式指定；
显式路径不存在时应停止并提示用户修正。
```

配置示例文件应提示用户可将对应 TOML section 合并到项目级或全局配置中。示例只能包含占位值；含密钥的实际 `agent_config.toml` 不应提交到仓库。

## 验证

模板只使用 Python 3.11+ 标准库：

```bash
python3 -m unittest discover -s shared/agent-config/scripts/tests -v
```

复制模板后，应在目标 skill 内保留针对查找优先级、全局兜底、显式路径和缺失策略的测试。
