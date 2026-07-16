#!/usr/bin/env python3
"""Unified helper for Douyin, WeChat Channels, and yt-dlp downloads."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

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
DEFAULT_WX_CHANNELS_API_URL = "http://127.0.0.1:2022"
DEFAULT_WX_CHANNELS_TIMEOUT_SECONDS = 30
DEFAULT_WX_CHANNELS_LOGIN_TIMEOUT_SECONDS = 300
WX_CHANNELS_COOKIE_NAMES = ("hy_source", "hy_user", "hy_token")
WX_CHANNELS_REQUIRED_COOKIE_NAMES = frozenset({"hy_user", "hy_token"})
YUANBAO_URL = "https://yuanbao.tencent.com/"
WX_CHANNELS_TITLE_MAX_LENGTH = 30
DEFAULT_YT_DLP_OUTPUT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"
YT_DLP_AUTHOR_DIRECTORY_TEMPLATE = (
    "%(uploader,channel,creator,uploader_id,channel_id|unknown-author)s"
)


@dataclass(frozen=True)
class WxChannelsDownloadResult:
    path: Path
    description: str
    author_name: str


def die(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def info(message: str) -> None:
    print(f"INFO: {message}", file=sys.stderr)


def expand_path(value: str | None, base_dir: Path = SKILL_DIR) -> Path | None:
    if not value:
        return None
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


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


def is_wx_channels_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if host == "channels.weixin.qq.com" or host.endswith(".channels.weixin.qq.com"):
        return path == "/finder-preview/pages/sph" and bool(parse_qs(parsed.query).get("id"))
    if host == "weixin.qq.com" or host.endswith(".weixin.qq.com"):
        return path.startswith("/sph/")
    return False


def resolve_backend(url: str, requested: str, settings: dict) -> str:
    if requested != "auto":
        return requested
    configured = settings.get("default_backend", "auto")
    if configured in {"douyin", "wx-channels", "yt-dlp"}:
        return configured
    if is_douyin_url(url):
        return "douyin"
    if is_wx_channels_url(url):
        return "wx-channels"
    return "yt-dlp"


def build_yt_dlp_output_template(settings: dict) -> str:
    """Place every yt-dlp download below an author/account directory."""
    configured = settings.get("yt_dlp_output_template", DEFAULT_YT_DLP_OUTPUT_TEMPLATE)
    if not isinstance(configured, str) or not configured.strip():
        raise ValueError("yt_dlp_output_template must be a non-empty string")

    relative_template = configured.strip()
    posix_path = PurePosixPath(relative_template)
    windows_path = PureWindowsPath(relative_template)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ValueError("yt_dlp_output_template must be relative to the author directory")

    parts = re.split(r"[\\/]", relative_template)
    if any(part == ".." for part in parts):
        raise ValueError("yt_dlp_output_template must not contain '..'")
    normalized_template = "/".join(part for part in parts if part not in {"", "."})
    if not normalized_template:
        raise ValueError("yt_dlp_output_template must contain a file name template")

    return f"{YT_DLP_AUTHOR_DIRECTORY_TEMPLATE}/{normalized_template}"


def build_yt_dlp_download_command(
    yt_bin: Path,
    output_dir: Path,
    settings: dict,
    url: str,
) -> list[str]:
    return [
        str(yt_bin),
        "-P",
        str(output_dir),
        "-o",
        build_yt_dlp_output_template(settings),
        url,
    ]


def normalize_wx_channels_api_url(value: str | None) -> str:
    raw = (value or DEFAULT_WX_CHANNELS_API_URL).strip().rstrip("/")
    if raw.endswith("/api"):
        raw = raw[:-4]
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("wx_channels_api_url must be an HTTP(S) base URL without query or fragment")
    return raw.rstrip("/")


def wx_channels_timeout(settings: dict) -> float:
    value = settings.get("wx_channels_timeout_seconds", DEFAULT_WX_CHANNELS_TIMEOUT_SECONDS)
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("wx_channels_timeout_seconds must be a positive number") from exc
    if timeout <= 0:
        raise ValueError("wx_channels_timeout_seconds must be a positive number")
    return timeout


def wx_channels_login_timeout(settings: dict, override: float | None = None) -> float:
    value = override if override is not None else settings.get(
        "wx_channels_login_timeout_seconds", DEFAULT_WX_CHANNELS_LOGIN_TIMEOUT_SECONDS
    )
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("wx_channels_login_timeout_seconds must be a positive number") from exc
    if timeout <= 0:
        raise ValueError("wx_channels_login_timeout_seconds must be a positive number")
    return timeout


def detect_wx_channels_binary(settings: dict) -> Path | None:
    configured = expand_path(settings.get("wx_channels_binary_path"))
    candidates: list[Path] = []
    if configured:
        candidates.append(configured)
    path_bin = shutil.which("wx_video_download")
    if path_bin:
        candidates.append(Path(path_bin))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def resolve_wx_channels_config_path(settings: dict, binary: Path | None) -> Path | None:
    configured = expand_path(settings.get("wx_channels_config_path"))
    if configured:
        return configured
    if binary:
        adjacent = binary.parent / "config.yaml"
        if adjacent.exists():
            return adjacent
    return None


def wx_channels_config_path_for_update(settings: dict) -> Path:
    configured = expand_path(settings.get("wx_channels_config_path"))
    if configured:
        return configured
    binary = detect_wx_channels_binary(settings)
    if not binary:
        raise RuntimeError(
            "wx_channels_download binary is unavailable; configure wx_channels_binary_path "
            "before refreshing its cookie"
        )
    return binary.parent / "config.yaml"


def wx_channels_browser_profile_dir(settings: dict, runtime_dir: Path) -> Path:
    return expand_path(settings.get("wx_channels_browser_profile_dir")) or (
        runtime_dir / "wx-channels-browser-profile"
    )


def format_wx_channels_cookie(cookies: list[dict]) -> str | None:
    values: dict[str, str] = {}
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if name in WX_CHANNELS_COOKIE_NAMES and isinstance(value, str) and value:
            values[name] = value
    if not WX_CHANNELS_REQUIRED_COOKIE_NAMES.issubset(values):
        return None
    return "; ".join(f"{name}={values[name]}" for name in WX_CHANNELS_COOKIE_NAMES if name in values)


def collect_wx_channels_cookie(profile_dir: Path, timeout: float) -> str:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright is unavailable. Run install-wx-channels-browser through the skill's uv environment."
        ) from exc

    profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        profile_dir.chmod(0o700)
    deadline = time.monotonic() + timeout
    old_umask = os.umask(0o077) if os.name != "nt" else None
    try:
        with sync_playwright() as playwright:
            try:
                context = playwright.chromium.launch_persistent_context(
                    str(profile_dir),
                    headless=False,
                )
            except PlaywrightError as exc:
                raise RuntimeError(
                    "Playwright Chromium is unavailable. Run install-wx-channels-browser first."
                ) from exc
            try:
                page = context.pages[0] if context.pages else context.new_page()
                try:
                    page.goto(YUANBAO_URL, wait_until="domcontentloaded", timeout=60_000)
                except PlaywrightError as exc:
                    raise RuntimeError("Failed to open the Tencent Yuanbao login page") from exc
                info("Complete the Tencent Yuanbao login in the opened browser window.")
                while time.monotonic() < deadline:
                    cookie = format_wx_channels_cookie(context.cookies(YUANBAO_URL))
                    if cookie:
                        return cookie
                    page.wait_for_timeout(1000)
                raise RuntimeError("Timed out waiting for Tencent Yuanbao login")
            finally:
                context.close()
    except PlaywrightError as exc:
        raise RuntimeError("Tencent Yuanbao login browser closed before authentication completed") from exc
    finally:
        if old_umask is not None:
            os.umask(old_umask)


def update_wx_channels_cookie(config_path: Path, cookie: str) -> None:
    try:
        from ruamel.yaml import YAML
        from ruamel.yaml.comments import CommentedMap
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ruamel.yaml is unavailable. Run this command through the skill's uv environment."
        ) from exc

    yaml = YAML()
    yaml.preserve_quotes = True
    try:
        if config_path.exists():
            with open(config_path, encoding="utf-8") as source:
                data = yaml.load(source) or CommentedMap()
        else:
            data = CommentedMap()
    except Exception as exc:
        raise RuntimeError(f"failed to parse wx_channels_download config: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("wx_channels_download config root must be a YAML mapping")
    cloudflare = data.get("cloudflare")
    if cloudflare is None:
        cloudflare = CommentedMap()
        data["cloudflare"] = cloudflare
    if not isinstance(cloudflare, dict):
        raise RuntimeError("wx_channels_download cloudflare config must be a YAML mapping")
    cloudflare["sphCookie"] = cookie

    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{config_path.name}.", dir=config_path.parent)
    temp_path = Path(temp_name)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        output_file = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with output_file as output:
            yaml.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, config_path)
        if os.name != "nt":
            config_path.chmod(0o600)
    except Exception:
        if fd >= 0:
            os.close(fd)
        temp_path.unlink(missing_ok=True)
        raise


def is_wx_channels_auth_error(message: str) -> bool:
    lowered = message.lower()
    markers = (
        "sphcookie",
        "hy_token",
        "hy_user",
        "unauthorized",
        "status 401",
        "status 403",
        "does not contain feedinfo",
        "did not return a playable video url",
    )
    return any(marker in lowered for marker in markers)


def _read_json_url(url: str, timeout: float) -> dict:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "video-downloader-skill/1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except HTTPError as exc:
        payload = exc.read()
        try:
            detail = json.loads(payload.decode("utf-8")).get("msg", str(exc))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            detail = str(exc)
        raise RuntimeError(detail) from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("wx_channels_download returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("wx_channels_download returned an unexpected response")
    return data


def probe_wx_channels_api(api_url: str, timeout: float = 1.0) -> tuple[bool, str, str]:
    try:
        payload = _read_json_url(f"{api_url}/api/status", timeout)
    except RuntimeError as exc:
        return False, "", str(exc)
    data = payload.get("data", {})
    version = data.get("version", "") if isinstance(data, dict) else ""
    return True, str(version), ""


def parse_wx_channels_video(share_url: str, api_url: str, timeout: float) -> dict:
    endpoint = f"{api_url}/api/channels/parse_sph?url={quote(share_url, safe='')}"
    payload = _read_json_url(endpoint, timeout)
    if payload.get("code") != 0:
        raise RuntimeError(str(payload.get("msg") or "failed to parse WeChat Channels share URL"))

    response_data = payload.get("data", {})
    profile_data = response_data.get("data", {}) if isinstance(response_data, dict) else {}
    feed_info = profile_data.get("feedInfo", {}) if isinstance(profile_data, dict) else {}
    author_info = profile_data.get("authorInfo", {}) if isinstance(profile_data, dict) else {}
    if not isinstance(feed_info, dict):
        raise RuntimeError("wx_channels_download response does not contain feedInfo")

    video_url = feed_info.get("originVideoUrl") or feed_info.get("videoUrl")
    if not video_url:
        for key in ("h264VideoInfo", "h265VideoInfo"):
            candidate = feed_info.get(key, {})
            if isinstance(candidate, dict) and candidate.get("videoUrl"):
                video_url = candidate["videoUrl"]
                break
    if not isinstance(video_url, str) or not video_url.startswith(("http://", "https://")):
        raise RuntimeError("wx_channels_download did not return a playable video URL")
    author_name = author_info.get("nickname", "") if isinstance(author_info, dict) else ""
    return {
        "video_url": video_url,
        "description": str(feed_info.get("description") or ""),
        "author_name": str(author_name or ""),
    }


def wx_channels_share_id(share_url: str) -> str:
    parsed = urlparse(share_url)
    if parsed.hostname and parsed.hostname.lower().endswith("weixin.qq.com"):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[-2] == "sph":
            return parts[-1]
    return (parse_qs(parsed.query).get("id") or ["video"])[0]


def safe_media_stem(description: str, share_id: str) -> str:
    title = next(
        (line.strip() for line in description.splitlines() if line.strip()),
        "视频号作品",
    )
    title = re.sub(r"#[^#\s]+", " ", title)
    title = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "", title)
    title = "".join(
        character
        for character in title
        if not unicodedata.category(character).startswith(("C", "S"))
    )
    title = re.sub(r"\s+", " ", title).strip(" ._-")
    title = title[:WX_CHANNELS_TITLE_MAX_LENGTH].rstrip(" ._-")
    title = title or "视频号作品"
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", share_id)[:80] or "video"
    return f"{title} [{safe_id}]"


def safe_wx_channels_author_dir(author_name: str) -> str:
    name = author_name.replace("\n", " ").replace("\r", " ")
    name = re.sub(r'[<>:"/\\|?*#\x00-\x1f]', "_", name)
    name = re.sub(r"_+", "_", name)
    name = re.sub(r" +", " ", name).strip("._- ")
    name = name[:80].rstrip("._- ")
    return name or "unknown-channel"


def download_wx_channels_video(
    share_url: str,
    output_dir: Path,
    api_url: str,
    timeout: float,
) -> WxChannelsDownloadResult:
    parsed = parse_wx_channels_video(share_url, api_url, timeout)
    filename = f"{safe_media_stem(parsed['description'], wx_channels_share_id(share_url))}.mp4"
    channel_dir = output_dir / safe_wx_channels_author_dir(parsed["author_name"])
    output_path = channel_dir / filename
    if output_path.exists() and output_path.stat().st_size > 0:
        info(f"Already downloaded: {output_path}")
        return WxChannelsDownloadResult(
            path=output_path,
            description=parsed["description"],
            author_name=parsed["author_name"],
        )

    legacy_path = output_dir / filename
    if legacy_path.exists() and legacy_path.stat().st_size > 0:
        channel_dir.mkdir(parents=True, exist_ok=True)
        legacy_path.replace(output_path)
        info(f"Moved legacy download into channel directory: {output_path}")
        return WxChannelsDownloadResult(
            path=output_path,
            description=parsed["description"],
            author_name=parsed["author_name"],
        )

    channel_dir.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_suffix(f"{output_path.suffix}.part")
    request = Request(
        parsed["video_url"],
        headers={
            "Referer": "https://channels.weixin.qq.com/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response, open(partial_path, "wb") as output:
            content_type = response.headers.get_content_type()
            if content_type in {"text/html", "application/json"}:
                raise RuntimeError(f"media URL returned unexpected content type: {content_type}")
            shutil.copyfileobj(response, output)
        if partial_path.stat().st_size == 0:
            raise RuntimeError("downloaded WeChat Channels video is empty")
        partial_path.replace(output_path)
    except (HTTPError, URLError, OSError, RuntimeError) as exc:
        partial_path.unlink(missing_ok=True)
        try:
            channel_dir.rmdir()
        except OSError:
            pass
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"failed to download WeChat Channels video: {exc}") from exc
    return WxChannelsDownloadResult(
        path=output_path,
        description=parsed["description"],
        author_name=parsed["author_name"],
    )


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
    candidate = expand_path(settings.get("douyin_downloader_home"))
    if not candidate:
        candidate = runtime_douyin_home(runtime_dir)
    if candidate and (candidate / "run.py").exists() and (candidate / "pyproject.toml").exists():
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
    wx_binary = detect_wx_channels_binary(settings)
    try:
        wx_api_url = normalize_wx_channels_api_url(settings.get("wx_channels_api_url"))
        wx_timeout = min(wx_channels_timeout(settings), 2.0)
        wx_reachable, wx_version, wx_error = probe_wx_channels_api(wx_api_url, wx_timeout)
    except ValueError as exc:
        wx_api_url = str(settings.get("wx_channels_api_url", ""))
        wx_reachable, wx_version, wx_error = False, "", str(exc)
    wx_config = resolve_wx_channels_config_path(settings, wx_binary)
    wx_profile = wx_channels_browser_profile_dir(settings, runtime_dir)

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
        "wx_channels": {
            "available": wx_reachable,
            "api_url": wx_api_url,
            "version": wx_version,
            "error": wx_error,
            "binary_path": str(wx_binary) if wx_binary else "",
            "configured_binary_path": settings.get("wx_channels_binary_path", ""),
            "config_path": str(wx_config) if wx_config else "",
            "config_exists": bool(wx_config and wx_config.exists()),
            "browser_profile_dir": str(wx_profile),
        },
    }

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.json else None))
    return 0


def command_install_douyin(args: argparse.Namespace) -> int:
    settings, _ = load_settings(resolve_config_path(args.config))
    runtime_dir = runtime_dir_from_settings(settings)
    repo_dir = (
        expand_path(args.repo_dir)
        or expand_path(settings.get("douyin_downloader_home"))
        or runtime_douyin_home(runtime_dir)
    )
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


def command_install_wx_channels_browser(args: argparse.Namespace) -> int:
    try:
        import playwright  # noqa: F401
    except ModuleNotFoundError:
        die(
            "Playwright is unavailable. Run this command with: "
            "uv run --project scripts python scripts/video_downloader.py install-wx-channels-browser"
        )
    run([sys.executable, "-m", "playwright", "install", "chromium"], cwd=SCRIPT_DIR)
    info("Playwright Chromium is ready for Tencent Yuanbao login")
    return 0


def build_wx_channels_service_command(settings: dict, server_args: list[str]) -> tuple[list[str], Path]:
    binary = detect_wx_channels_binary(settings)
    if not binary:
        die(
            "wx_channels_download binary is unavailable. Install wx_video_download, "
            "or set [video-downloader].wx_channels_binary_path in agent_config.toml."
        )
    config_path = resolve_wx_channels_config_path(settings, binary)
    if config_path and not config_path.exists():
        die(f"wx_channels_config_path does not exist: {config_path}")
    if config_path and os.name != "nt" and config_path.stat().st_mode & 0o077:
        info(f"Warning: protect the wx_channels_download config with: chmod 600 {config_path}")

    cmd = [str(binary)]
    if config_path:
        cmd.extend(["--config", str(config_path)])
    cmd.extend(server_args)
    return cmd, binary.parent


def wait_for_wx_channels_api(settings: dict, timeout: float = 10.0) -> None:
    api_url = normalize_wx_channels_api_url(settings.get("wx_channels_api_url"))
    deadline = time.monotonic() + timeout
    error = "service did not become ready"
    while time.monotonic() < deadline:
        reachable, _, error = probe_wx_channels_api(api_url, 1.0)
        if reachable:
            return
        time.sleep(0.25)
    raise RuntimeError(f"wx_channels_download API did not restart: {error}")


def restart_wx_channels_service(settings: dict) -> None:
    cmd, cwd = build_wx_channels_service_command(settings, ["server", "restart"])
    run(cmd, cwd=cwd)
    wait_for_wx_channels_api(settings)


def refresh_wx_channels_cookie(
    settings: dict,
    runtime_dir: Path,
    timeout_override: float | None = None,
) -> Path:
    timeout = wx_channels_login_timeout(settings, timeout_override)
    profile_dir = wx_channels_browser_profile_dir(settings, runtime_dir)
    config_path = wx_channels_config_path_for_update(settings)
    cookie = collect_wx_channels_cookie(profile_dir, timeout)
    update_wx_channels_cookie(config_path, cookie)
    del cookie
    restart_wx_channels_service(settings)
    info("Tencent Yuanbao authentication refreshed")
    return config_path


def download_wx_channels_with_auth_refresh(
    share_url: str,
    output_dir: Path,
    api_url: str,
    timeout: float,
    settings: dict,
    runtime_dir: Path,
    non_interactive: bool,
) -> WxChannelsDownloadResult:
    try:
        return download_wx_channels_video(share_url, output_dir, api_url, timeout)
    except RuntimeError as exc:
        if not is_wx_channels_auth_error(str(exc)):
            raise
        if non_interactive:
            raise RuntimeError(
                "wx_channels_download authentication is missing or expired; browser login is disabled "
                "in --non-interactive mode"
            ) from exc
        info("Tencent Yuanbao authentication is missing or expired; opening the login browser")
        refresh_wx_channels_cookie(settings, runtime_dir)
        return download_wx_channels_video(share_url, output_dir, api_url, timeout)


def command_start_wx_channels(args: argparse.Namespace) -> int:
    settings, _ = load_settings(resolve_config_path(args.config))
    try:
        api_url = normalize_wx_channels_api_url(settings.get("wx_channels_api_url"))
        timeout = min(wx_channels_timeout(settings), 2.0)
    except ValueError as exc:
        die(str(exc))
    reachable, version, _ = probe_wx_channels_api(api_url, timeout)
    if reachable:
        suffix = f" (version {version})" if version else ""
        info(f"wx_channels_download API is already running at {api_url}{suffix}")
        return 0

    cmd, cwd = build_wx_channels_service_command(settings, ["server", "--daemon"])
    run(cmd, cwd=cwd)
    return 0


def command_restart_wx_channels(args: argparse.Namespace) -> int:
    settings, _ = load_settings(resolve_config_path(args.config))
    try:
        restart_wx_channels_service(settings)
    except (RuntimeError, ValueError) as exc:
        die(str(exc))
    return 0


def command_refresh_wx_channels_cookie(args: argparse.Namespace) -> int:
    if args.non_interactive:
        die("refresh-wx-channels-cookie requires an interactive browser login")
    settings, _ = load_settings(resolve_config_path(args.config))
    runtime_dir = runtime_dir_from_settings(settings)
    try:
        refresh_wx_channels_cookie(settings, runtime_dir, args.timeout)
    except (RuntimeError, ValueError) as exc:
        die(str(exc))
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

    if backend == "wx-channels":
        if not is_wx_channels_url(args.url):
            die("wx-channels backend only supports weixin.qq.com/sph links and their preview URLs")
        try:
            api_url = normalize_wx_channels_api_url(settings.get("wx_channels_api_url"))
            timeout = wx_channels_timeout(settings)
            result = download_wx_channels_with_auth_refresh(
                args.url,
                output_dir,
                api_url,
                timeout,
                settings,
                runtime_dir,
                args.non_interactive,
            )
        except (RuntimeError, ValueError) as exc:
            message = str(exc)
            if "Connection refused" in message or "timed out" in message:
                die(
                    f"wx_channels_download API is unavailable at {api_url}. "
                    "Start the local service with start-wx-channels, or fix wx_channels_api_url."
                )
            die(f"wx-channels download failed: {message}")
        print(f"Downloaded file: {result.path}")
        print(f"Original description: {result.description}")
        return 0

    if backend == "yt-dlp":
        yt_bin = detect_yt_dlp(settings, runtime_dir)
        if not yt_bin:
            die(
                "yt-dlp backend is unavailable. Ask the user whether to install yt-dlp, "
                "or set [video-downloader].yt_dlp_path in agent_config.toml."
            )
        try:
            cmd = build_yt_dlp_download_command(yt_bin, output_dir, settings, args.url)
        except ValueError as exc:
            die(str(exc))
        run(cmd, cwd=PROJECT_ROOT)
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
    parser = argparse.ArgumentParser(description="Unified helper for Douyin, WeChat Channels, and yt-dlp downloads.")
    parser.add_argument("--config", help="Explicit agent_config.toml path")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable browser-based authentication refresh for automation",
    )

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

    install_wx_browser = subparsers.add_parser(
        "install-wx-channels-browser", help="Install Playwright Chromium for Tencent Yuanbao login"
    )
    install_wx_browser.set_defaults(func=command_install_wx_channels_browser)

    start_wx_channels = subparsers.add_parser(
        "start-wx-channels", help="Start the configured wx_channels_download API service"
    )
    start_wx_channels.set_defaults(func=command_start_wx_channels)

    restart_wx_channels = subparsers.add_parser(
        "restart-wx-channels", help="Restart wx_channels_download after changing its config"
    )
    restart_wx_channels.set_defaults(func=command_restart_wx_channels)

    refresh_wx_cookie = subparsers.add_parser(
        "refresh-wx-channels-cookie", help="Refresh wx_channels_download authentication through Tencent Yuanbao"
    )
    refresh_wx_cookie.add_argument(
        "--timeout", type=float, help="Override login timeout in seconds for this refresh"
    )
    refresh_wx_cookie.set_defaults(func=command_refresh_wx_channels_cookie)

    download = subparsers.add_parser("download", help="Download a URL")
    download.add_argument("url", help="Video URL to download")
    download.add_argument("--backend", choices=("auto", "douyin", "wx-channels", "yt-dlp"), default="auto")
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
