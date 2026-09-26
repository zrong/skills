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
CD_MARKER = re.compile(r'(?:^|[-._\s\[\]一-鿿㐀-䶿])(cd|disc|dvd|part|pt)\s*(\d{1,2})(?=[-._\s\[\]]|$)', re.IGNORECASE)

# BT 组喜欢在标题后塞中文质量词，CJK 提取时会粘进标题（「真实的谎言.BD中英双字1024高清」
# → 中文名变成「真实的谎言中英双字高清」），搜索前必须剥掉
CJK_NOISE = ['中英双字', '国英双语', '国粤双语', '国语中字', '中英字幕', '中文字幕', '导演剪辑版',
             '终极剪辑版', '未删减版', '无删减版', '修复版', '完整版', '加长版', '终极版', '收藏版',
             '纪念版', '重制版', '特别版', '抢先版', '清晰版', '纯净版', '剪辑版', '中西双字',
             '精译版', '无水印', '国语四川话', '国英台粤', '英国粤台', '国粤日', '国粤', '国英',
             '佛兰芒语', '西班牙', '葡萄牙', '意大利', '俄罗斯', '波兰语', '印度语', '无删减',
             '未删减', '中文', '国语', '粤语', '英语', '日语', '韩语', '泰语', '法语', '德语',
             '双字', '双语', '三语', '四语', '中字', '字幕', '中英', '配音', '特效', '超清',
             '高清', '标清', '蓝光', '熟肉', '生肉', '台版', '英版', '美版', '韩版', '日版',
             '港版', '修正']


def _strip_cjk_noise(s: str) -> str:
    """反复剥掉 CJK 标题里的质量/版本/语言噪声词（长的先剥，剥到不动为止）。"""
    orig = s
    prev = None
    while prev != s:
        prev = s
        for w in CJK_NOISE:
            s = s.replace(w, '')
    if s != orig and s.endswith('版'):
        s = s[:-1]  # 「中英双字版」剥完剩下的孤零零的「版」
    return s


# 英文侧的发布质量噪声：分辨率/编码/来源/语言标签等。整体可拼接匹配
# （「BD1024」= BD+1024、「HD1280」= HD+1280 也视为噪声）。
# 注意：纯数字仅 4 位以上或带 p/bit/k 后缀才算噪声，避免误杀「300」「9」这类片名
ENG_NOISE = re.compile(
    r'(?:\d{3,4}x\d{3,4}|\d{4,}(?:p|bit|k)?|\d+(?:p|bit|k)|\d+\.\d+|'
    r'bd|hd|dvd|web|web-?dl|hdrip|bdrip|dvdrip|webrip|hdtv|blu-?ray|remux|'
    r'xvid|divx|x26[45]|h\.?26[45]|avc|hevc|aac|ac3|eac3|mp3|dts|flac|'
    r'3d|imax|extended|proper|repack|r5|cam|tc|ts|mp4|mkv|avi|scr|screener|ppv|hdcam|'
    r'chs|cht|chn|zho|eng|jpn|kor|subs?)*', re.IGNORECASE)


def _is_eng_noise(token: str) -> bool:
    return bool(token) and bool(ENG_NOISE.fullmatch(token))


def _cd_marker(stem: str) -> str | None:
    """文件名中的碟片标记（cd1/disc2/part3，可在任意 token 位置），返回规范化小写如 'cd1'。"""
    m = CD_MARKER.search(stem)
    return f"{m.group(1).lower()}{m.group(2)}" if m else None


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
        # OMDb 不可用时的回退反查（标题为 TMDb 中文名或提供商英文名）
        info = _lookup_via_jellyfin_imdb(imdb_id)
    if not info:
        return True, f"无法反查 {imdb_id}（未配置 OMDb key 或网络失败），跳过验证", None
    problems = []
    if eng_title and not _titles_match(eng_title, info['title']):
        problems.append(f"标题不符：文件「{eng_title}」vs 反查「{info['title']} ({info.get('year')})」")
    oy = _year_int(info.get('year'))
    if year and oy and abs(oy - year) > 2:
        problems.append(f"年份不符：文件 {year} vs 反查 {oy}")
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
    # OMDb 挂掉时的 not-found 不写缓存（避免把「今天配额没了」缓存成永久未找到）
    if not _OMDB_DOWN:
        cache.set(cache_key, result)
    return result


def _query_omdb_smart_uncached(title: str, year: int | None, base_url: str, api_key: str) -> dict:
    not_found = {'found': False, 'imdb_id': '', 'title': '', 'year': '', 'warning': None,
                 'searched': title, 'candidates': []}
    if not api_key:
        _omdb_unavailable("未配置 OMDb API Key（agent_config.toml [jellyfin.omdb] api_key）")
        return not_found
    if _OMDB_DOWN:
        return {**not_found, 'searched': title}

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

    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        _omdb_unavailable(f"OMDb 不可用（{e}）")
        return {'found': False, 'imdb_id': '', 'title': '', 'year': '', 'warning': None,
                'searched': title, 'candidates': []}


def _parse_folder_name(name: str) -> dict:
    """解析 BT/字幕组风格的文件夹名，提取标题、年份、媒体类型和集数信息。"""
    # 已有的元数据标签直接复用（含 imdbi-/imdb- 拼写变体）
    m_imdb = re.search(r'\[(?:imdbid|imdbi|imdb)-(tt\d+)\]', name, re.IGNORECASE)
    m_tmdb = re.search(r'\[tmdbid-(\d+)\]', name, re.IGNORECASE)
    existing_imdb = m_imdb.group(1) if m_imdb else None
    existing_tmdb = m_tmdb.group(1) if m_tmdb else None
    # […] 标签、国家/地区标记（美）(韩)、重复副本标记「复制(1)」都不是标题，剥掉
    name = re.sub(r'\[[^\]]*\]', ' ', name.strip())
    name = re.sub(r'[（(](?:美|韩|日|港|台|法|国|英|泰|俄|印|意|德|西)[）)]', ' ', name)
    name = re.sub(r'[-_]?复制\s*[（(]\d+[）)]\s*$', '', name)
    tokens = re.split(r'[._\s]+', name)

    year = None
    year_idx = None
    for i, t in enumerate(tokens):
        if YEAR_PATTERN.match(t):
            year = int(t)
            year_idx = i
            break
    if year is None:
        # 括号年份：「阿波罗11号 Apollo 11 (2019)」
        m = re.search(r'\((\d{4})\)', name)
        if m and YEAR_PATTERN.match(m.group(1)):
            year = int(m.group(1))
            tokens = [re.sub(r'\(\d{4}\)', ' ', t) for t in tokens]

    title_tokens = tokens[:year_idx] if year_idx is not None else tokens
    after_tokens = tokens[year_idx + 1:] if year_idx is not None else []

    cjk_parts = []
    eng_parts = []
    for t in title_tokens:
        if not t.strip():
            continue
        m = re.match(r'^(.*?[一-鿿㐀-䶿A-Za-z])[-–—]((?:19|20)\d{2})$', t)
        if m:
            # 尾部粘连年份：「X特遣队：全员集结-2021」「黑豹2-2022」
            if year is None:
                year = int(m.group(2))
            t = m.group(1)
        m = re.match(r'^((?:19|20)\d{2})([一-鿿㐀-䶿].*)$', t)
        if m:
            # 粘连年份前缀：「2013终极神差」→ 年份 2013 + 中文名 终极神差
            if year is None:
                year = int(m.group(1))
            c = _strip_cjk_noise(m.group(2))
            if c:
                cjk_parts.append(c)
            continue
        if re.search(r'[一-鿿㐀-䶿]', t) and not re.search(r'[A-Za-z]', t):
            # 纯 CJK+数字 token，整个属于中文片名：「教父2」「阿波罗11号」「12只猴子」
            c = _strip_cjk_noise(t)
            if c:
                cjk_parts.append(c)
            continue
        raw_cjk = re.sub(r'[^一-鿿㐀-䶿：·，。！？（）、—…「」『』]', '', t)
        cjk = _strip_cjk_noise(raw_cjk)
        eng = re.sub(r'^[^0-9A-Za-z]+|[^0-9A-Za-z]+$', '',
                     re.sub(r'[一-鿿㐀-䶿]', '', t)).strip()
        if cjk and eng and (len(eng) <= 2 or (len(eng) == 3 and re.search(r'\d', eng))):
            # 混合短尾/短头 token：「食人鱼3D」「3D豪情」「X特遣队」—— eng 部分是片名
            # 而非质量标记，按原始顺序合并（前缀就前置，后缀就后置）
            cjk = (eng + cjk) if re.match(r'^[0-9A-Za-z]', t) else (cjk + eng)
            eng = ''
        if _is_eng_noise(eng):
            eng = ''
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
        'imdb_id': existing_imdb,
        'tmdb_id': existing_tmdb,
    }


_OMDB_DOWN = False


def _omdb_unavailable(reason: str) -> None:
    """OMDb 挂掉（401 配额/Key 失效、网络不通）时置标志：本批次后续查询直接跳过，
    由调用方降级到 Jellyfin RemoteSearch，而不是整批退出。"""
    global _OMDB_DOWN
    if not _OMDB_DOWN:
        click.echo(f"警告：{reason}；本批次后续 OMDb 查询跳过，自动降级 Jellyfin RemoteSearch", err=True)
    _OMDB_DOWN = True


def _query_omdb(title: str, year: int | None, media_type: str, base_url: str, api_key: str) -> dict:
    """查询 OMDb API，返回 {found, imdb_id, title, year, candidates}。"""
    not_found = {'found': False, 'imdb_id': '', 'title': '', 'year': '', 'candidates': []}
    if not api_key:
        _omdb_unavailable("未配置 OMDb API Key（agent_config.toml [jellyfin.omdb] api_key）")
        return not_found
    if _OMDB_DOWN:
        return not_found

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

    except (httpx.ConnectError, httpx.HTTPStatusError) as e:
        _omdb_unavailable(f"OMDb 不可用（{e}）")
        return {'found': False, 'imdb_id': '', 'title': '', 'year': '', 'candidates': []}


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


def _query_via_jellyfin_search(title: str, year: int | None) -> dict | None:
    """OMDb 查不到时的回退：借 Jellyfin 服务器的 RemoteSearch 搜索
    （TMDb 等提供商支持中文标题）。返回 {'imdb_id','title','year'} 或 None。
    未配置 [jellyfin] base_url/api_key 或搜索失败时返回 None。

    分数相同时偏好年份较新的候选（同名翻拍通常要的是新版）；
    目录名里的年份只是整理归类，与电影发布年份无关，不参与过滤。"""
    cfg = _find_config()
    base_url, api_key = _get_server_config(cfg)
    if not base_url or not api_key:
        return None
    search_info: dict = {'Name': title}
    # 不把年份放进请求：服务端按年过滤太死，文件名年份又常是下载/封装年。
    # 年份只在我的评分里做偏好，搜不到再放宽
    try:
        with httpx.Client(base_url=base_url, timeout=30.0, headers={
            "Authorization": (
                f'MediaBrowser Token="{api_key}", Client="jellyfin-tool", '
                'Device="jellyfin-tool", DeviceId="jellyfin-tool", Version="1.0"')
        }) as client:
            r = client.post('/Items/RemoteSearch/Movie', json={'SearchInfo': search_info})
            if r.status_code >= 400:
                return None
            results = r.json()
            if not results:
                # 服务器偶发返回空（并发/抖动），停 1s 重试一次
                import time
                time.sleep(1)
                r = client.post('/Items/RemoteSearch/Movie', json={'SearchInfo': search_info})
                if r.status_code >= 400:
                    return None
                results = r.json()

            tn0 = _norm_cmp(title)

            def pick(year_filter: bool):
                best = None
                best_score = -1.0
                for x in results:
                    name = x.get('Name') or ''
                    xy = x.get('ProductionYear')
                    diff = abs(xy - year) if (xy and year and year_filter) else None
                    if diff is not None and diff > 2:
                        continue
                    nn = _norm_cmp(name)
                    score = _similarity(tn0, nn)
                    if tn0 and nn and (tn0 in nn or nn in tn0):
                        score += 0.5  # 中文名互相包含（如「未来水世界」命中「未来水世界」）
                    if diff is not None:
                        score += (2 - diff) * 0.05
                    if score > best_score + 1e-9 or (
                            abs(score - best_score) <= 1e-9 and best is not None
                            and xy and (best.get('ProductionYear') or 0) < xy):
                        best_score, best = score, x
                return best

            best = pick(year_filter=True)
            if best is None and year:
                # 文件名里的年份可能是下载/封装年而非发行年（2013终极神差 → 实为 1997），
                # 放宽年份过滤重试；可信度由调用方的名称比对把关
                best = pick(year_filter=False)
            if best is None:
                return None
            ids = best.get('ProviderIds') or {}
            imdb = ids.get('Imdb')
            if not imdb and ids.get('Tmdb'):
                # 搜索结果常只带 TMDb id；带 Tmdb id 再查一次，提供商会补全 Imdb
                r2 = client.post('/Items/RemoteSearch/Movie',
                                 json={'SearchInfo': {'ProviderIds': {'Tmdb': ids['Tmdb']}}})
                if r2.status_code < 400:
                    for x2 in r2.json():
                        imdb = (x2.get('ProviderIds') or {}).get('Imdb')
                        if imdb:
                            break
            if not imdb:
                return None
            return {'imdb_id': imdb, 'title': best.get('Name') or '',
                    'year': str(best.get('ProductionYear') or '')}
    except Exception:
        return None


_CN_DIGITS = dict(zip('一二三四五六七八九', '123456789'))


def _norm_cmp(s: str) -> str:
    """比较用规范化：中文数字转阿拉伯（十二猴子 vs 12只猴子），去掉所有标点/空白/间隔号
    （「本杰明巴顿奇事」vs「本杰明·巴顿奇事」）。"""
    s = (s or '').lower()
    s = re.sub(r'([一二三四五六七八九])十([一二三四五六七八九]?)',
               lambda m: _CN_DIGITS[m.group(1)] + (_CN_DIGITS.get(m.group(2)) or '0'), s)
    s = re.sub(r'十([一二三四五六七八九])', lambda m: '1' + _CN_DIGITS[m.group(1)], s)
    s = re.sub(r'[一二三四五六七八九]', lambda m: _CN_DIGITS[m.group(0)], s)
    return re.sub(r'[^0-9a-z一-鿿㐀-䶿]+', '', s)


def _lookup_via_jellyfin_imdb(imdb_id: str) -> dict | None:
    """OMDb 不可用时的反查回退：用 Jellyfin RemoteSearch 按 IMDb id 取标题和年份。
    优先返回带 Imdb 标记的提供商结果（标题为英文原名）。"""
    cfg = _find_config()
    base_url, api_key = _get_server_config(cfg)
    if not base_url or not api_key:
        return None
    try:
        with httpx.Client(base_url=base_url, timeout=30.0, headers={
            "Authorization": (
                f'MediaBrowser Token="{api_key}", Client="jellyfin-tool", '
                'Device="jellyfin-tool", DeviceId="jellyfin-tool", Version="1.0"')
        }) as client:
            r = client.post('/Items/RemoteSearch/Movie',
                            json={'SearchInfo': {'ProviderIds': {'Imdb': imdb_id}}})
            if r.status_code >= 400:
                return None
            results = r.json()
            for x in results:
                if (x.get('ProviderIds') or {}).get('Imdb'):
                    return {'title': x.get('Name') or '', 'year': str(x.get('ProductionYear') or '')}
            if results:
                return {'title': results[0].get('Name') or '',
                        'year': str(results[0].get('ProductionYear') or '')}
    except Exception:
        pass
    return None


def _tmdb_to_imdb(tmdb_id: str) -> str | None:
    """用 Jellyfin RemoteSearch 把文件名自带的 [tmdbid-…] 补全成 IMDb id。"""
    cfg = _find_config()
    base_url, api_key = _get_server_config(cfg)
    if not base_url or not api_key:
        return None
    try:
        with httpx.Client(base_url=base_url, timeout=30.0, headers={
            "Authorization": (
                f'MediaBrowser Token="{api_key}", Client="jellyfin-tool", '
                'Device="jellyfin-tool", DeviceId="jellyfin-tool", Version="1.0"')
        }) as client:
            r = client.post('/Items/RemoteSearch/Movie',
                            json={'SearchInfo': {'ProviderIds': {'Tmdb': tmdb_id}}})
            if r.status_code < 400:
                for x in r.json():
                    imdb = (x.get('ProviderIds') or {}).get('Imdb')
                    if imdb:
                        return imdb
    except Exception:
        pass
    return None


def _server_hit_trustworthy(alt: dict, parsed: dict, year: int | None) -> bool:
    """RemoteSearch 命中是否可信：中/英标题规范化后互相包含或高度相似，或年份相差 ≤1。
    两者都不沾边的命中视为不可信（防止 TMDb 返回同名异物）。"""
    cn, an = _norm_cmp(parsed['cjk_title']), _norm_cmp(alt['title'])
    name_hit = bool(
        (cn and an and (cn in an or an in cn or _similarity(cn, an) >= 0.7))
        or _titles_match(parsed['eng_title'], alt['title']))
    ay = _year_int(alt.get('year'))
    yr_hit = bool(year and ay and abs(ay - year) <= 1)
    return name_hit or yr_hit


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
        skipped_tagged = [d.name for d in subdirs if IMDB_TAG.search(d.name)]
        subdirs = [d for d in subdirs if not IMDB_TAG.search(d.name)]
        if skipped_tagged:
            click.echo(f"跳过 {len(skipped_tagged)} 个已带 [imdbid-] 的子目录（无需重命名）。")
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
    search_title = title_override or parsed['eng_title'] or parsed['cjk_title']
    year = year_override or parsed['year']
    if year is None:
        # 文件夹名无年份时，尝试从内部视频文件名解析（如「三个白痴/…2009…cd1.avi」）
        for vf in sorted(folder.iterdir()):
            if vf.is_file() and vf.suffix.lower() in VIDEO_EXTS:
                year = _parse_folder_name(vf.stem)['year']
                if year:
                    break
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
    # 文件名自带标签直接复用：[imdbid-]/[imdb-] 变体直接用，[tmdbid-] 经服务器补全
    builtin = parsed.get('imdb_id')
    if not builtin and parsed.get('tmdb_id'):
        builtin = _tmdb_to_imdb(parsed['tmdb_id'])
        if builtin:
            click.echo(f"文件名 [tmdbid-{parsed['tmdb_id']}] → 补全 IMDb {builtin}")
    imdb_id_override = imdb_id_override or builtin
    if imdb_id_override:
        ok, msg, info = _check_imdb_override(imdb_id_override, parsed['eng_title'], year,
                                             base_url, api_key)
        if msg:
            click.echo(("⚠ " if not ok else "提示：") + msg)
        if not ok:
            if force_imdb:
                click.echo("--force-imdb 已指定，强制使用该 ID")
            elif dry_run:
                # 预览模式不拦截：标注出来让用户在终审时核对
                click.echo(f"⚠ 覆盖 ID 待核对：{msg}")
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
        result = {'found': False, 'candidates': []}
        if parsed['eng_title'] or title_override:
            click.echo(f"查询 OMDb：「{search_title}」({year}, {actual_type})...")
            result = _query_omdb(search_title, year, actual_type, base_url, api_key)
            cleaned = _clean_search_title(search_title)
            if not result['found'] and cleaned != search_title:
                # 原标题优先；带续集序号/剪辑版标记的标题（2 The Godfather Part II）
                # 再试清洗后的标题（但不能反过来，12 Rounds 的 12 是片名）
                result = _query_omdb(cleaned, year, actual_type, base_url, api_key)
        else:
            click.echo(f"纯中文名，直接查 RemoteSearch：「{search_title}」({year or '?'}, {actual_type})...")
        if not result['found'] and actual_type == 'movie':
            # 回退：借 Jellyfin RemoteSearch（TMDb 等提供商支持中文标题）
            alt = _query_via_jellyfin_search(search_title, year)
            if alt and not _server_hit_trustworthy(alt, parsed, year):
                click.echo(f"⚠ RemoteSearch 命中「{alt['title']}」({alt['year']}) 与文件名标题/年份均不吻合，不可信")
                alt = None
            if alt:
                ok, msg, info = _check_imdb_override(alt['imdb_id'], parsed['eng_title'],
                                                     year, base_url, api_key)
                cn, an = _norm_cmp(parsed['cjk_title']), _norm_cmp(alt['title'])
                if not (cn and an and cn == an):
                    click.echo(f"⚠ 经 RemoteSearch 命中「{alt['title']}」({alt['year']}) [{alt['imdb_id']}]，中文名不完全一致，请人工核对")
                if msg and not ok:
                    click.echo(f"⚠ {msg}")
                result = {'found': True, 'imdb_id': alt['imdb_id'],
                          'title': (info or {}).get('title') or alt['title'],
                          'year': (info or {}).get('year') or alt['year'],
                          'candidates': []}
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
    videos = [f for f in sorted(folder.iterdir())
              if f.is_file() and f.suffix.lower() in VIDEO_EXTS]
    multi_video = len(videos) > 1
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
            elif multi_video:
                # 多 CD/多碟电影：带碟片标记的加 " - cdN" 后缀（Jellyfin 堆叠识别）；
                # 无标记的（sample/花絮/不同版本）跳过，避免同名互相覆盖
                marker = _cd_marker(f.stem)
                if not marker:
                    click.echo(f"  跳过（多视频目录中无碟片标记）：{f.name}")
                    continue
                new_name = f"{new_folder_name} - {marker}{f.suffix}"
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
            key_stem = IMG_SUFFIX.sub('', f.stem)
            # 尾部碟片标记不参与分组：大鱼cd1 / 大鱼cd2 是同一部电影
            key_stem = re.sub(r'(?:cd|disc|dvd|part|pt)\s*\d{1,2}$', '', key_stem,
                              flags=re.IGNORECASE).rstrip('._- ')
            key = 's:' + key_stem.lower()
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
        multi_video = len(g['videos']) > 1
        if _norm_key(label) in excluded:
            click.echo(f"跳过（--exclude）：{label}")
            continue
        if IMDB_TAG.search(video.name):
            click.echo(f"跳过（已带 imdbid）：{label}")
            continue
        if multi_video:
            click.echo(f"提示：{label} 组内有 {len(g['videos'])} 个视频，按碟片标记加 - cdN 后缀")

        stem = video.stem
        if prefix and stem.startswith(prefix):
            stem = stem[len(prefix):]
        if multi_video:
            # 多碟组：解析标题前去掉尾部碟片标记（大鱼cd1 → 大鱼）
            stem = re.sub(r'(?:cd|disc|dvd|part|pt)\s*\d{1,2}$', '', stem,
                          flags=re.IGNORECASE).rstrip('._- ')
        parsed = _parse_folder_name(stem)
        click.echo(f"\n{'='*60}\n处理：{label}")
        click.echo(
            f"解析：中文={parsed['cjk_title'] or '(无)'}  英文={parsed['eng_title'] or '(无)'}  "
            f"年份={parsed['year'] or '?'}"
        )

        warnings: list[str] = []
        # 文件名自带标签直接复用：[imdbid-]/[imdb-] 变体直接用，[tmdbid-] 经服务器补全
        builtin_imdb = parsed.get('imdb_id')
        if not builtin_imdb and parsed.get('tmdb_id'):
            builtin_imdb = _tmdb_to_imdb(parsed['tmdb_id'])
            if builtin_imdb:
                click.echo(f"  文件名 [tmdbid-{parsed['tmdb_id']}] → 补全 IMDb {builtin_imdb}")
        ov = overrides.get(_norm_key(label)) or overrides.get('*') or builtin_imdb
        if ov:
            ok, msg, info = _check_imdb_override(ov, parsed['eng_title'], parsed['year'],
                                                 base_url, api_key,
                                                 None if no_cache else cache)
            if msg:
                click.echo(("  ⚠ " if not ok else "  提示：") + msg)
            if not ok:
                if force_imdb:
                    click.echo("  --force-imdb 已指定，强制使用该 ID")
                elif dry_run:
                    # 预览模式不拦截：标注出来让用户在终审清单里核对
                    warnings.append(f"覆盖 ID 待核对：{msg}")
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
            result = {'found': False, 'candidates': []}
            if parsed['eng_title']:
                # 有英文标题走 OMDb；纯中文名 OMDb 搜不动，直接用 RemoteSearch
                click.echo(f"查询 OMDb：「{search_title}」({parsed['year'] or '?'})...")
                result = _query_omdb_smart(search_title, parsed['year'], base_url, api_key, cache, no_cache)
            if not result['found']:
                # 回退：借 Jellyfin RemoteSearch（TMDb 等提供商支持中文标题）
                alt = _query_via_jellyfin_search(search_title, parsed['year'])
                if alt and not _server_hit_trustworthy(alt, parsed, parsed['year']):
                    click.echo(f"  ⚠ RemoteSearch 命中「{alt['title']}」({alt['year']}) 与文件名标题/年份均不吻合，不可信")
                    alt = None
                if alt:
                    ok, msg, info = _check_imdb_override(alt['imdb_id'], parsed['eng_title'],
                                                         parsed['year'], base_url, api_key, cache)
                    cn, an = _norm_cmp(parsed['cjk_title']), _norm_cmp(alt['title'])
                    exact = bool(cn and an and cn == an)
                    note = None
                    if not exact:
                        note = '经 Jellyfin RemoteSearch 命中（中文名不完全一致，请人工核对）'
                    if msg and not ok:
                        note = f"{note}；{msg}" if note else msg
                    result = {'found': True, 'imdb_id': alt['imdb_id'],
                              'title': (info or {}).get('title') or alt['title'],
                              'year': (info or {}).get('year') or alt['year'],
                              'warning': note,
                              'searched': search_title, 'candidates': []}
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
            if f == video and not multi_video:
                dst = base / f"{new_stem}{f.suffix}"
            elif ext in VIDEO_EXTS:
                # 多 CD/多碟电影：带碟片标记的加 " - cdN" 后缀（Jellyfin 堆叠识别）；
                # 无标记的（sample/花絮/不同版本）跳过，避免同名互相覆盖
                marker = _cd_marker(f.stem)
                if marker:
                    dst = base / f"{new_stem} - {marker}{f.suffix}"
                else:
                    click.echo(f"  跳过（多视频组中无碟片标记）：{f.name}")
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

        v_renames = [(s, d) for s, d in renames if s.suffix.lower() in VIDEO_EXTS]
        new_video_name = (v_renames[0][1].name if len(v_renames) == 1
                          else (f"{new_stem} - cdN（{len(v_renames)} 碟）" if v_renames
                                else f"{new_stem}{video.suffix}"))
        for s, d in renames:
            tag = "视频" if s.suffix.lower() in VIDEO_EXTS else "跟随"
            click.echo(f"  {tag}：{s.name}\n     → {d.name}")
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
# server：Jellyfin API 集成（配置在 [jellyfin] 根段的 base_url/api_key）
# ---------------------------------------------------------------------------

@cli.group()
def server():
    """Jellyfin 服务器 API 操作（需要 [jellyfin] 的 base_url/api_key）。"""


def _server_client() -> httpx.Client:
    cfg = _find_config()
    base_url, api_key = _get_server_config(cfg)
    if not base_url or not api_key:
        click.echo(
            "错误：未配置 Jellyfin 服务器。请在 agent_config.toml 的 [jellyfin] 段添加：\n"
            '  base_url = "http://localhost:8096"\n'
            '  api_key = "<控制台 → 高级 → API 密钥 生成的 key>"',
            err=True,
        )
        sys.exit(1)
    return httpx.Client(
        base_url=base_url,
        timeout=30.0,
        headers={
            "Authorization": (
                f'MediaBrowser Token="{api_key}", Client="jellyfin-tool", '
                'Device="jellyfin-tool", DeviceId="jellyfin-tool", Version="1.0"'
            )
        },
    )


def _server_request(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
    try:
        r = client.request(method, url, **kw)
    except httpx.ConnectError:
        click.echo(f"错误：无法连接 Jellyfin 服务器 ({client.base_url})", err=True)
        sys.exit(1)
    if r.status_code in (401, 403):
        click.echo("错误：Jellyfin 认证失败，请检查 [jellyfin] api_key（控制台 → 高级 → API 密钥）", err=True)
        sys.exit(1)
    if r.status_code >= 400:
        click.echo(f"Jellyfin API 错误：{method} {url} → HTTP {r.status_code} {r.text[:200]}", err=True)
        sys.exit(1)
    return r


def _first_user_id(client: httpx.Client) -> str:
    users = _server_request(client, 'GET', '/Users').json()
    if not users:
        click.echo("错误：Jellyfin 服务器上没有用户", err=True)
        sys.exit(1)
    return users[0]['Id']


def _server_refresh(library: str, replace_metadata: bool, replace_images: bool) -> None:
    """按库名或路径找到媒体库并触发全量刷新。"""
    client = _server_client()
    folders = _server_request(client, 'GET', '/Library/VirtualFolders').json()
    lib_norm = library.rstrip('/\\').lower()
    target = None
    for vf in folders:
        name = (vf.get('Name') or '').lower()
        locs = [str(l).rstrip('/\\').lower() for l in (vf.get('Locations') or [])]
        if name == lib_norm or lib_norm in locs or any(
                l.endswith('/' + lib_norm) or lib_norm.endswith('/' + l) for l in locs):
            target = vf
            break
    if target is None:
        click.echo(f"错误：找不到库「{library}」。可用库：", err=True)
        for vf in folders:
            click.echo(f"  - {vf.get('Name')}  ({', '.join(vf.get('Locations') or [])})", err=True)
        sys.exit(1)

    item_id = target.get('ItemId')
    if not item_id:
        uid = _first_user_id(client)
        views = _server_request(client, 'GET', f'/Users/{uid}/Views').json()
        for it in views.get('Items', []):
            if (it.get('Name') or '').lower() == (target.get('Name') or '').lower():
                item_id = it['Id']
                break
    if not item_id:
        click.echo(f"错误：无法解析库「{target.get('Name')}」的 ItemId", err=True)
        sys.exit(1)

    params = {
        'Recursive': 'true',
        'MetadataRefreshMode': 'FullRefresh',
        'ImageRefreshMode': 'FullRefresh',
    }
    if replace_metadata:
        params['ReplaceAllMetadata'] = 'true'
    if replace_images:
        params['ReplaceAllImages'] = 'true'
    _server_request(client, 'POST', f'/Items/{item_id}/Refresh', params=params)
    click.echo(f"已触发刷新：{target.get('Name')} (ItemId={item_id})")


@server.command(name='refresh')
@click.argument('library')
@click.option('--replace-metadata', is_flag=True, help='附加 ReplaceAllMetadata=true（替换全部元数据）')
@click.option('--replace-images', is_flag=True, help='附加 ReplaceAllImages=true（替换全部图片）')
def server_refresh(library, replace_metadata, replace_images):
    """对指定库（库名或路径）触发 POST /Items/{id}/Refresh 全量刷新。"""
    _server_refresh(library, replace_metadata, replace_images)


@server.command(name='images')
@click.argument('path')
def server_images(path):
    """列出某目录下所有电影的 ImageInfos，用于诊断「海报为什么没更新」。"""
    client = _server_client()
    uid = _first_user_id(client)
    prefix = path.rstrip('/\\').replace('\\', '/')
    matched = []
    start = 0
    while True:
        d = _server_request(client, 'GET', f'/Users/{uid}/Items', params={
            'Recursive': 'true', 'IncludeItemTypes': 'Movie',
            'Fields': 'Path,ProductionYear', 'StartIndex': start, 'Limit': 500,
        }).json()
        items = d.get('Items', [])
        for it in items:
            p = (it.get('Path') or '').replace('\\', '/')
            if p == prefix or p.startswith(prefix + '/'):
                matched.append(it)
        start += len(items)
        if not items or start >= d.get('TotalRecordCount', 0):
            break
    if not matched:
        click.echo(f"该目录下没有匹配到电影：{path}")
        return
    click.echo(f"共 {len(matched)} 部电影：")
    for it in matched:
        infos = _server_request(client, 'GET', f"/Items/{it['Id']}/Images").json()
        click.echo(f"\n{it.get('Name')} ({it.get('ProductionYear') or '?'})")
        click.echo(f"  路径：{it.get('Path')}")
        if not infos:
            click.echo("  (无图片)")
        for info in infos:
            size_kb = (info.get('Size') or 0) / 1024
            wh = f"{info.get('Width') or '?'}x{info.get('Height') or '?'}"
            click.echo(f"  {str(info.get('ImageType')):<10} {wh:>9}  {size_kb:>6.0f} KB  {info.get('Path') or ''}")


# ---------------------------------------------------------------------------
# de-localart：本地旧图/.nfo 让位在线刮削
# ---------------------------------------------------------------------------

LOCALART_SUFFIX = re.compile(r'-(poster|backdrop|landscape|logo|fanart\d*)$', re.IGNORECASE)


@cli.command(name='de-localart')
@click.argument('directory', default='.', type=click.Path(exists=True, file_okay=False))
@click.option('--delete', 'do_delete', is_flag=True, help='直接删除而不是移动到备份目录')
@click.option('--refresh', 'do_refresh', is_flag=True, help='完成后直接调用 server refresh 刷新库（拉取在线图）')
@click.option('--yes', '-y', is_flag=True, help='跳过确认，直接执行')
@click.option('--dry-run', is_flag=True, help='只预览，不执行')
def de_localart(directory, do_delete, do_refresh, yes, dry_run):
    """移除本地旧图和 .nfo，让位给 Jellyfin 在线刮削。

    Jellyfin 本地图片优先级高于在线刮削，旧海报会永久挡住在线海报（前端表现为
    视频截图缩略图）。本命令把目录（含子目录）下匹配
    -(poster|backdrop|landscape|logo|fanart) 的图片（含 extrafanart/ 目录内的）
    和所有 .nfo 移动到 <目录>_oldart_backup/（默认）或用 --delete 直接删除。
    """
    base = Path(directory).resolve()
    backup = base.parent / f"{base.name}_oldart_backup"
    targets = []
    for f in sorted(base.rglob('*')):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext == '.nfo':
            targets.append(f)
        elif ext in IMAGE_EXTS and (LOCALART_SUFFIX.search(f.stem)
                                    or f.parent.name.lower() == 'extrafanart'):
            targets.append(f)

    if not targets:
        click.echo("没有找到本地图片或 .nfo 文件。")
        return
    action = "删除" if do_delete else f"移动到 {backup}/"
    click.echo(f"共 {len(targets)} 个文件将被{action}：")
    for f in targets:
        click.echo(f"  {f.relative_to(base)}")

    if dry_run:
        click.echo("[预览模式，未执行]")
        return
    if not yes:
        if click.prompt("确认执行？[y/N]", default='N').lower() != 'y':
            click.echo("已取消。")
            return
    for f in targets:
        if do_delete:
            f.unlink()
        else:
            dst = backup / f.relative_to(base)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst))
    click.echo("完成。")

    if do_refresh:
        _server_refresh(str(base), replace_metadata=False, replace_images=True)
    else:
        click.echo("\n提示：请刷新库以拉取在线海报（本地图片已让位）：")
        click.echo(f'  jellyfin_tool.py server refresh "{base}" --replace-images')
        click.echo("  或重跑本命令时加 --refresh 自动刷新。")


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


# ---------------------------------------------------------------------------
# server identify / check：错误刮削修复工具链
# ---------------------------------------------------------------------------


def _all_movies_by_path(client: httpx.Client, uid: str) -> dict[str, dict]:
    """拉取全部电影（含 Path/ProviderIds/ProductionYear），按规范化路径建索引。

    文件改名会让 Jellyfin 删除旧条目、新建条目，ItemId 随之变化——所以任何
    按 ItemId 的操作（identify/refresh）都必须实时按路径查询当前条目，
    不能复用改名前缓存的 ItemId。
    """
    items: dict[str, dict] = {}
    start = 0
    while True:
        d = _server_request(client, 'GET', f'/Users/{uid}/Items', params={
            'Recursive': 'true', 'IncludeItemTypes': 'Movie',
            'Fields': 'Path,ProductionYear,ProviderIds', 'StartIndex': start, 'Limit': 500,
        }).json()
        batch = d.get('Items', [])
        for it in batch:
            p = (it.get('Path') or '').replace('\\', '/')
            if p:
                items[p] = it
        start += len(batch)
        if not batch or start >= d.get('TotalRecordCount', 0):
            break
    return items


def _find_item_for_file(items: dict[str, dict], local: Path):
    """按服务器路径精确匹配本地文件对应的条目；本地挂载点与服务器路径不一致时
    （如 macOS 挂载 SMB 到 /Volumes/xxx），退化为按文件名匹配（要求唯一）。"""
    norm = str(local).replace('\\', '/')
    if norm in items:
        return items[norm]
    hits = [it for p, it in items.items() if p.rsplit('/', 1)[-1] == local.name]
    return hits[0] if len(hits) == 1 else None


@server.command(name='identify')
@click.argument('path', type=click.Path(exists=True))
@click.option('--imdb-id', 'imdb_id', default=None,
              help='手动指定 IMDB ID；不传则从文件名的 [imdbid-ttXXX] 读取（目录模式必须如此）')
@click.option('--yes', '-y', is_flag=True, help='跳过确认，直接执行')
@click.option('--dry-run', is_flag=True, help='只预览，不执行')
def server_identify(path, imdb_id, yes, dry_run):
    """纠正错误刮削：通过「识别」流程把条目重新绑定到正确的 IMDB ID。

    Jellyfin 首次刮削后会把 ProviderIds 缓存在条目上，之后 ReplaceAllMetadata
    刷新也只沿用旧 ID；改文件名里的 imdbid 不会触发重新识别，必须走识别流程：
    取 ProviderIds.Imdb 与目标一致的首个结果，再 POST /Items/RemoteSearch/Apply/{itemId}。

    Apply 前会先移走同名 .nfo（改名为 .bak）：否则 Jellyfin 会把当前（可能是错的）
    元数据写回 nfo，本地 nfo 优先级最高，会把 Apply 的结果再压回去。

    传目录时批量处理目录下所有文件名带 [imdbid-ttXXX] 的视频。
    """
    p = Path(path).resolve()
    targets: list[tuple[Path, str]] = []
    if p.is_dir():
        if imdb_id:
            click.echo("错误：目录模式从每个文件名读取 [imdbid-ttXXX]，不接受 --imdb-id", err=True)
            sys.exit(1)
        for f in sorted(p.iterdir()):
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                m = IMDB_TAG.search(f.name)
                if m:
                    targets.append((f, m.group(1)))
        if not targets:
            click.echo("目录下没有文件名带 [imdbid-ttXXX] 的视频。")
            return
    else:
        tid = imdb_id
        if not tid:
            m = IMDB_TAG.search(p.name)
            tid = m.group(1) if m else None
        if not tid:
            click.echo("错误：文件名中没有 [imdbid-ttXXX]，请用 --imdb-id 指定", err=True)
            sys.exit(1)
        targets.append((p, tid))

    client = _server_client()
    uid = _first_user_id(client)
    items = _all_movies_by_path(client, uid)

    plan = []
    for f, tid in targets:
        parsed = _parse_renamed_name(f.stem)
        item = _find_item_for_file(items, f)
        plan.append((f, tid, parsed, item))

    click.echo(f"共 {len(plan)} 个识别任务：")
    for f, tid, parsed, item in plan:
        hint = f"{parsed['eng_title'] or parsed['cjk_title']} ({parsed['year'] or '?'})"
        if item:
            click.echo(f"  {f.name}\n    当前 DB：{item.get('Name')} ({item.get('ProductionYear') or '?'})"
                       f" [{((item.get('ProviderIds') or {}).get('Imdb')) or '无 imdb'}]"
                       f"\n    识别为：{hint} [{tid}]")
        else:
            click.echo(f"  {f.name}\n    识别为：{hint} [{tid}]  ⚠ 库中未找到对应条目（需先扫库）")

    if dry_run:
        click.echo("[预览模式，未执行]")
        return
    if not yes:
        if click.prompt("\n确认执行识别？[y/N]", default='N').lower() != 'y':
            click.echo("已取消。")
            return

    failures = []
    for f, tid, parsed, item in plan:
        if not item:
            click.echo(f"✗ {f.name}：库中未找到对应条目，跳过（先 server refresh 扫库）", err=True)
            failures.append(f.name)
            continue
        # 1) Apply 前移走同名 .nfo，防止旧元数据回压
        nfo = f.with_suffix('.nfo')
        if nfo.exists():
            bak = nfo.with_name(nfo.name + '.bak')
            shutil.move(str(nfo), str(bak))
            click.echo(f"  已移走同名 nfo → {bak.name}")
        # 2) RemoteSearch：IMDB ID + 文件名解析出的 Name/Year 提示
        search_info: dict = {'ProviderIds': {'Imdb': tid}}
        if parsed['eng_title'] or parsed['cjk_title']:
            search_info['Name'] = parsed['eng_title'] or parsed['cjk_title']
        if parsed['year']:
            search_info['Year'] = parsed['year']
        results = _server_request(client, 'POST', '/Items/RemoteSearch/Movie',
                                  json={'SearchInfo': search_info}).json()
        if not results:
            click.echo(f"✗ {f.name}：RemoteSearch 无结果（{tid}）", err=True)
            failures.append(f.name)
            continue
        # 优先取 ProviderIds.Imdb 与目标一致的结果（首结果可能来自无 Imdb 的提供商）
        best = next((r for r in results
                     if (r.get('ProviderIds') or {}).get('Imdb', '').lower() == tid.lower()),
                    results[0])
        # 3) Apply 第一个结果（不替换现有图片）
        _server_request(client, 'POST', f"/Items/RemoteSearch/Apply/{item['Id']}",
                        params={'replaceAllImages': 'false'}, json=best)
        click.echo(f"✓ {f.name}\n    → {best.get('Name')} ({best.get('ProductionYear') or '?'}) [{tid}]")

    click.echo("\n提示：旧 nfo 已备份为 .bak；待 Jellyfin 用新元数据重新生成 nfo 后可删除 .bak。")
    if failures:
        click.echo(f"失败 {len(failures)} 个：")
        for n in failures:
            click.echo(f"  - {n}")
        sys.exit(1)


@server.command(name='check')
@click.argument('directory', type=click.Path(exists=True, file_okay=False))
def server_check(directory):
    """诊断对账：批量比对 Jellyfin DB 元数据 vs 文件名。

    输出三类结果：
    ✗ 脏标题/错误绑定（DB 名与文件名完全对不上、IMDB 错绑）或年份偏差 ≥2
      （如 12 Angry Men 刮成 1997 电视电影版）→ 需 server identify 修复
    ⚠ 年份偏差 ±1（产地年 vs 发行年，正常，仅标注）
    ✓ 正常
    """
    base = Path(directory).resolve()
    files = [f for f in sorted(base.iterdir())
             if f.is_file() and f.suffix.lower() in VIDEO_EXTS]
    if not files:
        click.echo("目录下没有视频文件。")
        return
    client = _server_client()
    uid = _first_user_id(client)
    items = _all_movies_by_path(client, uid)

    need_identify = []
    year_notes = []
    trans_notes = []
    missing = []
    ok_count = 0
    for f in files:
        parsed = _parse_renamed_name(f.stem)
        if not parsed['eng_title'] and not parsed['cjk_title']:
            fp = _parse_folder_name(f.stem)
            parsed['eng_title'], parsed['cjk_title'] = fp['eng_title'], fp['cjk_title']
            parsed['year'] = parsed['year'] or fp['year']
        label = parsed['cjk_title'] or parsed['eng_title'] or f.stem
        item = _find_item_for_file(items, f)
        if item is None:
            missing.append(f.name)
            continue
        db_name = item.get('Name') or ''
        db_year = item.get('ProductionYear')
        db_imdb = (item.get('ProviderIds') or {}).get('Imdb') or ''
        fn_imdb = parsed['imdb_id'] or ''
        name_ok = bool(
            (parsed['cjk_title'] and (parsed['cjk_title'] in db_name or db_name in parsed['cjk_title']))
            or (parsed['eng_title'] and _titles_match(parsed['eng_title'], db_name)))
        year_diff = abs(db_year - parsed['year']) if (parsed['year'] and db_year) else 0
        imdb_same = bool(fn_imdb and db_imdb and fn_imdb.lower() == db_imdb.lower())

        problems = []
        if fn_imdb and db_imdb and not imdb_same:
            # 错绑：文件名 id 与 DB ProviderIds.Imdb 不一致（如七宗罪刮成 Die Grube）
            problems.append(f"IMDB 错绑：文件 {fn_imdb} vs DB {db_imdb}")
        if not name_ok and (parsed['cjk_title'] or parsed['eng_title']):
            if imdb_same:
                # IMDB 一致只是译名不同（无间行者/无间道风云），不是错绑
                trans_notes.append(f"  {label}：文件译名 vs DB「{db_name}」")
            else:
                problems.append(
                    f"标题不符：文件「{parsed['cjk_title']} {parsed['eng_title']}」vs DB「{db_name}」")
        if year_diff:
            if imdb_same or year_diff == 1:
                # 产地年 vs 发行年口径差异（同 IMDB 时不代表错绑，如千与千寻 2001/2003）
                year_notes.append(f"  {label}：文件 {parsed['year']} vs DB {db_year}")
            else:
                problems.append(f"年份偏差 {year_diff} 年：文件 {parsed['year']} vs DB {db_year}（可能错绑版本）")
        if problems:
            need_identify.append((f, label, db_name, db_year, db_imdb, problems))
        else:
            ok_count += 1

    click.echo(f"\n{'='*60}\n对账结果：{base}（{len(files)} 个视频）\n{'='*60}")
    click.echo(f"✓ 正常：{ok_count} 部")
    if year_notes:
        click.echo(f"\n⚠ 年份口径差异（产地年 vs 发行年，无需处理）：{len(year_notes)} 部")
        for n in year_notes:
            click.echo(n)
    if trans_notes:
        click.echo(f"\nℹ 译名差异（IMDB 一致，无需处理）：{len(trans_notes)} 部")
        for n in trans_notes:
            click.echo(n)
    if missing:
        click.echo(f"\n? 未入库/未扫描：{len(missing)} 部（先 server refresh 扫库）")
        for n in missing:
            click.echo(f"  {n}")
    if need_identify:
        click.echo(f"\n✗ 需要 identify 修复：{len(need_identify)} 部")
        for f, label, db_name, db_year, db_imdb, problems in need_identify:
            click.echo(f"\n  {label}：DB「{db_name}」({db_year or '?'}) [{db_imdb or '无 imdb'}]")
            for prob in problems:
                click.echo(f"    ⚠ {prob}")
            if f.stem and IMDB_TAG.search(f.stem):
                click.echo(f'    修复：jellyfin_tool.py server identify "{f}"')
            else:
                click.echo(f'    修复：jellyfin_tool.py server identify "{f}" --imdb-id ttXXXXXXX')
        sys.exit(1)


if __name__ == '__main__':
    cli()
