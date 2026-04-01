#!/usr/bin/env python3
import sys
import json
import re
import subprocess
import click
import httpx
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def _find_config() -> Path:
    """查找 agent_config.toml"""
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
            return candidate

    raise FileNotFoundError(
        f"未找到 agent_config.toml\n"
        f"搜索了: {', '.join(str(c) for c in candidates)}"
    )


CONFIG_PATH = _find_config()

try:
    _config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
except Exception as e:
    print(f"Error loading config: {e}", file=sys.stderr)
    sys.exit(1)

_vikunja_config = _config.get("vikunja", {})
VIKUNJA_API_URL = _vikunja_config.get("api_url", "").rstrip("/")
VIKUNJA_API_TOKEN = _vikunja_config.get("api_token", "")

_joplin_config = _config.get("joplin", {})
JOPLIN_TOKEN = _joplin_config.get("token", "")
JOPLIN_BASE_URL = _joplin_config.get("base_url", "http://localhost:41184")


class VikunjaClient:
    """Vikunja REST API 客户端"""

    def __init__(self, api_url: str, token: str):
        self.api_url = api_url.rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None,
                 json_data: Optional[Dict[str, Any]] = None):
        url = f"{self.api_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self.token}"}

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.request(method, url, params=params,
                                          json=json_data, headers=headers)
                response.raise_for_status()
                if not response.content:
                    return {}
                try:
                    return response.json()
                except json.JSONDecodeError:
                    return {"content": response.text}
        except httpx.ConnectError:
            click.echo(f"Error: 无法连接 Vikunja API ({self.api_url})", err=True)
            sys.exit(1)
        except httpx.HTTPStatusError as e:
            click.echo(f"HTTP Error {e.response.status_code}: {e.response.text}", err=True)
            sys.exit(1)
        except Exception as e:
            click.echo(f"Unexpected error: {e}", err=True)
            sys.exit(1)

    def list_projects(self):
        return self._request("GET", "projects")

    def list_tasks(self, page: int = 1, per_page: int = 50, **filters):
        params = {"page": page, "per_page": per_page}
        params.update(filters)
        return self._request("GET", "tasks", params=params)

    def get_task(self, task_id: int):
        return self._request("GET", f"tasks/{task_id}")


def _call_joplin(*args) -> dict:
    """调用 joplin_tool.py CLI 并解析 JSON 输出"""
    joplin_script = Path(__file__).resolve().parent.parent.parent / "joplin" / "scripts" / "joplin_tool.py"
    if not joplin_script.exists():
        click.echo("Error: joplin skill 未安装。请从 https://github.com/zrong/skills 安装。", err=True)
        sys.exit(1)

    cmd = ["uv", "run", "--project", str(joplin_script.parent), str(joplin_script)] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            click.echo(f"Joplin tool error: {result.stderr}", err=True)
            sys.exit(1)
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except subprocess.TimeoutExpired:
        click.echo("Error: joplin_tool.py 调用超时", err=True)
        sys.exit(1)
    except json.JSONDecodeError:
        click.echo(f"Error: 无法解析 joplin 输出: {result.stdout[:200]}", err=True)
        sys.exit(1)


@click.group()
def cli():
    """Vikunja CLI Tool — 任务管理与 Joplin 同步"""
    pass


@cli.command("list-projects")
def list_projects():
    """列出所有项目"""
    client = VikunjaClient(VIKUNJA_API_URL, VIKUNJA_API_TOKEN)
    projects = client.list_projects()
    click.echo(json.dumps(projects, indent=2, ensure_ascii=False))


@cli.command("list-tasks")
@click.option("--project", "-p", type=int, help="按项目 ID 过滤")
@click.option("--done", is_flag=True, help="只显示已完成的任务")
@click.option("--week", "-w", help="按 ISO 周过滤 (如 2026-W14)")
@click.option("--limit", "-l", default=50, help="每页数量")
def list_tasks(project, done, week, limit):
    """列出任务"""
    client = VikunjaClient(VIKUNJA_API_URL, VIKUNJA_API_TOKEN)
    filters = {}

    if project:
        filters["filter"] = f"project_id = {project}"
    if done:
        done_filter = 'done = true'
        if "filter" in filters:
            filters["filter"] += f" && {done_filter}"
        else:
            filters["filter"] = done_filter
    if week:
        monday, sunday = _week_range(week)
        utc_monday = monday.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        utc_sunday = sunday.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        week_filter = f'done_at >= "{utc_monday}" && done_at <= "{utc_sunday}"'
        if "filter" in filters:
            filters["filter"] += f" && {week_filter}"
        else:
            filters["filter"] = week_filter

    tasks = client.list_tasks(per_page=limit, **filters)
    click.echo(json.dumps(tasks, indent=2, ensure_ascii=False))


@cli.command("sync-weekly")
@click.option("--note", help="Joplin 笔记标题 (如 '2026-03-14weeks')")
@click.option("--week", "-w", help="ISO 周编号 (如 '2026-W14')")
def sync_weekly(note, week):
    """同步 Vikunja 已完成任务到 Joplin weekly 笔记"""
    # 1. 确定目标周
    if note:
        week = _parse_week_from_note_title(note)
        if not week:
            click.echo(f"Error: 无法从笔记标题 '{note}' 解析周数", err=True)
            sys.exit(1)
    elif not week:
        week = datetime.now(timezone(timedelta(hours=8))).strftime("%G-W%V")

    click.echo(f"目标周: {week}")

    # 2. 计算日期范围
    monday, sunday = _week_range(week)
    click.echo(f"日期范围: {monday.strftime('%Y-%m-%d')} ~ {sunday.strftime('%Y-%m-%d')}")

    # 3. 查找 weekly 笔记
    note_data = _find_weekly_note(week)
    if not note_data:
        click.echo(f"Error: 未找到对应 {week} 的 weekly 笔记", err=True)
        sys.exit(1)

    note_id = note_data["id"]
    full_note = _call_joplin("note", "get", note_id)
    note_body = full_note.get("body", "")
    click.echo(f"找到笔记: {note_data.get('title', note_id)}")

    # 4. 获取 Vikunja 任务
    client = VikunjaClient(VIKUNJA_API_URL, VIKUNJA_API_TOKEN)
    tasks = _get_vikunja_tasks_for_week(client, monday, sunday)
    if not tasks:
        click.echo("该周没有已完成的任务")
        return

    click.echo(f"找到 {len(tasks)} 个已完成任务")

    # 5. 按日期分组并同步
    tasks_by_date = _group_tasks_by_date(tasks)
    _sync_tasks_to_note(note_id, note_body, tasks_by_date)


def _week_range(week_str: str) -> tuple:
    """从 ISO 周字符串 (如 '2026-W14') 计算该周的周一和周日"""
    monday = datetime.strptime(week_str + "-1", "%G-W%V-%u").replace(
        tzinfo=timezone(timedelta(hours=8))
    )
    sunday = monday + timedelta(days=6)
    sunday = sunday.replace(hour=23, minute=59, second=59)
    return monday, sunday


def _parse_week_from_note_title(title: str) -> Optional[str]:
    """从笔记标题解析 ISO 周字符串。

    标题格式: '2026-03-14weeks' → '2026-W14'（03 是月份，仅作展示用途）
    """
    m = re.match(r"(\d{4})-\d{2}-(\d{1,2})weeks", title)
    if not m:
        return None
    year, week_num = int(m.group(1)), int(m.group(2))
    return f"{year}-W{week_num:02d}"


def _find_weekly_note(target_week_str: str) -> Optional[dict]:
    """在 Joplin 中查找 weekly 笔记。

    1. 搜索带有 'weekly' 标签的笔记
    2. 匹配标题中的周数与目标周
    """
    notes = _call_joplin("search", "--query", "tag:weekly", "--type", "note", "--limit", "50")
    items = notes if isinstance(notes, list) else notes.get("items", [])

    for note in items:
        note_title = note.get("title", "")
        note_week = _parse_week_from_note_title(note_title)
        if note_week == target_week_str:
            return note

    return None


def _get_vikunja_tasks_for_week(client: VikunjaClient, monday: datetime, sunday: datetime) -> list:
    """获取 Vikunja 中指定周已完成的任务"""
    utc_monday = monday.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    utc_sunday = sunday.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_tasks = []
    page = 1
    while True:
        result = client.list_tasks(
            page=page, per_page=100,
            **{"filter": f'done = true && done_at >= "{utc_monday}" && done_at <= "{utc_sunday}"'}
        )
        tasks = result if isinstance(result, list) else result.get("items", [])
        all_tasks.extend(tasks)
        if len(tasks) < 100:
            break
        page += 1
    return all_tasks


def _group_tasks_by_date(tasks: list) -> dict[str, list]:
    """按 done_at 日期分组任务"""
    tz = timezone(timedelta(hours=8))
    groups: dict[str, list] = {}
    for task in tasks:
        done_at = task.get("done_at", "")
        if not done_at:
            continue
        dt = datetime.fromisoformat(done_at.replace("Z", "+00:00")).astimezone(tz)
        date_str = dt.strftime("%Y-%m-%d")
        groups.setdefault(date_str, []).append(task)
    return groups


def _find_existing_vikunja_ids(note_body: str) -> set[int]:
    """从笔记内容中提取已存在的 Vikunja task ID"""
    pattern = re.compile(r"#vikunja:(\d+)")
    return {int(m.group(1)) for m in pattern.finditer(note_body)}


def _sync_tasks_to_note(note_id: str, note_body: str, tasks_by_date: dict[str, list]):
    """将任务同步到笔记的对应日期节下"""
    existing_ids = _find_existing_vikunja_ids(note_body)
    lines = note_body.split("\n")
    new_lines = []
    i = 0
    modified = False

    while i < len(lines):
        new_lines.append(lines[i])

        # 检测 "# YYYY-MM-DD DayName" 格式的日期标题
        heading_match = re.match(r"^# (\d{4}-\d{2}-\d{2})\s+\w+", lines[i])
        if heading_match:
            date_str = heading_match.group(1)
            if date_str in tasks_by_date:
                # 找到这个日期下的任务列表末尾，然后追加新任务
                j = i + 1
                while j < len(lines) and lines[j].strip() != "" and not lines[j].startswith("# "):
                    new_lines.append(lines[j])
                    j += 1

                for task in tasks_by_date[date_str]:
                    if task["id"] not in existing_ids:
                        task_line = f"- [{task['title']}](#vikunja:{task['id']})"
                        new_lines.append(task_line)
                        modified = True

                i = j
                continue

        i += 1

    if modified:
        _call_joplin("note", "update", note_id, "--body", "\n".join(new_lines))
        click.echo(f"已同步到笔记 {note_id}")
    else:
        click.echo("没有新任务需要同步")


if __name__ == "__main__":
    cli()
