#!/usr/bin/env python3
"""UI Extractor — extract UI components from static images.

Workflow:
  1. Load the input image.
  2. Detect the background type (chroma or checkerboard) when --bg-type=auto.
  3. Build a foreground mask and a transparent BGRA copy.
  4. Optionally detect the checkerboard corners and apply a perspective warp.
  5. Optionally separate the foreground into individual UI elements.

Output:
  <output-dir>/<name>-nobg.png        BGRA foreground image
  <output-dir>/<name>-warped.png      Perspective-corrected image (--warp)
  <output-dir>/elements/element_NN.png BGRA crop per UI element
  <output-dir>/metadata.json          Element metadata
  <output-dir>/debug/                 Visualisations when --debug is set
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np

from utils import (
    Element,
    dump_metadata,
    draw_annotations,
    ensure_dir,
    save_mask_overlay,
    write_image,
    write_text,
)
from detect_checkerboard import detect_checkerboard
from remove_bg import remove_bg
from extract_elements import extract_ui_elements
from warp import warp_perspective

logger = logging.getLogger("ui_extractor")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract UI elements from a static image (chroma or checkerboard background)",
    )
    parser.add_argument("-i", "--input", required=True, help="Input image path")
    parser.add_argument(
        "-o",
        "--output-dir",
        help="Output directory (default: <input>-ui-extracted/)",
    )
    parser.add_argument(
        "--bg-type",
        choices=("auto", "chroma", "checkerboard"),
        default="auto",
        help="Background type (default: auto)",
    )
    parser.add_argument(
        "--bg-color",
        choices=("auto", "green", "blue", "white", "black"),
        default="auto",
        help="Chroma background colour (default: auto)",
    )
    parser.add_argument(
        "--pattern-size",
        type=int,
        nargs=2,
        default=(9, 6),
        metavar=("COLS", "ROWS"),
        help="Checkerboard inner-corner grid (default: 9 6)",
    )
    parser.add_argument(
        "--min-element-area",
        type=int,
        default=500,
        help="Discard components smaller than this (default: 500 px²)",
    )
    parser.add_argument(
        "--no-extract",
        action="store_true",
        help="Skip element separation, only output the background-removed image",
    )
    parser.add_argument(
        "--warp",
        action="store_true",
        help="Apply perspective correction using detected checkerboard corners",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write mask/annotation visualisations into <output-dir>/debug",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Force non-interactive mode (no prompts)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    src_path = Path(args.input)
    if not src_path.exists():
        logger.error("Input not found: %s", src_path)
        return 2

    output_dir = (
        Path(args.output_dir) if args.output_dir else src_path.with_name(src_path.stem + "-ui")
    )
    ensure_dir(output_dir)

    image_bgr = cv2.imread(str(src_path), cv2.IMREAD_COLOR)
    if image_bgr is None:
        logger.error("Failed to decode image: %s", src_path)
        return 2

    logger.info("Loaded %s (%dx%d)", src_path, image_bgr.shape[1], image_bgr.shape[0])

    # ── Stage 1+2: background removal ────────────────────────────────
    bgra, mask, used_type = remove_bg(
        image_bgr,
        bg_type=args.bg_type,
        bg_color=args.bg_color,
        pattern_size=tuple(args.pattern_size),
    )
    logger.info("Background removed (type=%s)", used_type)

    nobg_path = output_dir / f"{src_path.stem}-nobg.png"
    write_image(nobg_path, bgra)

    if args.debug:
        dbg = ensure_dir(output_dir / "debug")
        save_mask_overlay(image_bgr, mask, dbg / "mask_overlay.png")
        cv2.imwrite(str(dbg / "foreground_mask.png"), mask)

    # ── Stage 4 (optional): perspective correction ───────────────────
    warped_path: Path | None = None
    if args.warp:
        corners, confidence = detect_checkerboard(image_bgr, tuple(args.pattern_size))
        if corners is None:
            logger.warning("--warp requested but checkerboard corners not detected")
        else:
            warped = warp_perspective(image_bgr, corners, tuple(args.pattern_size))
            warped_path = output_dir / f"{src_path.stem}-warped.png"
            write_image(warped_path, warped)
            if args.debug:
                vis = image_bgr.copy()
                cols, rows = args.pattern_size
                grid = corners.reshape(rows, cols, 2).astype(int)
                for r in range(rows):
                    for c in range(cols):
                        cv2.circle(vis, tuple(grid[r, c]), 4, (0, 255, 0), -1)
                cv2.imwrite(str(output_dir / "debug" / "corners.png"), vis)

    # ── Stage 3 (optional): element separation ──────────────────────
    elements: List[Element] = []
    element_paths: list[str] = []
    if not args.no_extract:
        elements = extract_ui_elements(
            mask,
            bgra,
            min_area=args.min_element_area,
        )
        element_paths = _save_elements(elements, output_dir, src_path.stem)
        if args.debug and elements:
            annotated = draw_annotations(image_bgr, elements)
            cv2.imwrite(str(output_dir / "debug" / "elements_annotated.png"), annotated)

    # ── Metadata ─────────────────────────────────────────────────────
    dump_metadata(
        output_dir / "metadata.json",
        source=str(src_path),
        bg_type=used_type,
        bg_options={
            "bg_color": args.bg_color,
            "pattern_size": list(args.pattern_size),
            "min_element_area": args.min_element_area,
        },
        elements=elements,
        image_paths=element_paths,
    )

    # ── Summary ──────────────────────────────────────────────────────
    summary = _format_summary(
        src_path=src_path,
        output_dir=output_dir,
        used_type=used_type,
        nobg_path=nobg_path,
        warped_path=warped_path,
        elements=elements,
    )
    print(summary)
    write_text(output_dir / "summary.txt", summary)
    return 0


def _save_elements(
    elements: List[Element], output_dir: Path, stem: str
) -> list[str]:
    if not elements:
        return []
    el_dir = ensure_dir(output_dir / "elements")
    paths: list[str] = []
    for elem in elements:
        p = el_dir / f"element_{elem.index:02d}.png"
        write_image(p, elem.image)
        paths.append(str(p.relative_to(output_dir)))
    return paths


def _format_summary(
    *,
    src_path: Path,
    output_dir: Path,
    used_type: str,
    nobg_path: Path,
    warped_path: Path | None,
    elements: List[Element],
) -> str:
    lines = [
        "UI Extractor — done",
        f"  source:        {src_path}",
        f"  output-dir:    {output_dir}",
        f"  bg-type used:  {used_type}",
        f"  no-bg image:   {nobg_path.relative_to(output_dir.parent)}",
    ]
    if warped_path is not None:
        lines.append(
            f"  warped image:  {warped_path.relative_to(output_dir.parent)}"
        )
    if elements:
        lines.append(f"  elements:      {len(elements)} (in elements/element_NN.png)")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
