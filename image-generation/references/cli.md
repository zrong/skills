# CLI 参考

所有命令：

```bash
uv run --project {SCRIPTS_DIR} imggen COMMAND ...
```

## 静态与远端检查

```bash
imggen list [--config PATH]
imggen models [-p PROVIDER] [-e ENDPOINT] [--show-unconfigured] [--config PATH]
```

`list` 不解析或显示 key。`models` 需要当前 endpoint 的 key，调用远端模型列表后与本地 allowlist 对照。

## generate/edit 共用参数

- prompt：positional、`--prompt`、`--prompt-file` 三选一
- endpoint：`-p/--provider`, `-e/--endpoint`, `-m/--model`, `--config`
- 输出：`-o/--out/--output`, `--out-dir`, `--force`
- 数量与模型选项：`-n`, `--size`, `--quality`, `--background`, `--output-format`, `--output-compression`, `--moderation`, `--input-fidelity`
- Gemini：`--aspect-ratio`, `--image-size`
- Seedream：`--seed`, `--stream/--no-stream`, `--watermark/--no-watermark`, `--sequential auto|disabled`
- 执行：`--dry-run`, `--max-attempts 1..10`
- prompt augmentation：`--augment/--no-augment` 以及 prompting.md 中的字段
- 后处理：`--downscale-max-dim`, `--downscale-suffix`

`edit` 另有可重复 `--image` 和可选 `--mask`。

所有显式模型选项都需要当前 model policy 的同名 capability。没有映射的参数不会发送。

## 输出命名

- 单图默认 `generated.<format>`；
- `-n > 1` 时使用 `_1`, `_2` 后缀；
- `--out-dir` 使用 `generated_1...`；
- batch 默认使用 `job_1...`；
- interactive 默认使用 `<session-stem>-turn-N.png`；
- 已存在文件必须显式 `--force`。

## generate-batch

```bash
imggen generate-batch --input jobs.jsonl --out-dir outputs \
  --concurrency 3 --max-attempts 3 [--fail-fast] ENDPOINT_OPTIONS...
```

每行是一个 JSON 对象且必须有 `prompt`：

```json
{"prompt":"第一张图","size":"1024x1024","out":"outputs/one.png"}
{"prompt":"第二张图","model":"another-allowed-model","fields":{"style":"ink drawing"}}
```

行内非空字段覆盖命令行默认值。模型仍须位于同一 endpoint allowlist；batch 不允许靠 job 切换到未配置 endpoint。默认收集全部失败后统一报错；`--fail-fast` 尽快取消尚未开始的任务。

## 重试

网络超时、连接错误、HTTP `408/409/429/500/502/503/504` 使用指数退避，最多 `--max-attempts`。其他 4xx 不重试。interactive 会把最终失败写入 session，供 `interactive retry` 恢复。

## chroma-key

```bash
imggen chroma-key --input keyed.png --out alpha.png \
  [--key-color '#00ff00'] [--tolerance 12] \
  [--auto-key none|corners|border] [--soft-matte] \
  [--transparent-threshold 12] [--opaque-threshold 96] \
  [--edge-contract 0..16] [--edge-feather 0..64] \
  [--spill-cleanup|--despill] [--force]
```

输出必须是 PNG 或 WebP，以保留 alpha。

## remove-background

```bash
imggen remove-background --input source.png --out transparent.png \
  [--config agent_config.toml] [--method METHOD --model MODEL] \
  [--parameters-json JSON] [--reprocess] [--no-matting] [--no-fallback] \
  [--fallback-key-color '#00ff00'] [--fallback-auto-key border] \
  [--fallback-tolerance 12] [--fallback-transparent-threshold 12] \
  [--fallback-opaque-threshold 96] [--fallback-edge-feather 0] \
  [--fallback-edge-contract 0] [--dry-run] [--force]
```

默认先运行独立 matting skill 的 `status`，探测通过后运行 `remove`。探测失败才回退 `chroma-key`；探测通过后的执行失败不会回退。`--config` 指向同时包含 `[matting]` 的共享 `agent_config.toml`。`--no-matting` 可强制使用旧方法，`--no-fallback` 可要求 matting 不可用时立即失败。

为保证 matting 与回退分支交付一致，`remove-background` 输出固定为透明 PNG。
