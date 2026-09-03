# 技术实现索引

## 模块

```text
scripts/imggen/
├── cli.py                # argparse 统一 CLI、batch、interactive
├── config.py             # 配置发现、endpoint 凭据、精确模型 allowlist
├── models.py             # EndpointConfig / ModelPolicy / ImageRequest
├── service.py            # capability 校验与瞬时错误重试
├── prompting.py          # prompt-file 与结构化 prompt
├── output.py             # 防覆盖、命名、格式、downscale
├── interactive.py        # Seedream 坐标标注与原子会话 manifest
├── chroma.py             # 纯色背景转 alpha
├── matting_bridge.py     # 可选调用独立 matting skill；不可用时回退 chroma-key
├── provider.py           # 兼容 Python 入口，不再做协议猜测
└── adapters/
    ├── base.py
    ├── openai.py
    ├── gemini.py
    └── seedream.py
```

## 关键边界

- `config.get_endpoint_config()` 在任何网络动作前解析 endpoint，并要求非空 `models` 子表。
- `EndpointConfig.resolve_model()` 只做 exact match，并校验 `generate/edit` operation。
- `service.validate_request()` 将每个显式参数映射到 capability，同时检查引用数、输出数和枚举 allowlist。
- `adapters.create_adapter()` 只读取 endpoint 的 `adapter`；任何模型名称都不会改变协议。
- `interactive.SessionStore` 先原子记录 pending turn，再记录 completed/failed，重启后可从最后成功输出继续或重试失败轮次。

## 参考

- [配置与安全边界](references/configuration.md)
- [能力矩阵](references/capability-matrix.md)
- [完整 CLI](references/cli.md)
- [Prompt 结构](references/prompting.md)
- [Seedream 交互编辑](references/seedream-interactive.md)
- [系统 imagegen 迁移审计](references/migration-audit.md)
