#!/usr/bin/env python3
"""Jellyfin 媒体库文件重命名工具。"""

import difflib
import json
import os
import re
import shutil
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
    candidates.append(Path.home() / ".agents" / "agent_config.toml")
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


def _get_server_config(cfg: dict) -> tuple[str, str]:
    """从 [jellyfin] 根段提取 Jellyfin 服务器 base_url 和 api_key（server/de-localart --refresh 用）。"""
    return cfg.get("base_url", "").rstrip("/"), cfg.get("api_key", "")


# ---------------------------------------------------------------------------
# OMDb 脏数据防御：标题清洗 / 脏标题过滤 / 文件名 sanitize / 本地缓存
# ---------------------------------------------------------------------------

CUT_SUFFIX = re.compile(r'(?:[-._ ]+(?:DC|SP|EXT|UNRATED|RECUT|REDUX))+$', re.IGNORECASE)
LEADING_SEQNO = re.compile(r'^\d{1,2}[-–—.\s]\s*')
DIRTY_TITLE = re.compile(r'\(\d{4}\).*\(\d{4}\)|/|making of|unmasked|live on stage', re.IGNORECASE)
_ILLEGAL_NAME_CHARS = re.compile(r'[/\\:*?"<>|]')


def _clean_search_title(title: str) -> str:
    """清洗搜索标题：去开头续集序号（"2 The Godfather Part II"、"5- Star Wars V-"）
    和尾部剪辑版标记（DC/SP/EXT/UNRATED/ReCut/Redux）。"""
    t = CUT_SUFFIX.sub('', (title or '').strip()).strip()
    m = LEADING_SEQNO.match(t)
    if m and t[m.end():].strip():
        t = t[m.end():].strip()
    return t.strip('-–—. ')


def _sanitize_filename(name: str) -> str:
    """去掉路径非法字符（如标题中的 /，否则会生成非法路径）。"""
    return _ILLEGAL_NAME_CHARS.sub(' ', name)


def _year_int(value) -> int | None:
    m = re.match(r'(\d{4})', str(value or ''))
    return int(m.group(1)) if m else None


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


IMDB_TAG = re.compile(r'\[imdbid-(tt\d+)\]', re.IGNORECASE)


def _titles_match(a: str, b: str, threshold: float = 0.6) -> bool:
    """规范化后比对两个标题：互相包含或相似度达标视为同一部。"""
    na = re.sub(r'[^a-z0-9 ]', '', (a or '').lower()).strip()
    nb = re.sub(r'[^a-z0-9 ]', '', (b or '').lower()).strip()
    if not na or not nb:
        return False
    return na in nb or nb in na or _similarity(na, nb) >= threshold


def _check_imdb_override(imdb_id: str, eng_title: str, year: int | None,
                         base_url: str, api_key: str, cache: 'OmdbCache | None' = None):
    """OMDb i= 反查手动指定的 IMDB ID，与文件名解析出的标题/年份比对。

    血的教训：人工凭记忆指定的 IMDB ID 极易记错（tt0406662 实际是 The Coiner，
    Se7en 记成 tt0113227 实际是 Die Grube）。返回 (可信, 消息, info)。
    无法反查（未配置 key / 网络失败）时返回 (True, 提示, None)，不阻塞流程。
    """
    info = cache.get(f"i:{imdb_id}") if cache is not None else None
    if info is None:
        info = _lookup_by_imdb_id(imdb_id, base_url, api_key)
        if info and cache is not None:
            cache.set(f"i:{imdb_id}", info)
    if not info:
        return True, f"无法反查 {imdb_id}（未配置 OMDb key 或网络失败），跳过验证", None
    problems = []
    if eng_title and not _titles_match(eng_title, info['title']):
        problems.append(f"标题不符：文件「{eng_title}」vs OMDb「{info['title']} ({info.get('year')})」")
    oy = _year_int(info.get('year'))
    if year and oy and abs(oy - year) > 2:
        problems.append(f"年份不符：文件 {year} vs OMDb {oy}")
    if problems:
        return False, f"IMDB ID {imdb_id} 疑似错误绑定：{'；'.join(problems)}", info
    return True, '', info


class OmdbCache:
    """omdb_cache.json 本地缓存：中断/失败重跑时不重复请求已查到的结果。"""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                self.data = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        try:
            # 原子写：先写临时文件再替换，防止进程被杀时留下半截 JSON
            tmp = self.path.with_name(self.path.name + '.tmp')
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding='utf-8')
            os.replace(tmp, self.path)
        except OSError:
            pass


def _query_omdb_smart(title: str, year: int | None, base_url: str, api_key: str,
                      cache: OmdbCache, no_cache: bool = False) -> dict:
    """rename-flat 用的强化查询：标题清洗、年份 ±2 容差、脏候选过滤、本地缓存。

    返回 {found, imdb_id, title, year, warning, searched, candidates}。
    no_cache=True 时忽略已有缓存（用于 OMDb 间歇返回垃圾数据后强制重查），结果仍会覆盖写入缓存。
    """
    cache_key = f"q:{title}|{year or ''}"
    if not no_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    result = _query_omdb_smart_uncached(title, year, base_url, api_key)
    cache.set(cache_key, result)
    return result


def _query_omdb_smart_uncached(title: str, year: int | None, base_url: str, api_key: str) -> dict:
    if not api_key:
        click.echo("错误：未配置 OMDb API Key，请在 agent_config.toml 中设置 [jellyfin.omdb] api_key", err=True)
        sys.exit(1)

    titles: list[str] = []
    for t in (title, _clean_search_title(title)):
        t = (t or '').strip()
        if t and t not in titles:
            titles.append(t)

    last_candidates: list[dict] = []
    try:
        with httpx.Client(timeout=15.0) as client:
            # 1) 精确匹配：原始标题优先，其次清洗后的标题（各尝试带年份/不带年份）
            for t in titles:
                extras = ({'y': year}, {}) if year else ({},)
                for extra in extras:
                    r = client.get(f"{base_url}/",
                                   params={'apikey': api_key, 't': t, 'type': 'movie', **extra})
                    r.raise_for_status()
                    d = r.json()
                    if d.get('Response') == 'True':
                        warning = None
                        oy = _year_int(d.get('Year'))
                        if year and oy and abs(oy - year) > 2:
                            warning = f"年份与原名不符：文件名 {year} vs OMDb {oy}"
                        return {'found': True, 'imdb_id': d['imdbID'], 'title': d['Title'],
                                'year': d['Year'].split('–')[0].strip(), 'warning': warning,
                                'searched': t, 'candidates': []}
            # 2) 搜索候选：过滤脏标题，年份 ±2 容差内按相似度选最优
            for t in titles:
                r = client.get(f"{base_url}/", params={'apikey': api_key, 's': t, 'type': 'movie'})
                r.raise_for_status()
                d = r.json()
                if d.get('Response') != 'True':
                    continue
                candidates = [{'imdb_id': x['imdbID'], 'title': x['Title'], 'year': x.get('Year', '')}
                              for x in d.get('Search', [])]
                last_candidates = candidates
                best = None
                best_score = -1.0
                for c in candidates:
                    if DIRTY_TITLE.search(c['title']):
                        continue  # 双年份 / 含 / / Making of / Unmasked / Live on Stage 直接排除
                    cy = _year_int(c['year'])
                    diff = abs(cy - year) if (cy and year) else None
                    if diff is not None and diff > 2:
                        continue  # 年份容差 ±2（美国上映年 vs 产地年常差 1）
                    score = _similarity(t, c['title']) + (2 - diff) * 0.05 if diff is not None \
                        else _similarity(t, c['title'])
                    if score > best_score:
                        best_score, best = score, c
                if best is None:
                    continue
                warning = None
                if _similarity(t, best['title']) < 0.6:
                    warning = f"相似度低：搜索「{t}」→ 命中「{best['title']}」({best['year']})"
                return {'found': True, 'imdb_id': best['imdb_id'], 'title': best['title'],
                        'year': str(_year_int(best['year']) or best['year']), 'warning': warning,
                        'searched': t, 'candidates': candidates}
            return {'found': False, 'imdb_id': '', 'title': '', 'year': '', 'warning': None,
                    'searched': titles[-1] if titles else title, 'candidates': last_candidates}

    except httpx.ConnectError:
        click.echo(f"错误：无法连接 OMDb API ({base_url})，请检查网络", err=True)
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        click.echo(f"OMDb API 错误 {e.response.status_code}", err=True)
        sys.exit(1)


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
        if re.fullmatch(r'[一-鿿㐀-䶿]+\d+', t):
            # 「教父2」「大话西游2」：CJK 后紧跟的数字是续集序号，属于中文标题
            cjk_parts.append(t)
            continue
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


def _match_subtitle_name(sub_file: Path, video_renames: list[tuple[Path, Path]]) -> str | None:
    """字幕 stem 与某视频 stem 相同（或仅多出 .chs 等语言标签后缀）时，返回跟随该视频的新文件名。"""
    sub_stem = sub_file.stem
    for src, dst in video_renames:
        v_stem = src.stem
        if sub_stem == v_stem or sub_stem.startswith(v_stem + '.'):
            remainder = sub_stem[len(v_stem):]
            return f"{dst.stem}{remainder}{sub_file.suffix}"
    return None


@click.group()
def cli():
    """Jellyfin 媒体库文件重命名与服务器维护工具。"""
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


@cli.command(name='rename-folder')
@click.argument('folder', type=click.Path(exists=True, file_okay=False))
@click.option('--batch', is_flag=True, help='批量模式：遍历 folder 下所有子目录')
@click.option('--type', 'media_type', type=click.Choice(['movie', 'series', 'auto']), default='auto',
              help='媒体类型（默认 auto 自动检测）')
@click.option('--title', 'title_override', default=None, help='手动指定英文搜索标题')
@click.option('--year', 'year_override', type=int, default=None, help='手动指定年份')
@click.option('--imdb-id', 'imdb_id_override', default=None, help='手动指定 IMDB ID（跳过 API 搜索）')
@click.option('--force-imdb', is_flag=True, help='OMDb 反查验证不通过时仍强制使用手动指定的 --imdb-id')
@click.option('--exclude', 'excludes', multiple=True, help='批量模式下要跳过的子目录名，可多次使用')
@click.option('--yes', '-y', is_flag=True, help='跳过确认，直接执行')
@click.option('--dry-run', is_flag=True, help='只预览，不执行')
def rename_folder(folder, batch, media_type, title_override, year_override, imdb_id_override,
                  force_imdb, excludes, yes, dry_run):
    """智能重命名：按 Jellyfin 规范重命名电影/剧集文件夹及内部文件。

    适用于「一部电影一个子文件夹」结构；平铺目录（多部电影共用一个目录）
    请使用 rename-flat。解析 BT/字幕组风格文件夹名（如 Movie.Name.2020.1080P.X264），
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
                             imdb_id_override, yes, dry_run, base_url, api_key, force_imdb)
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
                    imdb_id_override, yes, dry_run, base_url, api_key, force_imdb)


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
    force_imdb: bool = False,
) -> bool:
    """处理单个文件夹的重命名，返回 True 表示成功。"""
    parsed = _parse_folder_name(folder.name)
    search_title = title_override or _clean_search_title(parsed['eng_title']) or parsed['cjk_title']
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
        ok, msg, info = _check_imdb_override(imdb_id_override, parsed['eng_title'], year,
                                             base_url, api_key)
        if msg:
            click.echo(("⚠ " if not ok else "提示：") + msg)
        if not ok:
            if force_imdb:
                click.echo("--force-imdb 已指定，强制使用该 ID")
            elif yes:
                click.echo("错误：--yes 模式下拒绝采用可疑 IMDB ID；确认无误请加 --force-imdb", err=True)
                return False
            elif click.prompt("仍要使用该 IMDB ID？[y/N]", default='N').lower() != 'y':
                click.echo("已取消。")
                return False
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
    new_folder_name = re.sub(r'\s{2,}', ' ', _sanitize_filename(
        f"{display_title} ({official_year}) [imdbid-{imdb_id}]")).strip()
    new_folder = folder.parent / new_folder_name

    # 规划文件重命名；字幕需依据视频的新名称匹配，放在视频之后处理
    file_renames: list[tuple[Path, Path]] = []
    video_renames: list[tuple[Path, Path]] = []
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
            video_renames.append((f, folder / new_name))
        elif ext in IMAGE_EXTS:
            # 多图时首图作 poster，其余按 Jellyfin 约定放入 extrafanart，避免同名互相覆盖
            if not poster_done:
                file_renames.append((f, folder / f"{new_folder_name}-poster{f.suffix}"))
                poster_done = True
            else:
                fanart_count += 1
                file_renames.append((f, folder / "extrafanart" / f"fanart{fanart_count}{f.suffix}"))
    for f in sorted(folder.iterdir()):
        if f.is_dir() or f.suffix.lower() not in SUBTITLE_EXTS:
            continue
        sub_new = _match_subtitle_name(f, video_renames)
        if sub_new:
            file_renames.append((f, folder / sub_new))

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


# ---------------------------------------------------------------------------
# rename-flat：平铺目录（多部电影共用一个目录）批量重命名
# ---------------------------------------------------------------------------

FLAT_PREFIX = re.compile(r'^([A-Za-z]*\d{1,4}[._\- ])')
# 已规范命名里的排序前缀要求带字母（Top001.），避免把「12 Angry Men」的 12 误当前缀
RENAMED_PREFIX = re.compile(r'^([A-Za-z]+\d{1,4}[._\- ])')
IMG_SUFFIX = re.compile(r'-(poster|backdrop|landscape|logo|fanart\d*)$', re.IGNORECASE)


def _norm_key(s: str) -> str:
    return s.rstrip('._- ').lower()


def _flat_groups(base: Path) -> list[dict]:
    """把平铺目录的文件分组：有公共前缀（如 Top001.）的按前缀分组，无前缀的按 stem
    （去掉 -poster 等图片后缀）分组。无视频的组尝试并入视频 stem 为其前缀的组
    （如无前缀字幕 Movie.1994.chn.srt 并入 Movie.1994.mkv 所在组）。"""
    groups: dict[str, dict] = {}
    for f in sorted(base.iterdir()):
        if f.is_dir() or f.name == 'omdb_cache.json':
            continue
        m = FLAT_PREFIX.match(f.name)
        if m:
            prefix = m.group(1)
            key = 'p:' + _norm_key(prefix)
        else:
            prefix = ''
            key = 's:' + IMG_SUFFIX.sub('', f.stem).lower()
        g = groups.setdefault(key, {'prefix': prefix, 'files': []})
        if prefix and not g['prefix']:
            g['prefix'] = prefix
        g['files'].append(f)

    video_groups = []
    orphans = []
    for g in groups.values():
        videos = sorted(f for f in g['files'] if f.suffix.lower() in VIDEO_EXTS)
        if videos:
            g['videos'] = videos
            video_groups.append(g)
        else:
            orphans.append(g)
    for g in orphans:
        stems = [f.stem.lower() for f in g['files']]
        for vg in video_groups:
            v_stem = vg['videos'][0].stem.lower()
            if any(s == v_stem or s.startswith(v_stem + '.') or s.startswith(v_stem + '-')
                   for s in stems):
                vg['files'].extend(g['files'])
                break
    return video_groups


@cli.command(name='rename-flat')
@click.argument('directory', default='.', type=click.Path(exists=True, file_okay=False))
@click.option('--keep-prefix', is_flag=True, help='保留文件名开头的排序前缀（如 Top001.），否则丢弃')
@click.option('--imdb-id', 'imdb_overrides', multiple=True,
              help='手动指定 IMDB ID：PREFIX=ttXXXX（可多次使用）；只有一部电影时可直接给 ttXXXX')
@click.option('--force-imdb', is_flag=True, help='OMDb 反查验证不通过时仍强制使用手动指定的 --imdb-id')
@click.option('--exclude', 'excludes', multiple=True, help='跳过指定前缀/组名，可多次使用')
@click.option('--no-cache', is_flag=True, help='忽略已有 omdb_cache.json 强制重查（OMDb 返回垃圾数据后用），结果仍会覆盖写入缓存')
@click.option('--yes', '-y', is_flag=True, help='跳过确认，直接执行')
@click.option('--dry-run', is_flag=True, help='只预览，不执行')
def rename_flat(directory, keep_prefix, imdb_overrides, force_imdb, excludes, no_cache, yes, dry_run):
    """平铺目录智能重命名：多部电影共用一个目录（无电影子文件夹）时使用。

    按文件名公共前缀（如 Top001.肖申克的救赎.The.Shawshank... 中的 Top001.）分组，
    组内含视频的视为一部电影，从视频文件名解析标题/年份并查询 OMDb，重命名为：
    {prefix}{中文} {English Title} (Year) [imdbid-ttXXXX].mkv

    组内图片保留 -poster/-backdrop/-landscape/-logo 后缀关键字只换 stem（无后缀的
    默认加 -poster），.nfo 用同名 stem，字幕（stem 与视频相同或仅多语言标签）跟随改名。
    """
    cfg = _find_config()
    base_url, api_key = _get_omdb_config(cfg)
    base = Path(directory).resolve()

    overrides: dict[str, str] = {}
    for item in imdb_overrides:
        if '=' in item:
            k, v = item.split('=', 1)
            overrides[_norm_key(k)] = v.strip()
        else:
            overrides['*'] = item.strip()
    excluded = {_norm_key(e) for e in excludes}

    cache = OmdbCache(base / 'omdb_cache.json')
    groups = _flat_groups(base)
    if not groups:
        click.echo("没有找到含视频的文件组。")
        return

    review: list[tuple[str, str | None, list[str]]] = []  # (label, 新视频名, warnings)
    plans: list[tuple[str, list[tuple[Path, Path]]]] = []
    failures: list[str] = []

    for g in groups:
        video = g['videos'][0]
        prefix = g['prefix']
        label = prefix or video.stem
        if _norm_key(label) in excluded:
            click.echo(f"跳过（--exclude）：{label}")
            continue
        if len(g['videos']) > 1:
            click.echo(f"警告：{label} 组内有 {len(g['videos'])} 个视频，使用 {video.name}")

        stem = video.stem
        if prefix and stem.startswith(prefix):
            stem = stem[len(prefix):]
        parsed = _parse_folder_name(stem)
        click.echo(f"\n{'='*60}\n处理：{label}")
        click.echo(
            f"解析：中文={parsed['cjk_title'] or '(无)'}  英文={parsed['eng_title'] or '(无)'}  "
            f"年份={parsed['year'] or '?'}"
        )

        warnings: list[str] = []
        ov = overrides.get(_norm_key(label)) or overrides.get('*')
        if ov:
            ok, msg, info = _check_imdb_override(ov, parsed['eng_title'], parsed['year'],
                                                 base_url, api_key,
                                                 None if no_cache else cache)
            if msg:
                click.echo(("  ⚠ " if not ok else "  提示：") + msg)
            if not ok:
                if force_imdb:
                    click.echo("  --force-imdb 已指定，强制使用该 ID")
                elif yes:
                    click.echo("  拒绝采用（--yes 模式）；确认无误请加 --force-imdb", err=True)
                    failures.append(label)
                    review.append((label, None, [msg]))
                    continue
                elif click.prompt("  仍要使用该 IMDB ID？[y/N]", default='N').lower() != 'y':
                    failures.append(label)
                    review.append((label, None, ['用户取消']))
                    continue
            imdb_id = ov
            official_title = (info or {}).get('title') or parsed['eng_title'] or parsed['cjk_title']
            official_year = (info or {}).get('year') or (str(parsed['year']) if parsed['year'] else '????')
            click.echo(f"使用 IMDB ID {imdb_id} → {official_title} ({official_year})")
        else:
            search_title = parsed['eng_title'] or parsed['cjk_title']
            if not search_title:
                click.echo("错误：无法解析标题，可用 --imdb-id 手动指定", err=True)
                failures.append(label)
                review.append((label, None, ['无法解析标题']))
                continue
            click.echo(f"查询 OMDb：「{search_title}」({parsed['year'] or '?'})...")
            result = _query_omdb_smart(search_title, parsed['year'], base_url, api_key, cache, no_cache)
            if not result['found']:
                click.echo("未找到可靠匹配，请用 --imdb-id 手动指定：", err=True)
                for i, c in enumerate(result['candidates'][:10], 1):
                    click.echo(f"  {i}. {c['title']} ({c['year']}) [{c['imdb_id']}]")
                failures.append(label)
                review.append((label, None, ['未找到匹配']))
                continue
            imdb_id = result['imdb_id']
            official_title = result['title']
            official_year = result['year']
            if result.get('warning'):
                warnings.append(result['warning'])
            click.echo(f"找到：{official_title} ({official_year}) [{imdb_id}]")

        # 终审警告：脏标题、年份与原名不符
        if DIRTY_TITLE.search(official_title or ''):
            warnings.append(f"标题疑似脏数据：{official_title}")
        py, oy = parsed['year'], _year_int(official_year)
        if py and oy and abs(oy - py) > 2:
            w = f"年份与原名不符：文件名 {py} vs OMDb {oy}"
            if w not in warnings:
                warnings.append(w)

        cjk = parsed['cjk_title']
        display = f"{cjk} {official_title}" if cjk else official_title
        prefix_out = prefix if keep_prefix else ''
        new_stem = re.sub(r'\s{2,}', ' ', _sanitize_filename(
            f"{prefix_out}{display} ({official_year}) [imdbid-{imdb_id}]")).strip()

        renames: list[tuple[Path, Path]] = []
        taken_img_suffixes: set[str] = set()
        nfo_done = False
        fanart_n = 0
        for f in sorted(g['files']):
            ext = f.suffix.lower()
            dst = None
            if f == video:
                dst = base / f"{new_stem}{f.suffix}"
            elif ext in VIDEO_EXTS:
                click.echo(f"  跳过（组内额外视频）：{f.name}")
            elif ext in IMAGE_EXTS:
                m = IMG_SUFFIX.search(f.stem)
                if m:
                    suffix = '-' + m.group(1).lower()
                elif '-poster' not in taken_img_suffixes:
                    suffix = '-poster'
                else:
                    suffix = ''
                while suffix and suffix in taken_img_suffixes or not suffix:
                    fanart_n += 1
                    suffix = f'-fanart{fanart_n}'
                taken_img_suffixes.add(suffix)
                dst = base / f"{new_stem}{suffix}{f.suffix}"
            elif ext == '.nfo':
                if nfo_done:
                    click.echo(f"  跳过（组内额外 .nfo）：{f.name}")
                else:
                    nfo_done = True
                    dst = base / f"{new_stem}.nfo"
            elif ext in SUBTITLE_EXTS:
                v_stem = video.stem
                if f.stem == v_stem or f.stem.startswith(v_stem + '.'):
                    dst = base / f"{new_stem}{f.stem[len(v_stem):]}{f.suffix}"
                elif prefix and len(g['videos']) == 1:
                    # 同前缀单视频组：字幕即使 stem 与视频不完全一致（少了质量标记等）
                    # 也跟随改名，保留尾部语言标签（.chn / .chs.eng 等）
                    tokens = f.stem.split('.')
                    lang = ''
                    while len(tokens) > 1 and re.fullmatch(r'[a-zA-Z]{2,4}', tokens[-1]):
                        lang = '.' + tokens.pop() + lang
                    dst = base / f"{new_stem}{lang}{f.suffix}"
                else:
                    click.echo(f"  跳过（无法匹配视频的字幕）：{f.name}")
            if dst is not None and dst.name != f.name:
                renames.append((f, dst))

        # 与磁盘上已有文件（且不在本组计划内）的冲突检查
        srcs = {s for s, _ in renames}
        renames = [(s, d) for s, d in renames if not (
            d.exists() and d not in srcs and
            not warnings.append(f"目标已存在，跳过：{d.name}"))]

        new_video_name = f"{new_stem}{video.suffix}"
        click.echo(f"  视频：{video.name}\n     → {new_video_name}")
        for s, d in renames:
            if s != video:
                click.echo(f"  跟随：{s.name}\n     → {d.name}")
        for w in warnings:
            click.echo(f"  ⚠ {w}")
        review.append((label, new_video_name, warnings))
        plans.append((label, renames))

    # 终审清单：dry-run 和执行后都输出
    click.echo(f"\n{'='*60}\n终审清单（{len(review)} 部）：\n{'='*60}")
    for label, new_name, warns in review:
        if new_name is None:
            click.echo(f"  ✗ {label}（{'; '.join(warns)}）")
        elif warns:
            click.echo(f"  ⚠ {label} → {new_name}")
            for w in warns:
                click.echo(f"      ⚠ {w}")
        else:
            click.echo(f"  ✓ {label} → {new_name}")

    if dry_run:
        click.echo("\n[预览模式，未执行]")
        if failures:
            sys.exit(1)
        return
    if not plans:
        if failures:
            click.echo(f"\n{len(failures)} 部未处理，见上方终审清单。")
            sys.exit(1)
        return
    if not yes:
        if click.prompt("\n确认执行以上重命名？[y/N]", default='N').lower() != 'y':
            click.echo("已取消。")
            return
    for _, renames in plans:
        for src, dst in renames:
            if dst.exists() and dst != src:
                click.echo(f"  冲突跳过：{dst.name} 已存在", err=True)
                continue
            src.rename(dst)
    click.echo(f"\n重命名完成！共 {len(plans)} 部电影。")
    if failures:
        click.echo(f"以下 {len(failures)} 部需要手动处理：")
        for n in failures:
            click.echo(f"  - {n}")
        click.echo("使用 --imdb-id PREFIX=ttXXXX 手动指定后重跑（已成功的会命中缓存，不重复请求）。")
        sys.exit(1)


# ---------------------------------------------------------------------------
# verify：批量校验文件名中的 [imdbid-ttXXX] 与 OMDb 反查是否匹配
# ---------------------------------------------------------------------------


def _parse_renamed_name(stem: str) -> dict:
    """解析本工具输出规范的名字：{prefix}{中文} {English} (Year) [imdbid-ttXXX]。
    也容忍缺 prefix / 缺年份 / 缺 imdbid 的写法。"""
    m = IMDB_TAG.search(stem)
    imdb_id = m.group(1) if m else None
    s = IMDB_TAG.sub('', stem).strip()
    year = None
    ym = re.search(r'\((\d{4})\)\s*$', s)
    if ym:
        year = int(ym.group(1))
        s = s[:ym.start()].strip()
    pm = RENAMED_PREFIX.match(s)
    prefix = pm.group(1) if pm else ''
    if prefix:
        s = s[len(prefix):].strip()
    cm = re.match(r'^([一-鿿㐀-䶿][一-鿿㐀-䶿\d]*)\s+(.*)$', s)
    if cm:
        cjk, eng = cm.group(1), cm.group(2).strip()
    elif re.search(r'[一-鿿㐀-䶿]', s):
        cjk, eng = s, ''
    else:
        cjk, eng = '', s
    return {'imdb_id': imdb_id, 'year': year, 'prefix': prefix,
            'cjk_title': cjk, 'eng_title': eng}


@cli.command()
@click.argument('directory', default='.', type=click.Path(exists=True, file_okay=False))
@click.option('--clean-orphans', is_flag=True, help='把孤儿媒体文件移动到 <目录>_oldart_backup/')
@click.option('--yes', '-y', is_flag=True, help='--clean-orphans 时跳过确认')
def verify(directory, clean_orphans, yes):
    """批量校验：文件名中的 [imdbid-ttXXX] 与 OMDb 反查标题是否匹配。

    扫描目录下的视频文件和已规范命名的子文件夹，逐个用 OMDb i= 反查，
    标题相似度低或年份偏差 >2 的列入可疑清单。结果写入 omdb_cache.json 缓存。
    同时检测孤儿媒体文件（nfo/图片的 stem 匹配不到任何视频），
    --clean-orphans 把它们移动到 <目录>_oldart_backup/。
    """
    cfg = _find_config()
    base_url, api_key = _get_omdb_config(cfg)
    base = Path(directory).resolve()
    cache = OmdbCache(base / 'omdb_cache.json')

    entries: list[tuple[str, Path]] = []
    for f in sorted(base.iterdir()):
        if f.is_dir():
            if IMDB_TAG.search(f.name):
                entries.append((f.name, f))
        elif f.suffix.lower() in VIDEO_EXTS and IMDB_TAG.search(f.stem):
            entries.append((f.stem, f))
    if not entries:
        click.echo("没有找到带 [imdbid-ttXXX] 的文件/文件夹。")
        return

    ok_count = 0
    suspicious: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    for name, _ in entries:
        p = _parse_renamed_name(name)
        ok, msg, info = _check_imdb_override(p['imdb_id'], p['eng_title'], p['year'],
                                             base_url, api_key, cache)
        if info is None:
            skipped.append((name, msg))
            click.echo(f"? {name}\n    {msg}")
        elif not ok:
            suspicious.append((name, msg))
            click.echo(f"⚠ {name}\n    {msg}")
        else:
            ok_count += 1

    click.echo(f"\n{'='*60}\n校验结果：✓ {ok_count} 正常，⚠ {len(suspicious)} 可疑，? {len(skipped)} 无法验证")
    if suspicious:
        click.echo("\n可疑清单（文件名 imdbid 与 OMDb 反查不符）：")
        for name, msg in suspicious:
            click.echo(f"  - {name}\n      {msg}")
        click.echo("\n确认绑定错误后的修复顺序：先用 retag 修正文件名里的 imdbid，再执行识别：")
        click.echo('  jellyfin_tool.py retag "<文件或目录>" tt旧ID=tt新ID')
        click.echo('  jellyfin_tool.py server identify "<文件或目录>"   # 从文件名读取正确 imdbid 重新识别')

    # 孤儿媒体文件检测：nfo/图片的 stem 匹配不到目录下任何视频
    # （典型来源：错误绑定时残留的旧 imdbid nfo）
    video_stems = [f.stem for f in base.iterdir()
                   if f.is_file() and f.suffix.lower() in VIDEO_EXTS]
    orphans: list[Path] = []
    for f in sorted(base.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext != '.nfo' and ext not in IMAGE_EXTS:
            continue
        stem = IMG_SUFFIX.sub('', f.stem) if ext in IMAGE_EXTS else f.stem
        if not any(stem == v or stem.startswith(v + '.') or stem.startswith(v + '-')
                   for v in video_stems):
            orphans.append(f)
    if orphans:
        click.echo(f"\n🗑 孤儿媒体文件（stem 匹配不到任何视频，可能是旧错误命名的残留）：{len(orphans)} 个")
        for f in orphans:
            click.echo(f"  {f.name}")
        if clean_orphans:
            backup = base.parent / f"{base.name}_oldart_backup"
            if not yes:
                if click.prompt(f"\n确认把这 {len(orphans)} 个文件移动到 {backup}/？[y/N]",
                                default='N').lower() != 'y':
                    click.echo("已取消。")
                    return
            backup.mkdir(parents=True, exist_ok=True)
            moved = 0
            for f in orphans:
                dst = backup / f.name
                if dst.exists():
                    click.echo(f"  冲突跳过：{dst.name} 已存在于备份目录", err=True)
                    continue
                shutil.move(str(f), str(dst))
                moved += 1
            click.echo(f"已移动 {moved} 个孤儿文件到 {backup}/")
        else:
            click.echo("  加 --clean-orphans 移动到 <目录>_oldart_backup/（de-localart 会移走"
                       "全部 nfo/图片，不适合只清孤儿）。")

    if suspicious:
        sys.exit(1)


# ---------------------------------------------------------------------------
# retag：只替换文件名中的 [imdbid-ttXXX] 标签
# ---------------------------------------------------------------------------


@cli.command()
@click.argument('path', type=click.Path(exists=True))
@click.argument('mappings', nargs=-1, required=True)
@click.option('--force-imdb', is_flag=True, help='OMDb 反查验证不通过时仍强制使用新 ID')
@click.option('--yes', '-y', is_flag=True, help='跳过确认，直接执行')
@click.option('--dry-run', is_flag=True, help='只预览，不执行')
def retag(path, mappings, force_imdb, yes, dry_run):
    """只替换文件名中的 [imdbid-ttXXX] 标签，其余部分原样保留。

    \b
    目录模式：retag <目录> ttOLD=ttNEW [ttOLD=ttNEW ...]
      目录下所有带旧标签的文件（视频+图片+nfo+字幕）一起改。
    单文件模式：retag <视频文件> ttNEW
      旧 id 从文件名读取，带同一旧标签的旁挂文件一起改。

    新 id 会先经 OMDb 反查验证（与文件名解析出的标题比对，不符则拒绝，
    确认无误加 --force-imdb 强制）。
    """
    p = Path(path).resolve()
    pairs: list[tuple[str, str]] = []
    if p.is_file():
        if len(mappings) != 1:
            click.echo("错误：单文件模式只接受一个映射", err=True)
            sys.exit(1)
        m = IMDB_TAG.search(p.name)
        if not m:
            click.echo(f"错误：{p.name} 中没有 [imdbid-ttXXX] 标签", err=True)
            sys.exit(1)
        pairs.append((m.group(1), mappings[0].split('=', 1)[-1].strip()))
        scope = p.parent
    else:
        scope = p
        for m in mappings:
            if '=' not in m:
                click.echo(f"错误：目录模式的映射必须是 ttOLD=ttNEW 形式：{m}", err=True)
                sys.exit(1)
            old, new = m.split('=', 1)
            pairs.append((old.strip(), new.strip()))

    cfg = _find_config()
    base_url, api_key = _get_omdb_config(cfg)
    cache = OmdbCache(scope / 'omdb_cache.json')

    renames: list[tuple[Path, Path]] = []
    rejected = 0
    for old, new in pairs:
        if not re.fullmatch(r'tt\d+', new):
            click.echo(f"错误：新 IMDB ID 格式不正确：{new}", err=True)
            sys.exit(1)
        tag_re = re.compile(r'\[imdbid-' + re.escape(old) + r'\]', re.IGNORECASE)
        hits = [f for f in sorted(scope.iterdir()) if f.is_file() and tag_re.search(f.name)]
        if not hits:
            click.echo(f"⚠ 没有文件名包含 [imdbid-{old}]", err=True)
            continue
        # 用该组的视频文件名解析标题，反查验证新 id（血的教训：手填 id 极易记错）
        rep = next((f for f in hits if f.suffix.lower() in VIDEO_EXTS), hits[0])
        parsed = _parse_renamed_name(tag_re.sub('', rep.stem).strip())
        ok, msg, _ = _check_imdb_override(new, parsed['eng_title'], parsed['year'],
                                          base_url, api_key, cache)
        if msg:
            click.echo(("⚠ " if not ok else "提示：") + msg)
        if not ok:
            if force_imdb:
                click.echo("--force-imdb 已指定，强制使用该 ID")
            elif yes:
                click.echo("错误：--yes 模式下拒绝采用可疑 IMDB ID；确认无误请加 --force-imdb", err=True)
                rejected += 1
                continue
            elif click.prompt("仍要使用该 IMDB ID？[y/N]", default='N').lower() != 'y':
                click.echo("已跳过。")
                rejected += 1
                continue
        for f in hits:
            renames.append((f, f.with_name(tag_re.sub(f'[imdbid-{new}]', f.name))))

    if not renames:
        if rejected:
            click.echo("没有需要修改的文件（有映射被验证拒绝）。", err=True)
            sys.exit(1)
        click.echo("没有需要修改的文件。")
        return
    click.echo(f"\n重命名计划（{len(renames)} 个文件）：")
    for s, d in renames:
        click.echo(f"  {s.name}\n→ {d.name}")
    if dry_run:
        click.echo("\n[预览模式，未执行]")
        if rejected:
            sys.exit(1)
        return
    if not yes:
        if click.prompt("\n确认执行？[y/N]", default='N').lower() != 'y':
            click.echo("已取消。")
            return
    srcs = {s for s, _ in renames}
    failed = 0
    for s, d in renames:
        if d.exists() and d not in srcs:
            click.echo(f"  冲突跳过：{d.name} 已存在", err=True)
            failed += 1
            continue
        s.rename(d)
    click.echo(f"完成：{len(renames) - failed} 个文件已 retag。")
    if failed or rejected:
        if rejected:
            click.echo(f"⚠ 另有 {rejected} 个映射被验证拒绝，未执行。", err=True)
        sys.exit(1)



if __name__ == '__main__':
    cli()
