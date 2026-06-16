#!/usr/bin/env python3
"""--repack-dir 重打包：基于已抽取的成品帧重新组合，不重跑抠图/裁切。

支持删帧/选帧后重拼 spritesheet + 重写 metadata + player。正式化"删最后两帧再重拼"
这类后处理操作。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import cv2


def parse_frame_spec(spec: str, total: int) -> set[int]:
    """解析帧规格，返回 1-based 帧号集合。支持 '15,16' 和 '1-14' 混用。"""
    result: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            for i in range(int(lo), int(hi) + 1):
                if 1 <= i <= total:
                    result.add(i)
        else:
            i = int(part)
            if 1 <= i <= total:
                result.add(i)
    return result


def _main_utils():
    """获取主文件的 create_spritesheet / generate_player。

    主入口作为 __main__ 运行时直接从 __main__ 拿，避免反向 import 触发双重加载；
    否则 fallback 到 spritesheet 模块。
    """
    main_mod = sys.modules.get("__main__")
    if main_mod and hasattr(main_mod, "create_spritesheet"):
        return main_mod.create_spritesheet, main_mod.generate_player
    from spritesheet import create_spritesheet, generate_player  # noqa: E402

    return create_spritesheet, generate_player


def run_repack(
    repack_dir: str | Path,
    *,
    drop: str | None = None,
    keep: str | None = None,
    cols: int | None = None,
    video: str | None = None,
) -> None:
    """重打包一个已有 spritesheet 输出目录。"""
    repack_dir = Path(repack_dir).expanduser().resolve()
    meta_path = repack_dir / "metadata.json"
    frames_dir = repack_dir / "frames"

    if not meta_path.exists() or not frames_dir.exists():
        print(f"错误: {repack_dir} 不是有效的 spritesheet 输出目录（缺少 metadata.json 或 frames/）", file=sys.stderr)
        sys.exit(1)

    if drop and keep:
        print("错误: --drop-frames 与 --keep-frames 互斥", file=sys.stderr)
        sys.exit(1)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    frame_paths = sorted(
        frames_dir.glob("frame_*.png"),
        key=lambda p: int(re.search(r"(\d+)", p.stem).group(1)),
    )
    total = len(frame_paths)
    if total == 0:
        print(f"错误: {frames_dir} 下无 frame_*.png", file=sys.stderr)
        sys.exit(1)

    # 决定保留哪些帧（1-based）
    drop_set: set[int] = set()
    if drop:
        drop_set = parse_frame_spec(drop, total)
        keep_ids = [i for i in range(1, total + 1) if i not in drop_set]
    elif keep:
        keep_ids = sorted(parse_frame_spec(keep, total))
    else:
        keep_ids = list(range(1, total + 1))  # 原地重拼（用于改 cols）

    if not keep_ids:
        print("错误: 保留帧为空", file=sys.stderr)
        sys.exit(1)

    print(f"重打包: {repack_dir.name}")
    print(f"  原始 {total} 帧 → 保留 {len(keep_ids)} 帧" + (f"（删除 {sorted(drop_set)}）" if drop_set else ""))

    # 读保留的帧
    kept: list = []

    for i in keep_ids:
        img = cv2.imread(str(frame_paths[i - 1]), cv2.IMREAD_UNCHANGED)
        if img is None:
            print(f"  警告: 无法读取 frame_{i:02d}.png，跳过")
            continue
        kept.append(img)

    if not kept:
        print("错误: 无可读帧", file=sys.stderr)
        sys.exit(1)

    cols = cols or int(meta.get("cols", 4))
    rows = (len(kept) + cols - 1) // cols
    fh, fw = kept[0].shape[:2]

    # 重写 frames/（连续编号）
    for p in frame_paths:
        p.unlink()
    for i, img in enumerate(kept):
        cv2.imwrite(str(frames_dir / f"frame_{i + 1:02d}.png"), img)

    # 重写 spritesheet + player（复用主文件函数）
    create_spritesheet, generate_player = _main_utils()
    sheet = create_spritesheet(kept, cols=cols)
    cv2.imwrite(str(repack_dir / "spritesheet.png"), sheet)

    # 更新 metadata（loop 语义已变：删了帧，原始索引不再对应当前帧序）
    old_loop = meta.get("loop", {})
    meta["frames"] = len(kept)
    meta["cols"] = cols
    meta["rows"] = rows
    meta["frame_w"] = int(fw)
    meta["frame_h"] = int(fh)
    meta["loop"] = {
        "start": old_loop.get("start"),
        "end": old_loop.get("end"),
        "method": "repacked",
        "note": "帧已重打包，loop.start/end 为原始视频帧索引，不再对应当前帧序",
    }
    meta["repacked"] = {
        "dropped": sorted(drop_set),
        "kept": len(kept),
        "original_frames": total,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    video_src = Path(video).expanduser() if video else Path(meta.get("video", ""))
    generate_player(repack_dir, video_src, cols, rows, len(kept), int(fw), int(fh))

    print(f"  → frames/frame_01.png ~ frame_{len(kept):02d}.png")
    print(f"  → spritesheet.png ({sheet.shape[1]} × {sheet.shape[0]})")
    print(f"  → metadata.json (method=repacked)")
    print(f"  → player.html (TOTAL={len(kept)})")
