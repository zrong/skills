#!/usr/bin/env python3
"""Unified downloader helper for Douyin and yt-dlp backends."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    print("ERROR: Python 3.11+ is required.", file=sys.stderr)
    raise SystemExit(1)


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = SKILL_DIR.parent
PROJECT_CONFIG = PROJECT_ROOT / "agent_config.toml"
GLOBAL_CONFIG = Path.home() / ".agents" / "agent_config.toml"
CONFIG_SECTION = "video-downloader"
DOUYIN_REMOTE = "https://github.com/jiji262/douyin-downloader.git"


def die(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def info(message: str) -> None:
    print(f"INFO: {message}", file=sys.stderr)


def expand_path(value: str | None) -> Path | None:
    if not value:
        return None
    return Path(value).expanduser()


def runtime_dir_from_settings(settings: dict) -> Path:
    configured = expand_path(settings.get("runtime_dir"))
    if configured:
        return configured
    return SKILL_DIR / ".runtime"


def resolve_config_path(config_arg: str | None = None) -> Path:
    if config_arg:
        return Path(config_arg).expanduser()
    if PROJECT_CONFIG.exists():
        return PROJECT_CONFIG
    return GLOBAL_CONFIG


def load_settings(config_path: Path) -> tuple[dict, bool]:
    if not config_path.exists():
        return {}, False
    with open(config_path, "rb") as fh:
        data = tomllib.load(fh)
    section = data.get(CONFIG_SECTION, {})
    if not isinstance(section, dict):
        die(f"[{CONFIG_SECTION}] must be a TOML table in {config_path}")
    return section, True


def is_douyin_url(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    if not host and "douyin.com" in url.lower():
        return True
    douyin_hosts = (
        "douyin.com",
        "www.douyin.com",
        "v.douyin.com",
        "iesdouyin.com",
        "www.iesdouyin.com",
        "live.douyin.com",
    )
    return any(host == item or host.endswith(f".{item}") for item in douyin_hosts)


def resolve_backend(url: str, requested: str, settings: dict) -> str:
    if requested != "auto":
        return requested
    configured = settings.get("default_backend", "auto")
    if configured in {"douyin", "yt-dlp"}:
        return configured
    return "douyin" if is_douyin_url(url) else "yt-dlp"


def runtime_yt_dlp_bin(runtime_dir: Path) -> Path:
    if os.name == "nt":
        return runtime_dir / "yt-dlp" / ".venv" / "Scripts" / "yt-dlp.exe"
    return runtime_dir / "yt-dlp" / ".venv" / "bin" / "yt-dlp"


def yt_dlp_bin_from_venv(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "yt-dlp.exe"
    return venv_dir / "bin" / "yt-dlp"


def runtime_python_bin(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def runtime_douyin_home(runtime_dir: Path) -> Path:
    return runtime_dir / "douyin-downloader"


def detect_yt_dlp(settings: dict, runtime_dir: Path) -> Path | None:
    configured = expand_path(settings.get("yt_dlp_path"))
    candidates: list[Path] = []
    if configured:
        candidates.append(configured)
    runtime_bin = runtime_yt_dlp_bin(runtime_dir)
    candidates.append(runtime_bin)
    path_bin = shutil.which("yt-dlp")
    if path_bin:
        candidates.append(Path(path_bin))
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def detect_douyin_home(settings: dict, runtime_dir: Path) -> Path | None:
    configured = expand_path(settings.get("douyin_downloader_home"))
    candidates = [configured, runtime_douyin_home(runtime_dir)]
    for candidate in candidates:
        if not candidate:
            continue
        if (candidate / "run.py").exists() and (candidate / "pyproject.toml").exists():
            return candidate
    return None


def resolve_douyin_config_path(settings: dict, douyin_home: Path) -> Path:
    configured = expand_path(settings.get("douyin_config_path"))
    if configured:
        return configured
    return douyin_home / "config.yml"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_default_douyin_config(config_path: Path, output_dir: Path) -> None:
    content = f"""path: {output_dir.expanduser()}/

auto_cookie: true

music: true
cover: true
avatar: true
json: true

mode:
  - post

number:
  post: 0
  like: 0
  mix: 0
  music: 0
  collect: 0
  collectmix: 0

thread: 5
retry_times: 3
proxy: ""
database: true
database_path: dy_downloader.db

progress:
  quiet_logs: true

browser_fallback:
  enabled: true
  headless: false
  max_scrolls: 240
  idle_rounds: 8
  wait_timeout_seconds: 600

comments:
  enabled: false
  include_replies: false
  max_comments: 0
  page_size: 20

cookies: {{}}
"""
    ensure_parent(config_path)
    config_path.write_text(content, encoding="utf-8")


def run(cmd: list[str], cwd: Path | None = None) -> None:
    info(f"Running: {' '.join(str(x) for x in cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def build_douyin_request_config(
    douyin_home: Path,
    base_config_path: Path,
    output_path: Path,
    output_dir: Path,
    video_only: bool,
    with_assets: bool,
    comments: bool,
    include_replies: bool,
    max_comments: int | None,
) -> None:
    overrides = {
        "path": f"{output_dir.expanduser()}/",
        "auto_cookie": True,
        "music": with_assets and not video_only,
        "cover": with_assets and not video_only,
        "avatar": with_assets and not video_only,
        "json": with_assets and not video_only,
        "mode": ["post"],
        "comments": {
            "enabled": comments,
            "include_replies": include_replies,
            "max_comments": 0 if max_comments is None else max_comments,
            "page_size": 20,
        },
    }
    code = r"""
import json
import sys
from pathlib import Path
import yaml

base_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
overrides = json.loads(sys.argv[3])
data = {}
if base_path.exists():
    data = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}

for key, value in overrides.items():
    if isinstance(value, dict) and isinstance(data.get(key), dict):
        merged = dict(data.get(key, {}))
        merged.update(value)
        data[key] = merged
    else:
        data[key] = value

output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(
    yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
"""
    run(
        [
            "uv",
            "run",
            "python",
            "-c",
            code,
            str(base_config_path),
            str(output_path),
            json.dumps(overrides, ensure_ascii=False),
        ],
        cwd=douyin_home,
    )


def command_doctor(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config)
    settings, exists = load_settings(config_path)
    runtime_dir = runtime_dir_from_settings(settings)
    yt_bin = detect_yt_dlp(settings, runtime_dir)
    douyin_home = detect_douyin_home(settings, runtime_dir)
    douyin_config = resolve_douyin_config_path(settings, douyin_home) if douyin_home else None

    result = {
        "project_root": str(PROJECT_ROOT),
        "skill_dir": str(SKILL_DIR),
        "project_config_path": str(PROJECT_CONFIG),
        "global_config_path": str(GLOBAL_CONFIG),
        "resolved_config_path": str(config_path),
        "resolved_config_exists": exists,
        "runtime_dir": str(runtime_dir),
        "yt_dlp": {
            "available": bool(yt_bin),
            "path": str(yt_bin) if yt_bin else "",
            "configured_path": settings.get("yt_dlp_path", ""),
        },
        "douyin": {
            "available": bool(douyin_home),
            "home": str(douyin_home) if douyin_home else "",
            "configured_home": settings.get("douyin_downloader_home", ""),
            "config_path": str(douyin_config) if douyin_config else "",
            "cookies_json": str((douyin_home / "config" / "cookies.json")) if douyin_home else "",
        },
    }

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0


def command_install_douyin(args: argparse.Namespace) -> int:
    settings, _ = load_settings(resolve_config_path(args.config))
    runtime_dir = runtime_dir_from_settings(settings)
    repo_dir = expand_path(args.repo_dir) or runtime_douyin_home(runtime_dir)
    default_output_dir = expand_path(settings.get("default_output_dir")) or (Path.home() / "Downloads" / "video-downloads")
    repo_dir.parent.mkdir(parents=True, exist_ok=True)

    if not shutil.which("git"):
        die("git is required to install douyin-downloader")
    if not shutil.which("uv"):
        die("uv is required to install douyin-downloader")

    if (repo_dir / ".git").exists():
        run(["git", "pull", "--ff-only"], cwd=repo_dir)
    elif repo_dir.exists() and any(repo_dir.iterdir()):
        die(f"Install path is not empty and not a git checkout: {repo_dir}")
    else:
        run(["git", "clone", DOUYIN_REMOTE, str(repo_dir)], cwd=repo_dir.parent)

    run(["uv", "sync", "--extra", "browser"], cwd=repo_dir)
    if not args.skip_browser_install:
        run(["uv", "run", "python", "-m", "playwright", "install", "chromium"], cwd=repo_dir)

    configured_config = expand_path(settings.get("douyin_config_path"))
    config_path = configured_config or (repo_dir / "config.yml")
    if configured_config and not configured_config.exists():
        write_default_douyin_config(config_path, default_output_dir)
    elif not configured_config and not config_path.exists():
        write_default_douyin_config(config_path, default_output_dir)

    info(f"douyin-downloader ready at {repo_dir}")
    return 0


def command_install_ytdlp(args: argparse.Namespace) -> int:
    settings, _ = load_settings(resolve_config_path(args.config))
    runtime_dir = runtime_dir_from_settings(settings)
    install_root = expand_path(args.venv_dir) or (runtime_dir / "yt-dlp" / ".venv")
    if not shutil.which("uv"):
        die("uv is required to install yt-dlp")

    install_root.parent.mkdir(parents=True, exist_ok=True)
    run(["uv", "venv", str(install_root)], cwd=PROJECT_ROOT)
    python_bin = runtime_python_bin(install_root)
    run(["uv", "pip", "install", "--python", str(python_bin), "--upgrade", "yt-dlp"], cwd=PROJECT_ROOT)

    yt_bin = yt_dlp_bin_from_venv(install_root)
    info(f"yt-dlp ready at {yt_bin}")
    return 0


def command_download(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config)
    settings, _ = load_settings(config_path)
    runtime_dir = runtime_dir_from_settings(settings)
    backend = resolve_backend(args.url, args.backend, settings)
    info(f"Selected backend: {backend}")

    output_dir = expand_path(args.output_dir)
    if not output_dir:
        output_dir = expand_path(settings.get("default_output_dir")) or (Path.home() / "Downloads" / "video-downloads")
    output_dir.mkdir(parents=True, exist_ok=True)

    if backend == "yt-dlp":
        yt_bin = detect_yt_dlp(settings, runtime_dir)
        if not yt_bin:
            die(
                "yt-dlp backend is unavailable. Ask the user whether to install yt-dlp, "
                "or set [video-downloader].yt_dlp_path in agent_config.toml."
            )
        template = settings.get("yt_dlp_output_template", "%(title)s [%(id)s].%(ext)s")
        run([str(yt_bin), "-o", str(output_dir / template), args.url], cwd=PROJECT_ROOT)
        return 0

    if backend != "douyin":
        die(f"Unsupported backend: {backend}")

    douyin_home = detect_douyin_home(settings, runtime_dir)
    if not douyin_home:
        die(
            "douyin backend is unavailable. Ask the user whether to install douyin-downloader, "
            "or set [video-downloader].douyin_downloader_home in agent_config.toml."
        )
    if not shutil.which("uv"):
        die("uv is required to run douyin-downloader")

    configured_base = expand_path(settings.get("douyin_config_path"))
    if configured_base and not configured_base.exists():
        die(
            f"douyin_config_path does not exist: {configured_base}\n"
            "Fix the path in agent_config.toml, or install/configure douyin-downloader first."
        )
    base_config_path = configured_base or (douyin_home / "config.yml")
    if not base_config_path.exists():
        write_default_douyin_config(base_config_path, output_dir)

    request_config = runtime_dir / "state" / "request-config.yml"
    build_douyin_request_config(
        douyin_home,
        base_config_path,
        request_config,
        output_dir,
        video_only=args.video_only,
        with_assets=args.with_assets,
        comments=args.comments,
        include_replies=args.include_replies,
        max_comments=args.max_comments,
    )
    run(
        [
            "uv",
            "run",
            "python",
            "run.py",
            "-c",
            str(request_config),
            "-u",
            args.url,
            "--show-warnings",
        ],
        cwd=douyin_home,
    )
    return 0


def command_refresh_cookies(args: argparse.Namespace) -> int:
    """刷新抖音 Cookie"""
    config_path = resolve_config_path(args.config)
    settings, _ = load_settings(config_path)
    runtime_dir = runtime_dir_from_settings(settings)

    douyin_home = detect_douyin_home(settings, runtime_dir)
    if not douyin_home:
        die(
            "douyin backend is unavailable. Ask the user whether to install douyin-downloader, "
            "or set [video-downloader].douyin_downloader_home in agent_config.toml."
        )
    if not shutil.which("uv"):
        die("uv is required to run douyin-downloader")

    configured_base = expand_path(settings.get("douyin_config_path"))
    if configured_base and not configured_base.exists():
        die(
            f"douyin_config_path does not exist: {configured_base}\n"
            "Fix the path in agent_config.toml, or install/configure douyin-downloader first."
        )
    base_config_path = configured_base or (douyin_home / "config.yml")
    if not base_config_path.exists():
        default_output_dir = expand_path(settings.get("default_output_dir")) or (Path.home() / "Downloads" / "video-downloads")
        write_default_douyin_config(base_config_path, default_output_dir)

    info("Launching browser for Douyin login...")
    run(
        ["uv", "run", "python", "-m", "tools.cookie_fetcher", "--config", str(base_config_path)],
        cwd=douyin_home,
    )
    return 0


def command_hot_board(args: argparse.Namespace) -> int:
    """获取抖音热搜榜"""
    config_path = resolve_config_path(args.config)
    settings, _ = load_settings(config_path)
    runtime_dir = runtime_dir_from_settings(settings)

    douyin_home = detect_douyin_home(settings, runtime_dir)
    if not douyin_home:
        die(
            "douyin backend is unavailable. Ask the user whether to install douyin-downloader, "
            "or set [video-downloader].douyin_downloader_home in agent_config.toml."
        )
    if not shutil.which("uv"):
        die("uv is required to run douyin-downloader")

    configured_base = expand_path(settings.get("douyin_config_path"))
    if configured_base and not configured_base.exists():
        die(
            f"douyin_config_path does not exist: {configured_base}\n"
            "Fix the path in agent_config.toml, or install/configure douyin-downloader first."
        )
    base_config_path = configured_base or (douyin_home / "config.yml")
    if not base_config_path.exists():
        default_output_dir = expand_path(settings.get("default_output_dir")) or (Path.home() / "Downloads" / "video-downloads")
        write_default_douyin_config(base_config_path, default_output_dir)

    output_dir = expand_path(args.output_dir)
    if not output_dir:
        output_dir = expand_path(settings.get("hot_board_output_dir")) or (Path.home() / "Downloads" / "video-downloads")

    cmd = ["uv", "run", "python", "run.py", "-c", str(base_config_path), "--hot-board", str(args.limit)]
    info(f"Fetching hot board with limit={args.limit}...")
    run(cmd, cwd=douyin_home)
    return 0


def command_search(args: argparse.Namespace) -> int:
    """搜索抖音作品"""
    config_path = resolve_config_path(args.config)
    settings, _ = load_settings(config_path)
    runtime_dir = runtime_dir_from_settings(settings)

    douyin_home = detect_douyin_home(settings, runtime_dir)
    if not douyin_home:
        die(
            "douyin backend is unavailable. Ask the user whether to install douyin-downloader, "
            "or set [video-downloader].douyin_downloader_home in agent_config.toml."
        )
    if not shutil.which("uv"):
        die("uv is required to run douyin-downloader")

    configured_base = expand_path(settings.get("douyin_config_path"))
    if configured_base and not configured_base.exists():
        die(
            f"douyin_config_path does not exist: {configured_base}\n"
            "Fix the path in agent_config.toml, or install/configure douyin-downloader first."
        )
    base_config_path = configured_base or (douyin_home / "config.yml")
    if not base_config_path.exists():
        default_output_dir = expand_path(settings.get("default_output_dir")) or (Path.home() / "Downloads" / "video-downloads")
        write_default_douyin_config(base_config_path, default_output_dir)

    output_dir = expand_path(args.output_dir)
    if not output_dir:
        output_dir = expand_path(settings.get("search_output_dir")) or (Path.home() / "Downloads" / "video-downloads")

    cmd = ["uv", "run", "python", "run.py", "-c", str(base_config_path), "--search", args.keyword, "--search-max", str(args.max)]
    info(f"Searching for '{args.keyword}' with max={args.max}...")
    run(cmd, cwd=douyin_home)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified helper for Douyin and yt-dlp downloads.")
    parser.add_argument("--config", help="Explicit agent_config.toml path")
    parser.add_argument("--non-interactive", action="store_true", help="Reserved for automation; the script never prompts.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Inspect config and backend availability")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    doctor.set_defaults(func=command_doctor)

    install_douyin = subparsers.add_parser("install-douyin", help="Install or update douyin-downloader into runtime dir")
    install_douyin.add_argument("--repo-dir", help="Override douyin-downloader install directory")
    install_douyin.add_argument("--skip-browser-install", action="store_true", help="Skip Playwright Chromium install")
    install_douyin.set_defaults(func=command_install_douyin)

    install_ytdlp = subparsers.add_parser("install-yt-dlp", help="Install or update yt-dlp into runtime dir")
    install_ytdlp.add_argument("--venv-dir", help="Override yt-dlp virtualenv directory")
    install_ytdlp.set_defaults(func=command_install_ytdlp)

    download = subparsers.add_parser("download", help="Download a URL")
    download.add_argument("url", help="Video URL to download")
    download.add_argument("--backend", choices=("auto", "douyin", "yt-dlp"), default="auto")
    download.add_argument("--output-dir", help="Override output directory")
    asset_mode = download.add_mutually_exclusive_group()
    asset_mode.add_argument("--video-only", action="store_true", help="For douyin: video only")
    asset_mode.add_argument("--with-assets", action="store_true", help="For douyin: include music, cover, avatar, JSON")
    download.add_argument("--comments", action="store_true", help="For douyin: collect comments")
    download.add_argument("--include-replies", action="store_true", help="For douyin comments: include second-level replies")
    download.add_argument("--max-comments", type=int, help="For douyin comments: 0 means unlimited")
    download.set_defaults(func=command_download)

    refresh_cookies = subparsers.add_parser("refresh-cookies", help="刷新抖音 Cookie（启动浏览器登录）")
    refresh_cookies.set_defaults(func=command_refresh_cookies)

    hot_board = subparsers.add_parser("hot-board", help="获取抖音热搜榜")
    hot_board.add_argument("--limit", type=int, default=30, help="限制条数（默认 30，0 表示全部）")
    hot_board.add_argument("--output-dir", help="输出目录")
    hot_board.set_defaults(func=command_hot_board)

    search = subparsers.add_parser("search", help="搜索抖音作品")
    search.add_argument("keyword", help="搜索关键词")
    search.add_argument("--max", type=int, default=50, help="最大条数（默认 50）")
    search.add_argument("--output-dir", help="输出目录")
    search.set_defaults(func=command_search)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
