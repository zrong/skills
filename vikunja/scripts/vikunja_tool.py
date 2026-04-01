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
        return self._request("GET", "tasks/all", params=params)

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
            filters["filter"] += f" AND {done_filter}"
        else:
            filters["filter"] = done_filter
    if week:
        monday, sunday = _week_range(week)
        week_filter = f'done_at >= "{monday.isoformat()}" AND done_at <= "{sunday.isoformat()}"'
        if "filter" in filters:
            filters["filter"] += f" AND {week_filter}"
        else:
            filters["filter"] = week_filter

    tasks = client.list_tasks(per_page=limit, **filters)
    click.echo(json.dumps(tasks, indent=2, ensure_ascii=False))


def _week_range(week_str: str) -> tuple:
    """从 ISO 周字符串 (如 '2026-W14') 计算该周的周一和周日"""
    monday = datetime.strptime(week_str + "-1", "%G-W%V-%u").replace(
        tzinfo=timezone(timedelta(hours=8))
    )
    sunday = monday + timedelta(days=6)
    sunday = sunday.replace(hour=23, minute=59, second=59)
    return monday, sunday


if __name__ == "__main__":
    cli()
