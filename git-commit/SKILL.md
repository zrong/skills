---
name: git-commit
description: 当用户明确要求提交、打标签、发版或推送 git 变更时使用；适用于使用 CalVer 管理版本号的仓库。
---

# Git Commit with CalVer Tag

将当前仓库变更提交到 git，并按 CalVer 规则打 tag。

## 核心规则

- **先提交，再算版本，再打 tag。**
- **版本号必须来自 `calver.py`，不得手动推断。**
- **`calver.py` 是本 skill 自带脚本，不属于项目仓库。**
- **如果无法可靠定位 skill 的安装目录，就停止。**不要猜路径，也不要继续后续步骤。
- **只有用户明确要求时，才执行 `git push` 和 `git push --tags`。**

## 脚本定位

`calver.py` 指的是 skill 自带脚本，而不是项目仓库里的同名文件。

执行时先定位 skill 的安装目录，再运行：

```bash
python3 <skill安装目录>/scripts/calver.py
```

如果环境不能提供安装目录，或目录无法可靠确定，立即停止并告知用户。

## 工作流程

1. 查看仓库状态，确认当前变更范围。
2. 只暂存并提交与当前任务相关的文件。
3. 如用户提供额外的 commit message，则优先使用它。
4. 运行 `calver.py` 获取下一个版本号。
5. 用该版本号创建 tag。
6. 如用户明确要求推送，再执行 `git push` 和 `git push --tags`。

## CalVer 规则

- 格式：`YY.WW.MICRO`
- `YY`：ISO 年份后两位
- `WW`：ISO 周数
- `MICRO`：全局递增序号，跨年不重置

## 常见错误

| 错误做法 | 正确做法 |
|---|---|
| 在项目仓库里找 `scripts/calver.py` | 在 skill 安装目录里执行脚本 |
| 找不到路径就手动算版本号 | 直接停止 |
| 用户没明确要求就 push | 先只提交和打 tag |
| 把无关文件一起提交 | 只提交当前任务相关文件 |
