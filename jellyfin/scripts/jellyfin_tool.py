#!/usr/bin/env python3
"""Jellyfin 媒体库文件重命名工具。"""

import os
import re
import sys
import click
import httpx
import tomllib
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".ts", ".m2ts", ".flv", ".rmvb"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tbn"}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".vtt"}

EPISODE_PATTERN = re.compile(r'(?:EP?|S\d+E?)(\d+)(?:[_\-](?:EP?|E)?(\d+))?', re.IGNORECASE)
SEASON_PATTERN = re.compile(r'S(\d+)E\d+', re.IGNORECASE)
YEAR_PATTERN = re.compile(r'^(19|20)\d{2}$')


def _find_config() -> dict:
    """返回 [jellyfin] 配置段，包含 omdb.api_key 和 omdb.base_url。"""
    skill_dir = Path(__file__).resolve().parent.parent
    candidates = [
        Path.cwd() / "agent_config.toml",
        skill_dir / "agent_config.toml",
    ]
    for parent in Path.cwd().parents:
        if (parent / ".git").exists():
            candidates.append(parent / "agent_config.toml")
            break
    for candidate in candidates:
        if candidate.exists():
            try:
                config = tomllib.loads(candidate.read_text(encoding="utf-8"))
                return config.get("jellyfin", {})
            except Exception:
                pass
    return {}


def _get_omdb_config(cfg: dict) -> tuple[str, str]:
    """从配置中提取 OMDb base_url 和 api_key。
    支持两种格式：
      [jellyfin.omdb] api_key = "..."  （新格式）
      [jellyfin] omdb_api_key = "..."  （旧格式兼容）
    """
    omdb = cfg.get("omdb", {})
    api_key = omdb.get("api_key") or cfg.get("omdb_api_key", "")
    base_url = omdb.get("base_url", "http://www.omdbapi.com").rstrip("/")
    return base_url, api_key


def _parse_folder_name(name: str) -> dict:
    """解析 BT/字幕组风格的文件夹名，提取标题、年份、媒体类型和集数信息。"""
    tokens = re.split(r'[._]+', name.strip())

    year = None
    year_idx = None
    for i, t in enumerate(tokens):
        if YEAR_PATTERN.match(t):
            year = int(t)
            year_idx = i
            break

    title_tokens = tokens[:year_idx] if year_idx is not None else tokens
    after_tokens = tokens[year_idx + 1:] if year_idx is not None else []

    cjk_parts = []
    eng_parts = []
    for t in title_tokens:
        cjk = re.sub(r'[^一-鿿㐀-䶿]', '', t)
        eng = re.sub(r'[一-鿿㐀-䶿]', '', t).strip()
        if cjk:
            cjk_parts.append(cjk)
        if eng:
            eng_parts.append(eng)

    media_type = 'movie'
    season = 1
    ep_start = ep_end = None

    for t in after_tokens:
        m = EPISODE_PATTERN.search(t)
        if m:
            media_type = 'series'
            ep_start = int(m.group(1))
            if m.group(2):
                ep_end = int(m.group(2))
            sm = SEASON_PATTERN.search(t)
            if sm:
                season = int(sm.group(1))
            break

    return {
        'original': name,
        'year': year,
        'cjk_title': ''.join(cjk_parts),
        'eng_title': ' '.join(eng_parts),
        'media_type': media_type,
        'season': season,
        'ep_start': ep_start,
        'ep_end': ep_end,
    }


def _query_omdb(title: str, year: int | None, media_type: str, base_url: str, api_key: str) -> dict:
    """查询 OMDb API，返回 {found, imdb_id, title, year, candidates}。"""
    if not api_key:
        click.echo("错误：未配置 OMDb API Key，请在 agent_config.toml 中设置 [jellyfin.omdb] api_key", err=True)
        sys.exit(1)

    omdb_type = 'series' if media_type == 'series' else 'movie'
    url = f"{base_url}/"

    try:
        with httpx.Client(timeout=15.0) as client:
            params: dict = {'apikey': api_key, 't': title, 'type': omdb_type}
            if year:
                params['y'] = year
            r = client.get(url, params=params)
            r.raise_for_status()
            d = r.json()
            if d.get('Response') == 'True':
                return {
                    'found': True,
                    'imdb_id': d['imdbID'],
                    'title': d['Title'],
                    'year': d['Year'].split('–')[0].strip(),
                    'candidates': [],
                }

            r2 = client.get(url, params={'apikey': api_key, 's': title, 'type': omdb_type})
            r2.raise_for_status()
            d2 = r2.json()
            candidates = []
            if d2.get('Response') == 'True':
                candidates = [
                    {'imdb_id': x['imdbID'], 'title': x['Title'], 'year': x['Year']}
                    for x in d2.get('Search', [])
                ]
            return {'found': False, 'imdb_id': '', 'title': '', 'year': '', 'candidates': candidates}

    except httpx.ConnectError:
        click.echo(f"错误：无法连接 OMDb API ({base_url})，请检查网络", err=True)
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        click.echo(f"OMDb API 错误 {e.response.status_code}", err=True)
        sys.exit(1)


def _lookup_by_imdb_id(imdb_id: str, base_url: str, api_key: str) -> dict | None:
    """通过 IMDB ID 查询官方标题和年份。"""
    if not api_key:
        return None
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(f"{base_url}/", params={'apikey': api_key, 'i': imdb_id})
            d = r.json()
            if d.get('Response') == 'True':
                return {'title': d['Title'], 'year': d['Year'].split('–')[0].strip()}
    except Exception:
        pass
    return None


def _parse_ep_from_name(name: str) -> tuple[int | None, int | None]:
    """从文件名中解析集数，返回 (ep_start, ep_end)。"""
    m = EPISODE_PATTERN.search(Path(name).stem)
    if m:
        return int(m.group(1)), int(m.group(2)) if m.group(2) else None
    return None, None


def _ep_filename(show: str, season: int, ep: int, ep_end: int | None, ext: str) -> str:
    """构建剧集文件名。"""
    if ep_end:
        return f"{show} S{season:02d}E{ep:02d}-E{ep_end:02d}{ext}"
    return f"{show} S{season:02d}E{ep:02d}{ext}"


@click.group()
def cli():
    """Jellyfin 媒体库文件重命名工具。"""
    pass


@cli.command()
@click.argument('directory', default='.', type=click.Path(exists=True, file_okay=False))
@click.option('--yes', '-y', is_flag=True, help='跳过确认，直接执行')
@click.option('--dry-run', is_flag=True, help='只预览，不执行')
def clean(directory, yes, dry_run):
    """批量清理：去掉文件夹/文件名中的空格，图片重命名为 poster。"""
    base = Path(directory).resolve()
    file_renames = []
    folder_renames = []

    for name in sorted(os.listdir(base)):
        folder = base / name
        if not folder.is_dir():
            continue
        videos, images = [], []
        for f in sorted(os.listdir(folder)):
            ext = Path(f).suffix.lower()
            if ext in VIDEO_EXTS:
                videos.append(f)
            elif ext in IMAGE_EXTS:
                images.append(f)
        if not videos and not images:
            continue

        first_stem = None
        for v in videos:
            t = v.replace(' ', '')
            if v != t:
                file_renames.append((folder, v, t))
            if first_stem is None:
                first_stem = Path(t).stem
        for idx, img in enumerate(images):
            if idx == 0 and first_stem:
                t = f"{first_stem}-poster{Path(img).suffix}"
            elif first_stem:
                t = f"extrafanart/fanart{idx}{Path(img).suffix}"
            else:
                t = img.replace(' ', '')
            if img != t:
                file_renames.append((folder, img, t))
        new_name = name.replace(' ', '')
        if new_name != name:
            folder_renames.append((base, name, new_name))

    if not file_renames and not folder_renames:
        click.echo("没有需要重命名的内容。")
        return

    if file_renames:
        click.echo("=" * 60)
        click.echo("文件重命名：")
        click.echo("-" * 60)
        for folder, old, new in file_renames:
            click.echo(f"  [{folder.name}]\n    {old}\n  → {new}\n")
    if folder_renames:
        click.echo("=" * 60)
        click.echo("文件夹重命名：")
        click.echo("-" * 60)
        for _, old, new in folder_renames:
            click.echo(f"  {old}\n→ {new}\n")

    if dry_run:
        click.echo("[预览模式，未执行]")
        return
    if not yes:
        if click.prompt("确认执行以上重命名？[y/N]", default='N').lower() != 'y':
            click.echo("已取消。")
            return

    for folder, old, new in file_renames:
        (folder / new).parent.mkdir(parents=True, exist_ok=True)
        (folder / old).rename(folder / new)
    for parent, old, new in folder_renames:
        (parent / old).rename(parent / new)
    click.echo("重命名完成！")


@cli.command()
@click.argument('folder', type=click.Path(exists=True, file_okay=False))
@click.option('--batch', is_flag=True, help='批量模式：遍历 folder 下所有子目录')
@click.option('--type', 'media_type', type=click.Choice(['movie', 'series', 'auto']), default='auto',
              help='媒体类型（默认 auto 自动检测）')
@click.option('--title', 'title_override', default=None, help='手动指定英文搜索标题')
@click.option('--year', 'year_override', type=int, default=None, help='手动指定年份')
@click.option('--imdb-id', 'imdb_id_override', default=None, help='手动指定 IMDB ID（跳过 API 搜索）')
@click.option('--exclude', 'excludes', multiple=True, help='批量模式下要跳过的子目录名，可多次使用')
@click.option('--yes', '-y', is_flag=True, help='跳过确认，直接执行')
@click.option('--dry-run', is_flag=True, help='只预览，不执行')
def rename(folder, batch, media_type, title_override, year_override, imdb_id_override, excludes, yes, dry_run):
    """智能重命名：按 Jellyfin 规范重命名电影/剧集文件夹及内部文件。

    解析 BT/字幕组风格文件夹名（如 Movie.Name.2020.1080P.X264），
    查询 OMDb API 获取 IMDB ID，重命名为 Jellyfin 标准格式。
    """
    cfg = _find_config()
    base_url, api_key = _get_omdb_config(cfg)
    base = Path(folder).resolve()

    if batch:
        subdirs = sorted([d for d in base.iterdir() if d.is_dir() and d.name not in excludes])
        failures = []
        for d in subdirs:
            click.echo(f"\n{'='*60}\n处理：{d.name}\n{'='*60}")
            ok = _rename_one(d, media_type, title_override, year_override,
                             imdb_id_override, yes, dry_run, base_url, api_key)
            if not ok:
                failures.append(d.name)
        if failures:
            click.echo(f"\n{'='*60}")
            click.echo(f"以下 {len(failures)} 个文件夹需要手动处理：")
            for n in failures:
                click.echo(f"  - {n}")
            click.echo("使用 --imdb-id 手动指定后重试。")
    else:
        _rename_one(base, media_type, title_override, year_override,
                    imdb_id_override, yes, dry_run, base_url, api_key)


def _rename_one(
    folder: Path,
    media_type: str,
    title_override: str | None,
    year_override: int | None,
    imdb_id_override: str | None,
    yes: bool,
    dry_run: bool,
    base_url: str,
    api_key: str,
) -> bool:
    """处理单个文件夹的重命名，返回 True 表示成功。"""
    parsed = _parse_folder_name(folder.name)
    search_title = title_override or parsed['eng_title'] or parsed['cjk_title']
    year = year_override or parsed['year']
    actual_type = parsed['media_type'] if media_type == 'auto' else media_type

    click.echo(
        f"解析：中文={parsed['cjk_title'] or '(无)'}  英文={parsed['eng_title'] or '(无)'}  "
        f"年份={year or '?'}  类型={actual_type}"
    )
    if actual_type == 'series' and parsed['ep_start']:
        ep_info = f"{parsed['ep_start']}-{parsed['ep_end']}" if parsed['ep_end'] else str(parsed['ep_start'])
        click.echo(f"       集数：第 {parsed['season']} 季 EP{ep_info}")

    if not search_title:
        click.echo("错误：无法解析标题，请用 --title 手动指定", err=True)
        return False

    # 获取官方标题和 IMDB ID
    if imdb_id_override:
        info = _lookup_by_imdb_id(imdb_id_override, base_url, api_key)
        if info:
            official_title, official_year = info['title'], info['year']
        else:
            official_title = title_override or search_title
            official_year = str(year) if year else '????'
        imdb_id = imdb_id_override
        click.echo(f"使用 IMDB ID {imdb_id} → {official_title} ({official_year})")
    else:
        click.echo(f"查询 OMDb：「{search_title}」({year}, {actual_type})...")
        result = _query_omdb(search_title, year, actual_type, base_url, api_key)
        if not result['found']:
            if result['candidates']:
                click.echo("找到多个候选，请用 --imdb-id 指定：")
                for i, c in enumerate(result['candidates'][:10], 1):
                    click.echo(f"  {i}. {c['title']} ({c['year']}) [{c['imdb_id']}]")
            else:
                click.echo("未找到匹配结果，请用 --title 调整搜索词或 --imdb-id 手动指定", err=True)
            return False
        official_title = result['title']
        official_year = result['year']
        imdb_id = result['imdb_id']
        click.echo(f"找到：{official_title} ({official_year}) [{imdb_id}]")

    # 若原文件夹名包含中文标题，则保留并拼接在官方英文标题前
    cjk_prefix = f"{parsed['cjk_title']} " if parsed['cjk_title'] else ""
    display_title = f"{cjk_prefix}{official_title}"
    new_folder_name = f"{display_title} ({official_year}) [imdbid-{imdb_id}]"
    new_folder = folder.parent / new_folder_name

    # 规划文件重命名
    file_renames: list[tuple[Path, Path]] = []
    poster_done = False
    fanart_count = 0
    for f in sorted(folder.iterdir()):
        if f.is_dir():
            continue
        ext = f.suffix.lower()
        if ext in VIDEO_EXTS:
            if actual_type == 'series':
                ep, ep_end_local = _parse_ep_from_name(f.name)
                if ep is not None:
                    new_name = _ep_filename(display_title, parsed['season'], ep, ep_end_local, f.suffix)
                else:
                    new_name = f.name
            else:
                new_name = f"{new_folder_name}{f.suffix}"
            file_renames.append((f, folder / new_name))
        elif ext in IMAGE_EXTS:
            # 多图时首图作 poster，其余按 Jellyfin 约定放入 extrafanart，避免同名互相覆盖
            if not poster_done:
                file_renames.append((f, folder / f"{new_folder_name}-poster{f.suffix}"))
                poster_done = True
            else:
                fanart_count += 1
                file_renames.append((f, folder / "extrafanart" / f"fanart{fanart_count}{f.suffix}"))

    # 显示预览
    click.echo(f"\n重命名计划：")
    click.echo(f"  文件夹：{folder.name}")
    click.echo(f"       → {new_folder_name}")
    changed = [(s, d) for s, d in file_renames if s.name != d.name]
    if changed:
        click.echo(f"  文件（{len(changed)} 个变更）：")
        for s, d in changed:
            click.echo(f"    {s.name}")
            click.echo(f"  → {d.name}")

    if dry_run:
        click.echo("\n[预览模式，未执行]")
        return True

    if not yes:
        if click.prompt("\n确认执行？[y/N]", default='N').lower() != 'y':
            click.echo("已取消。")
            return False

    # 先重命名文件，再重命名文件夹
    for src, dst in file_renames:
        if src != dst:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
    if folder != new_folder:
        folder.rename(new_folder)

    click.echo(f"完成！→ {new_folder_name}")
    return True


if __name__ == '__main__':
    cli()
