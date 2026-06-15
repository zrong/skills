#!/usr/bin/env python3
"""email_tool - 邮件处理 CLI 工具（基于 IMAP）

命令：
  init          初始化配置（交互式创建账户）
  folders       列出所有文件夹
  list          列出邮件（主题、发件人、日期）
  read          读取邮件内容（文本/HTML）
  download      下载邮件附件
  move          移动邮件到指定文件夹
  search-links  从邮件 HTML 提取链接
"""

import argparse
import email as emaillib
import imaplib
import json
import os
import re
import sys
import tomllib
from email.header import decode_header
from pathlib import Path

if sys.version_info < (3, 13):
    print("ERROR: Python 3.13+ is required.", file=sys.stderr)
    sys.exit(1)

# ============================================================
# 常量与配置
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
CONFIG_SECTION = "email"

DEFAULT_ACCOUNT = "qqmail"
DEFAULT_CONFIG = "agent_config.toml"


# ============================================================
# 工具函数
# ============================================================

def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg: str):
    print(f"INFO: {msg}", file=sys.stderr)


def output_json(data, pretty: bool = True):
    if pretty:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False))


def _decode_subject(raw_subj: str) -> str:
    """解码邮件主题（处理 MIME 编码）。"""
    subject = ""
    for part_bytes, charset in decode_header(raw_subj):
        if isinstance(part_bytes, bytes):
            enc = charset or "utf-8"
            if enc.lower() in ("unknown-8bit", "x-unknown"):
                enc = "utf-8"
            subject += part_bytes.decode(enc, errors="replace")
        else:
            subject += part_bytes
    return subject


def _imap_utf7_decode(s: str) -> str:
    """Decode IMAP modified UTF-7 folder name to Unicode."""
    result = []
    i = 0
    while i < len(s):
        if s[i] == "&":
            j = s.index("-", i)
            if j == i + 1:
                result.append("&")
            else:
                encoded = "+" + s[i + 1 : j].replace(",", "/") + "-"
                result.append(encoded.encode("ascii").decode("utf-7"))
            i = j + 1
        else:
            result.append(s[i])
            i += 1
    return "".join(result)


# ============================================================
# 配置加载
# ============================================================

def _load_dotenv():
    """从当前工作目录的 .env 文件加载环境变量（不覆盖已有）。"""
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _find_config() -> Path:
    """查找 agent_config.toml 配置文件（三位置发现策略）。

    优先级（从高到低）：
    1. 当前工作目录（CWD）/agent_config.toml
    2. Skill 目录（SKILL_DIR）/agent_config.toml
    3. 向上查找 .git 所在目录/agent_config.toml
    """
    candidates = [
        Path.cwd() / "agent_config.toml",
        SKILL_DIR / "agent_config.toml",
    ]
    for parent in Path.cwd().parents:
        if (parent / ".git").exists():
            candidates.append(parent / "agent_config.toml")
            break

    for candidate in candidates:
        if candidate.exists():
            return candidate

    searched = ", ".join(str(c) for c in candidates)
    die(
        f"未找到 agent_config.toml\n"
        f"搜索了: {searched}\n"
        f"请复制 {SKILL_DIR / 'agent_config.example.toml'} 为 agent_config.toml 并填入实际值"
    )


def _resolve_config_path(config_arg: str | None) -> Path:
    """解析配置文件路径。

    - 显式路径：直接解析（相对 cwd）
    - 默认值：使用三位置发现策略
    """
    if config_arg:
        p = Path(config_arg)
        if not p.is_absolute():
            p = Path.cwd() / p
        return p
    return _find_config()


def load_account(config_path: Path, account_name: str) -> dict:
    """从 agent_config.toml 加载账户，密码优先从环境变量读取。

    密码优先级：
    1. .env 文件中的 EMAIL_{ACCOUNT}_PASSWORD 环境变量
    2. agent_config.toml 中账户的 password 字段
    """
    if not config_path.exists():
        die(f"Config file not found: {config_path}\nRun 'email_tool.py init' to create one.")
    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    email_config = config.get(CONFIG_SECTION, {})
    accounts = email_config.get("accounts", {})
    if account_name not in accounts:
        available = ", ".join(accounts.keys()) or "(none)"
        die(f"Account '{account_name}' not found. Available: {available}")
    acct = accounts[account_name]

    _load_dotenv()
    env_key = f"EMAIL_{account_name.upper()}_PASSWORD"
    password = os.environ.get(env_key, "") or acct.get("password", "")
    if not password:
        die(
            f"Password not found for account '{account_name}'.\n"
            f"Set {env_key} in .env, or add 'password' to [email.accounts.{account_name}] in agent_config.toml"
        )

    return {
        "host": acct.get("imap_host", "imap.qq.com"),
        "port": int(acct.get("imap_port", 993)),
        "user": acct.get("email", ""),
        "password": password,
    }


# ============================================================
# IMAP 连接辅助
# ============================================================

def _imap_connect(cfg: dict, folder: str = "INBOX") -> imaplib.IMAP4_SSL:
    """创建 IMAP 连接并选择文件夹。"""
    imap = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
    imap.login(cfg["user"], cfg["password"])
    imap.select(folder)
    return imap


def _imap_search(imap: imaplib.IMAP4_SSL, criteria: list[str], use_uid: bool = True) -> list[bytes]:
    """执行 IMAP 搜索，返回消息 ID/UID 列表。

    criteria 是 [KEY, VALUE, KEY, VALUE, ...] 形式的列表。
    非 ASCII 值会被编码为 UTF-8 字节串作为 IMAP literal 传递。
    """
    has_nonascii = any(ord(c) > 127 for c in " ".join(criteria))

    if has_nonascii:
        # 逐项处理：非 ASCII 值编码为 bytes（imaplib 会作为 literal 发送）
        args = ["CHARSET", "UTF-8"]
        for item in criteria:
            if any(ord(c) > 127 for c in item):
                args.append(item.encode("utf-8"))
            else:
                args.append(item)
        if use_uid:
            status, msgs = imap.uid("SEARCH", *args)
        else:
            status, msgs = imap.search("UTF-8", *args[2:])
    else:
        if use_uid:
            status, msgs = imap.uid("SEARCH", None, *criteria)
        else:
            status, msgs = imap.search(None, *criteria)

    if status != "OK" or not msgs[0]:
        return []
    return msgs[0].split()


def _build_search_criteria(args) -> list[str]:
    """从 args 构建 IMAP 搜索条件。

    注意：QQ 邮箱 IMAP 在 CHARSET UTF-8 模式下 BEFORE 关键词有 bug，
    因此当存在非 ASCII 搜索条件时，BEFORE 不传给 IMAP，而是在本地过滤。
    """
    criteria = []
    if getattr(args, "subject", None):
        criteria.extend(["SUBJECT", args.subject])
    if getattr(args, "sender", None):
        criteria.extend(["FROM", args.sender])
    if getattr(args, "since", None):
        criteria.extend(["SINCE", args.since])
    # BEFORE: 仅在无非 ASCII 条件时传给 IMAP（QQ Mail CHARSET UTF-8 + BEFORE bug）
    has_nonascii = any(ord(c) > 127 for c in " ".join(criteria))
    if getattr(args, "before", None) and not has_nonascii:
        criteria.extend(["BEFORE", args.before])
    if not criteria:
        die("At least one search criterion is required (--subject, --sender, --since, --before).")
    return criteria


def _parse_imap_date(date_str):
    """解析 IMAP 日期格式 DD-Mon-YYYY 为 datetime，失败返回 None。"""
    from datetime import datetime as dt
    try:
        return dt.strptime(date_str, "%d-%b-%Y")
    except (ValueError, TypeError):
        return None


def _parse_email_date(date_str):
    """解析邮件 Date 头为 datetime，失败返回 None。"""
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(date_str).replace(tzinfo=None)
    except Exception:
        return None


def _should_filter_before(args) -> str | None:
    """如果 BEFORE 未传给 IMAP（因为非 ASCII），返回 before 日期字符串用于本地过滤。"""
    before = getattr(args, "before", None)
    if not before:
        return None
    # 检查是否有非 ASCII 条件（与 _build_search_criteria 逻辑一致）
    parts = []
    if getattr(args, "subject", None):
        parts.extend(["SUBJECT", args.subject])
    if getattr(args, "sender", None):
        parts.extend(["FROM", args.sender])
    if getattr(args, "since", None):
        parts.extend(["SINCE", args.since])
    if any(ord(c) > 127 for c in " ".join(parts)):
        return before
    return None


def _resolve_folder(imap: imaplib.IMAP4_SSL, target_name: str) -> str:
    """解析 Unicode 文件夹名到 IMAP modified UTF-7 名称。找不到则 die。"""
    status, folder_list = imap.list()
    if status != "OK":
        die("Failed to list IMAP folders.")

    for folder_info in folder_list:
        if not isinstance(folder_info, bytes):
            continue
        decoded = folder_info.decode("utf-8", errors="replace")
        match = re.search(r'"([^"]*)"$', decoded)
        if not match:
            parts = decoded.rsplit(" ", 1)
            raw_name = parts[-1] if parts else ""
        else:
            raw_name = match.group(1)
        try:
            unicode_name = _imap_utf7_decode(raw_name)
        except Exception:
            unicode_name = raw_name
        if unicode_name == target_name or unicode_name.endswith("/" + target_name):
            return raw_name

    die(f"Folder '{target_name}' not found on IMAP server.")


def _fetch_subjects(imap: imaplib.IMAP4_SSL, uid_list: list[bytes]) -> list[str]:
    """批量获取邮件主题。"""
    subjects = []
    for uid in uid_list:
        status, data = imap.uid("FETCH", uid, "(BODY[HEADER.FIELDS (SUBJECT)])")
        if status != "OK" or not data[0] or len(data[0]) < 2:
            subjects.append("(unknown)")
            continue
        msg = emaillib.message_from_bytes(data[0][1])
        subjects.append(_decode_subject(msg.get("Subject", "")))
    return subjects


# ============================================================
# 命令实现
# ============================================================

def cmd_init(args):
    """交互式初始化配置文件。"""
    default_config_path = Path.cwd() / DEFAULT_CONFIG
    env_path = Path.cwd() / ".env"

    # 询问配置保存路径
    print(f"Config file path [{default_config_path}]: ", end="", flush=True)
    user_path = input().strip()
    config_path = Path(user_path) if user_path else default_config_path
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path

    # 询问账户信息
    print(f"Account name [{DEFAULT_ACCOUNT}]: ", end="", flush=True)
    account_name = input().strip() or DEFAULT_ACCOUNT

    print("Email address: ", end="", flush=True)
    email_addr = input().strip()
    if not email_addr:
        die("Email address is required.")

    print("IMAP host [imap.qq.com]: ", end="", flush=True)
    imap_host = input().strip() or "imap.qq.com"

    print("IMAP port [993]: ", end="", flush=True)
    imap_port_str = input().strip() or "993"
    imap_port = int(imap_port_str)

    print("SMTP host [smtp.qq.com]: ", end="", flush=True)
    smtp_host = input().strip() or "smtp.qq.com"

    print("SMTP port [465]: ", end="", flush=True)
    smtp_port_str = input().strip() or "465"
    smtp_port = int(smtp_port_str)

    print("Password / App password: ", end="", flush=True)
    password = input().strip()
    if not password:
        die("Password is required.")

    # 构建账户区块内容
    account_section = (
        f'[email.accounts.{account_name}]\n'
        f'email = "{email_addr}"\n'
        f'imap_host = "{imap_host}"\n'
        f'imap_port = {imap_port}\n'
        f'smtp_host = "{smtp_host}"\n'
        f'smtp_port = {smtp_port}\n'
        f'password = "{password}"\n'
    )

    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        # 追加到已有配置文件，不覆盖其他 skill 配置
        existing = config_path.read_text(encoding="utf-8")
        with open(config_path, "rb") as f:
            existing_config = tomllib.load(f)

        email_accounts = existing_config.get(CONFIG_SECTION, {}).get("accounts", {})
        if account_name in email_accounts:
            info(f"Account '{account_name}' already exists in {config_path}, overwriting...")

        # 确保文件以换行结尾
        if not existing.endswith("\n"):
            existing += "\n"
        existing += "\n" + account_section
        config_path.write_text(existing, encoding="utf-8")
    else:
        # 创建新配置文件
        header = (
            "# Skill 配置文件（多 skill 共用，按 [skill名] 分区）\n"
            "# 密码优先从 .env 环境变量 EMAIL_{ACCOUNT}_PASSWORD 读取\n"
            "# 也可直接在账户配置中填写 password 字段\n"
            "\n"
        )
        config_path.write_text(header + account_section, encoding="utf-8")

    info(f"Config saved to: {config_path}")

    # 同时写入 .env（追加，不覆盖已有内容）
    env_key = f"EMAIL_{account_name.upper()}_PASSWORD"
    env_line = f"{env_key}={password}\n"

    if env_path.exists():
        existing_env = env_path.read_text()
        if env_key in existing_env:
            lines = existing_env.splitlines(keepends=True)
            new_lines = []
            for line in lines:
                if line.strip().startswith(f"{env_key}="):
                    new_lines.append(env_line)
                else:
                    new_lines.append(line)
            env_path.write_text("".join(new_lines))
            info(f"Updated {env_key} in: {env_path}")
        else:
            with open(env_path, "a") as f:
                f.write(env_line)
            info(f"Appended {env_key} to: {env_path}")
    else:
        env_path.write_text(env_line)
        info(f"Created .env with {env_key}: {env_path}")

    # 确保 .gitignore 包含 .env
    gitignore_path = Path.cwd() / ".gitignore"
    if gitignore_path.exists():
        content = gitignore_path.read_text()
        if ".env" not in content.splitlines():
            with open(gitignore_path, "a") as f:
                f.write("\n.env\n")
            info("Added .env to .gitignore")
    else:
        gitignore_path.write_text(".env\n")
        info("Created .gitignore with .env")

    # 验证连接
    info("Testing IMAP connection...")
    try:
        imap = imaplib.IMAP4_SSL(imap_host, imap_port)
        imap.login(email_addr, password)
        imap.logout()
        info("Connection successful!")
    except Exception as e:
        info(f"Connection failed: {e}")
        info("Config files have been saved. Please check your credentials.")

    output_json({"config": str(config_path), "env": str(env_path), "account": account_name})


def cmd_folders(args):
    """列出所有 IMAP 文件夹。"""
    cfg = load_account(_resolve_config_path(args.config), args.account)
    imap = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
    imap.login(cfg["user"], cfg["password"])

    status, folder_list = imap.list()
    imap.logout()

    if status != "OK":
        die("Failed to list folders.")

    folders = []
    for folder_info in folder_list:
        if not isinstance(folder_info, bytes):
            continue
        decoded = folder_info.decode("utf-8", errors="replace")
        match = re.search(r'"([^"]*)"$', decoded)
        raw_name = match.group(1) if match else decoded.rsplit(" ", 1)[-1]
        try:
            unicode_name = _imap_utf7_decode(raw_name)
        except Exception:
            unicode_name = raw_name
        folders.append(unicode_name)

    for f in folders:
        info(f"  {f}")
    output_json(folders)


def cmd_list(args):
    """列出邮件元数据。"""
    cfg = load_account(_resolve_config_path(args.config), args.account)
    criteria = _build_search_criteria(args)

    folder = args.folder or "INBOX"
    if folder != "INBOX":
        # 非 INBOX 文件夹名可能是中文，需解析为 IMAP modified-UTF-7 名后再 select
        probe = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
        probe.login(cfg["user"], cfg["password"])
        folder = _resolve_folder(probe, folder)
        probe.logout()
    imap = _imap_connect(cfg, folder)

    uid_list = _imap_search(imap, criteria, use_uid=True)

    if not uid_list:
        imap.logout()
        info("No matching emails found.")
        output_json([])
        return

    if args.last and args.last > 0:
        uid_list = uid_list[-args.last:]

    # 本地 BEFORE 过滤（QQ Mail CHARSET UTF-8 + BEFORE bug workaround）
    before_str = _should_filter_before(args)
    before_dt = _parse_imap_date(before_str) if before_str else None

    results = []
    for uid in uid_list:
        status, data = imap.uid("FETCH", uid, "(BODY[HEADER.FIELDS (SUBJECT FROM DATE)])")
        if status != "OK" or not data[0] or len(data[0]) < 2:
            continue
        msg = emaillib.message_from_bytes(data[0][1])
        date_str = str(msg.get("Date", ""))

        if before_dt:
            email_dt = _parse_email_date(date_str)
            if email_dt and email_dt >= before_dt:
                continue

        results.append({
            "uid": uid.decode(),
            "subject": _decode_subject(msg.get("Subject", "")),
            "from": str(msg.get("From", "")),
            "date": date_str,
        })

    imap.logout()

    info(f"Found {len(results)} email(s):")
    for i, r in enumerate(results):
        info(f"  {i+1}. [{r['date'][:16]}] {r['subject']}")

    output_json(results)


def cmd_read(args):
    """读取邮件内容。"""
    cfg = load_account(_resolve_config_path(args.config), args.account)

    if args.uid:
        uids = [u.encode() for u in args.uid]
    else:
        criteria = _build_search_criteria(args)
        imap = _imap_connect(cfg)
        uids = _imap_search(imap, criteria, use_uid=True)
        imap.logout()
        if not uids:
            info("No matching emails found.")
            output_json([])
            return
        if args.last and args.last > 0:
            uids = uids[-args.last:]

    imap = _imap_connect(cfg)
    results = []
    for uid in uids:
        status, data = imap.uid("FETCH", uid, "(RFC822)")
        if status != "OK":
            continue
        msg = emaillib.message_from_bytes(data[0][1])

        subject = _decode_subject(msg.get("Subject", ""))
        date_str = msg.get("Date", "")
        from_addr = msg.get("From", "")

        text_body = ""
        html_body = ""
        attachments = []
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                filename = part.get_filename() or "unnamed"
                if filename:
                    decoded_parts = decode_header(filename)
                    filename = ""
                    for fb, ch in decoded_parts:
                        if isinstance(fb, bytes):
                            filename += fb.decode(ch or "utf-8", errors="replace")
                        else:
                            filename += fb
                attachments.append(filename)
                continue
            if ct == "text/plain" and not text_body:
                payload = part.get_payload(decode=True)
                if payload:
                    text_body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            elif ct == "text/html" and not html_body:
                payload = part.get_payload(decode=True)
                if payload:
                    html_body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")

        result = {
            "uid": uid.decode(),
            "subject": subject,
            "from": from_addr,
            "date": date_str,
        }
        if args.html:
            result["body"] = html_body or text_body
            result["content_type"] = "html" if html_body else "text"
        else:
            result["body"] = text_body or html_body
            result["content_type"] = "text" if text_body else "html"
        if attachments:
            result["attachments"] = attachments

        results.append(result)

    imap.logout()
    output_json(results)


def cmd_download(args):
    """下载邮件附件。"""
    cfg = load_account(_resolve_config_path(args.config), args.account)

    if args.uid:
        uids = [u.encode() for u in args.uid]
    else:
        criteria = _build_search_criteria(args)
        imap = _imap_connect(cfg)
        uids = _imap_search(imap, criteria, use_uid=True)
        imap.logout()
        if not uids:
            info("No matching emails found.")
            output_json({"downloaded": []})
            return
        if args.last and args.last > 0:
            uids = uids[-args.last:]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    imap = _imap_connect(cfg)
    downloaded = []

    for uid in uids:
        status, data = imap.uid("FETCH", uid, "(RFC822)")
        if status != "OK":
            continue
        msg = emaillib.message_from_bytes(data[0][1])
        subject = _decode_subject(msg.get("Subject", ""))

        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" not in disp:
                continue

            filename = part.get_filename() or "unnamed"
            decoded_parts = decode_header(filename)
            filename = ""
            for fb, ch in decoded_parts:
                if isinstance(fb, bytes):
                    filename += fb.decode(ch or "utf-8", errors="replace")
                else:
                    filename += fb

            if args.ext:
                exts = [e.strip(".").lower() for e in args.ext.split(",")]
                if not any(filename.lower().endswith(f".{e}") for e in exts):
                    continue

            save_path = output_dir / filename
            if save_path.exists():
                stem = save_path.stem
                suffix = save_path.suffix
                counter = 1
                while save_path.exists():
                    save_path = output_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            payload = part.get_payload(decode=True)
            if payload:
                save_path.write_bytes(payload)
                info(f"Saved: {save_path} ({len(payload)} bytes) from [{subject}]")
                downloaded.append({
                    "file": str(save_path),
                    "filename": filename,
                    "size": len(payload),
                    "email_subject": subject,
                    "email_uid": uid.decode(),
                })

    imap.logout()
    info(f"Downloaded {len(downloaded)} attachment(s).")
    output_json({"downloaded": downloaded})


def cmd_move(args):
    """移动邮件到指定文件夹。"""
    cfg = load_account(_resolve_config_path(args.config), args.account)

    if not args.subject and not args.sender:
        die("At least --subject or --sender is required.")

    target_folder = args.target_folder

    imap = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
    imap.login(cfg["user"], cfg["password"])
    imap_folder_name = _resolve_folder(imap, target_folder)

    imap.select("INBOX")
    criteria = _build_search_criteria(args)
    uid_list = _imap_search(imap, criteria, use_uid=True)

    if not uid_list:
        imap.logout()
        info("No matching emails found.")
        output_json({"moved": 0, "failed": 0, "subjects": []})
        return

    if args.last and args.last > 0:
        uid_list = uid_list[-args.last:]

    subjects = _fetch_subjects(imap, uid_list)
    imap.logout()

    info(f"Found {len(uid_list)} matching email(s):")
    for i, subj in enumerate(subjects):
        info(f"  {i+1}. {subj}")

    if not args.yes:
        info("Dry run mode. Add --yes to execute the move.")
        output_json({"moved": 0, "failed": 0, "subjects": subjects, "dry_run": True})
        return

    info(f"Moving {len(uid_list)} email(s) to '{target_folder}'...")

    imap = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
    imap.login(cfg["user"], cfg["password"])
    imap.select("INBOX")

    _status, caps = imap.capability()
    has_move = caps and b"MOVE" in caps[0].upper().split()

    moved = 0
    failed = 0
    for uid in uid_list:
        try:
            if has_move:
                status, _ = imap.uid("MOVE", uid, imap_folder_name)
            else:
                status, _ = imap.uid("COPY", uid, imap_folder_name)
                if status == "OK":
                    imap.uid("STORE", uid, "+FLAGS", "\\Deleted")
                else:
                    failed += 1
                    continue
            if status == "OK":
                moved += 1
            else:
                failed += 1
        except Exception as e:
            info(f"Failed to move UID {uid}: {e}")
            failed += 1

    if not has_move and moved > 0:
        imap.expunge()

    imap.logout()
    info(f"Done: {moved} moved, {failed} failed.")
    output_json({"moved": moved, "failed": failed, "subjects": subjects})


def cmd_search_links(args):
    """从邮件 HTML 提取链接。"""
    cfg = load_account(_resolve_config_path(args.config), args.account)
    criteria = _build_search_criteria(args)

    imap = _imap_connect(cfg)
    msg_ids = _imap_search(imap, criteria, use_uid=False)

    if not msg_ids:
        imap.logout()
        info("No matching emails found.")
        output_json([])
        return

    invoice_re = re.compile(r"\d{17,20}")
    all_results = []

    for msg_id in msg_ids:
        status, data = imap.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue
        msg = emaillib.message_from_bytes(data[0][1])

        subject = _decode_subject(msg.get("Subject", ""))
        date_str = msg.get("Date", "")

        html = ""
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                break

        if not html:
            continue

        links = re.findall(r'href=["\']([^"\']+)["\']', html)
        pdf_links = []
        xml_links = []
        other_links = []
        for link in links:
            link = link.replace("&amp;", "&")
            lower = link.lower()
            if ".pdf" in lower or "downloadpdf" in lower:
                pdf_links.append(link)
            elif ".xml" in lower or "downloadxml" in lower:
                xml_links.append(link)
            elif any(kw in lower for kw in ["invoice", "fapiao", "einvoice", "fp.", "download"]):
                other_links.append(link)

        inv_nums = invoice_re.findall(subject)
        for link in pdf_links + xml_links:
            inv_nums.extend(invoice_re.findall(link))
        inv_nums = list(dict.fromkeys(inv_nums))

        result = {
            "subject": subject,
            "date": date_str,
            "invoice_numbers": inv_nums,
            "pdf_links": pdf_links,
            "xml_links": xml_links,
        }
        if other_links:
            result["other_invoice_links"] = other_links
        all_results.append(result)

    imap.logout()
    output_json(all_results)


# ============================================================
# argparse
# ============================================================

def build_parser():
    parser = argparse.ArgumentParser(
        prog="email_tool",
        description="邮件处理 CLI 工具（基于 IMAP）",
    )
    parser.add_argument("--account", default=DEFAULT_ACCOUNT,
                        help=f"账户名（配置文件中的 key，默认: {DEFAULT_ACCOUNT}）")
    parser.add_argument("--config", default=None,
                        help="配置文件路径（默认使用 agent_config.toml 发现策略）")

    sub = parser.add_subparsers(dest="command")

    # init
    sub.add_parser("init", help="初始化配置（交互式创建账户）")

    # folders
    sub.add_parser("folders", help="列出所有文件夹")

    # list
    p = sub.add_parser("list", help="列出邮件")
    p.add_argument("--subject", help="主题关键词")
    p.add_argument("--sender", help="发件人")
    p.add_argument("--since", help="起始日期 (DD-Mon-YYYY, e.g. 01-Jan-2026)")
    p.add_argument("--before", help="截止日期")
    p.add_argument("--folder", help="搜索文件夹（默认 INBOX）")
    p.add_argument("--last", type=int, help="只显示最新 N 封")

    # read
    p = sub.add_parser("read", help="读取邮件内容")
    p.add_argument("--uid", nargs="+", help="邮件 UID（可多个）")
    p.add_argument("--subject", help="主题关键词")
    p.add_argument("--sender", help="发件人")
    p.add_argument("--since", help="起始日期")
    p.add_argument("--before", help="截止日期")
    p.add_argument("--last", type=int, help="只读最新 N 封")
    p.add_argument("--html", action="store_true", help="优先返回 HTML 内容")

    # download
    p = sub.add_parser("download", help="下载邮件附件")
    p.add_argument("--uid", nargs="+", help="邮件 UID（可多个）")
    p.add_argument("--subject", help="主题关键词")
    p.add_argument("--sender", help="发件人")
    p.add_argument("--since", help="起始日期")
    p.add_argument("--before", help="截止日期")
    p.add_argument("--last", type=int, help="只处理最新 N 封")
    p.add_argument("--output-dir", default=".", help="附件保存目录（默认当前目录）")
    p.add_argument("--ext", help="只下载指定扩展名（逗号分隔，如 pdf,xml）")

    # move
    p = sub.add_parser("move", help="移动邮件到指定文件夹")
    p.add_argument("--subject", help="主题关键词")
    p.add_argument("--sender", help="发件人")
    p.add_argument("--since", help="起始日期")
    p.add_argument("--before", help="截止日期")
    p.add_argument("--target-folder", required=True, help="目标文件夹名称（中文）")
    p.add_argument("--last", type=int, help="只移动最新 N 封")
    p.add_argument("--yes", action="store_true", help="确认执行（否则仅列出）")

    # search-links
    p = sub.add_parser("search-links", help="从邮件 HTML 提取链接")
    p.add_argument("--subject", help="主题关键词")
    p.add_argument("--sender", help="发件人")
    p.add_argument("--since", help="起始日期")
    p.add_argument("--before", help="截止日期")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "init": cmd_init,
        "folders": cmd_folders,
        "list": cmd_list,
        "read": cmd_read,
        "download": cmd_download,
        "move": cmd_move,
        "search-links": cmd_search_links,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        die(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
