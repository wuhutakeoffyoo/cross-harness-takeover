#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""takeover.py — 跨 harness 历史会话发现与接管摘要(严格只读,纯 Python 标准库)

子命令:
  list    [--query 关键词] [--harness auto|all|zcode|codex|antigravity|claude|opencode|windsurf]
          [--since 7d|2026-11-01] [--limit N] [--json]
  show    --id ID [--harness auto|zcode|codex|antigravity|claude|opencode]
          [--max-chars 300] [--tail-chars 1500] [--last-n 1] [--full] [--json]
  strings --file <db/jsonl 路径> [--min-len 6] [--limit 80] [--max-bytes 52428800]
  formats                                                   # 打印本机各 harness 存储位置探测结果

用法示例(在任意 harness 中):
  python -I scripts/takeover.py list --query 关键词 --since 30d
  python -I scripts/takeover.py show --id sess_xxx
  python -I scripts/takeover.py show --id <uuid 或前8位>
  python -I scripts/takeover.py show --harness codex --id <uuid> --full
"""
import argparse
from contextlib import closing
import datetime
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


# ---------- 基础设施 ----------

def out_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def fixpath(p) -> Path:
    """兼容 Git Bash 风格 /c/Users/... 路径与 ~ 展开。"""
    s = str(p)
    m = re.match(r"^/([a-zA-Z])/(.*)$", s)
    if m and os.name == "nt":
        s = m.group(1).upper() + ":/" + m.group(2)
    return Path(os.path.expanduser(s))


HOME = Path.home()


def _env_path(var: str, default: Path) -> Path:
    val = os.environ.get(var, "").strip()
    return fixpath(val) if val else default


def app_config_dir(app_name: str) -> Path:
    """跨平台应用配置目录 (Windows %APPDATA% / macOS Application Support / Linux XDG)。"""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(HOME / "AppData" / "Roaming")))
        return base / app_name
    if sys.platform == "darwin":
        return HOME / "Library" / "Application Support" / app_name
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(HOME / ".config")))
    return base / app_name


def xdg_data_dir(app_name: str) -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", str(HOME / ".local" / "share")))
    return base / app_name


def open_ro(db: Path) -> sqlite3.Connection:
    """只读方式打开 sqlite,兼容含空格/#/中文的路径,避免锁住运行中的 harness。"""
    uri = db.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2.0)


def parse_ts(val) -> Tuple[float, str]:
    """将 epoch(秒/毫秒) 或 ISO-8601 字符串统一转换为 (epoch_seconds, 本地 'YYYY-MM-DD HH:MM')。"""
    if val is None or val == "":
        return 0.0, ""
    # 数字或纯数字字符串
    if isinstance(val, (int, float)) or (isinstance(val, str) and re.fullmatch(r"\d+(\.\d+)?", val.strip())):
        try:
            num = float(val)
            if num > 1e11:  # 毫秒
                num /= 1000.0
            dt = datetime.datetime.fromtimestamp(num)
            return num, dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return 0.0, str(val)
    # ISO 字符串(兼容 Python 3.8-3.10 不支持尾部 'Z' 的问题)
    s = str(val).strip()
    try:
        iso = s[:-1] + "+00:00" if s.endswith("Z") else s
        # 处理纳秒精度 (超过6位小数)
        iso = re.sub(r"(\.\d{6})\d+", r"\1", iso)
        dt = datetime.datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.timestamp(), dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return 0.0, s[:16].replace("T", " ")


def parse_since(spec: str) -> Optional[float]:
    """解析 --since 参数,支持相对时长(30m/12h/7d/4w)或 ISO 日期(2026-11-01)。"""
    if not spec:
        return None
    s = spec.strip()
    m = re.fullmatch(r"(\d+)\s*([mhdwMHDW])", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        mult = {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        return datetime.datetime.now().timestamp() - n * mult
    ts, _ = parse_ts(s)
    if ts > 0:
        return ts
    raise ValueError(f"无法解析 --since 参数: {spec!r} (示例: 7d, 24h, 2026-11-01)")


def trunc(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n] + f" …(+{len(s) - n}字)"


def md_cell(val) -> str:
    """转义 Markdown 表格单元格中的管道符和换行。"""
    s = str(val if val is not None else "")
    return s.replace("\r\n", " ").replace("\n", " ").replace("|", "\\|")


def looks_like_wrapper(t: str) -> bool:
    """harness 用户消息里的系统包裹段,不算真实用户输入。"""
    return bool(re.match(r"\s*<(environment_context|user_instructions|turn_context|SKU|permissions)", t or ""))


NOISE_PREFIXES = (
    "# Environment", "You are powered by", "The following skills", "Available agent types",
    "<total_tokens>", "Today's date is", "The TodoWrite tool", "Tool loaded.",
    "Contents of C:", "Contents of /", "<system-reminder>", "<env", "<think>",
    "Codebase and user instructions", "# Memory Index", "Caveat:", "<command-name>",
    "<local-command", "system-reminder", "This session is being continued",
    "File loaded.", "Tool result", "[Request interrupted",
    "The following deferred tools", "The date has changed", "Instruction files were re-read",
    "You have been invoked", "The user opened the file",
)


def is_noise(t: str) -> bool:
    """harness 注入到 user 角色里的系统噪声,不是真实用户输入。"""
    t2 = (t or "").lstrip()[:300].lower()
    if any(t2.startswith(p.lower()) for p in NOISE_PREFIXES):
        return True
    head = (t or "")[:400]
    return "<total_tokens>" in head or "tokens left</total_tokens>" in head


def strip_antigravity_user_request(text: str) -> str:
    """提取 Antigravity USER_INPUT 中 <USER_REQUEST>...</USER_REQUEST> 内的真实内容。"""
    if not text:
        return ""
    m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 去掉尾部 <ADDITIONAL_METADATA>...</ADDITIONAL_METADATA>
    cleaned = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", text, flags=re.DOTALL).strip()
    return cleaned


def _positive_int(val: str) -> int:
    n = int(val)
    if n <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return n


# ---------- ZCode (~/.zcode) ----------

def zcode_db() -> Path:
    return _env_path("ZCODE_HOME", HOME / ".zcode") / "cli" / "db" / "db.sqlite"


def zcode_list(query: str, limit: int) -> List[dict]:
    dbf = zcode_db()
    if not dbf.exists():
        return []
    with closing(open_ro(dbf)) as db:
        rows = db.execute(
            "select id, title, directory, time_created, time_updated from session "
            "where time_deleted is null order by time_updated desc"
        ).fetchall()
        hits = set()
        if query:
            q = f"%{query.lower()}%"
            try:
                for (sid,) in db.execute("select distinct session_id from input_history where lower(text) like ?", (q,)):
                    hits.add(sid)
            except sqlite3.OperationalError:
                pass
    ql = query.lower()
    out = []
    for sid, title, directory, tc, tu in rows:
        hay = f"{sid or ''} {title or ''} {directory or ''}".lower()
        if ql and (ql not in hay) and sid not in hits:
            continue
        tu_ts, tu_str = parse_ts(tu)
        _, tc_str = parse_ts(tc)
        out.append({
            "harness": "zcode", "id": sid, "title": title or "(无标题)",
            "updated": tu_str, "created": tc_str, "cwd": directory or "", "_ts": tu_ts,
        })
        if len(out) >= limit:
            break
    return out


def zcode_show(sid: str, max_chars: int, tail_chars: int):
    dbf = zcode_db()
    if not dbf.exists():
        return None
    with closing(open_ro(dbf)) as db:
        row = db.execute(
            "select id, title, directory, time_created, time_updated from session where id = ?", (sid,)
        ).fetchone()
        if not row:
            # 唯一前缀匹配兜底
            cands = db.execute(
                "select id, title, directory, time_created, time_updated from session "
                "where id like ? and time_deleted is null order by time_updated desc limit 2",
                (f"{sid}%",),
            ).fetchall()
            if len(cands) == 1:
                row = cands[0]
            else:
                return None
        real_id = row[0]
        _, tc_str = parse_ts(row[3])
        _, tu_str = parse_ts(row[4])
        meta = {
            "harness": "zcode", "id": real_id, "title": row[1] or "(无标题)",
            "cwd": row[2] or "", "created": tc_str, "updated": tu_str,
        }
        msgs = db.execute(
            "select id, time_created, data from message where session_id = ? order by sequence", (real_id,)
        ).fetchall()
        text_parts = {}  # type: Dict[str, List[str]]
        for (mid, data) in db.execute(
            "select message_id, data from part where session_id = ? order by sequence", (real_id,)
        ):
            try:
                p = json.loads(data)
            except Exception:
                continue
            if p.get("type") == "text" and p.get("text"):
                text_parts.setdefault(mid, []).append(p["text"])
    convo = []
    for mid, tc, data in msgs:
        try:
            m = json.loads(data)
        except Exception:
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        txt = "\n".join(text_parts.get(mid, [])).strip()
        if not txt:
            continue
        if role == "user" and (looks_like_wrapper(txt) or is_noise(txt)):
            continue
        _, t_str = parse_ts(tc)
        convo.append((role, t_str, txt))
    return meta, convo, max_chars, tail_chars


# ---------- Codex (~/.codex) ----------

def codex_home() -> Path:
    return _env_path("CODEX_HOME", HOME / ".codex")


def _codex_index_map() -> Dict[str, Tuple[str, str]]:
    idx = codex_home() / "session_index.jsonl"
    items = {}  # type: Dict[str, Tuple[str, str]]
    if not idx.exists():
        return items
    with idx.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            sid = o.get("id")
            if not sid:
                continue
            upd = o.get("updated_at", "")
            prev = items.get(sid)
            if not prev or (upd > prev[1]):
                items[sid] = (o.get("thread_name") or "(无标题)", upd)
    return items


def codex_list(query: str, limit: int) -> List[dict]:
    items = _codex_index_map()
    ql = query.lower()
    out = []
    if items:
        scored = []
        for sid, (name, upd) in items.items():
            ts, upd_str = parse_ts(upd)
            scored.append((ts, sid, name, upd_str))
        scored.sort(key=lambda x: x[0], reverse=True)
        for ts, sid, name, upd_str in scored:
            if ql and ql not in f"{sid} {name}".lower():
                continue
            out.append({
                "harness": "codex", "id": sid, "title": name,
                "updated": upd_str, "cwd": "", "_ts": ts,
            })
            if len(out) >= limit:
                break
        return out
    # 若 session_index.jsonl 不存在,回退扫描 sessions/ 目录
    sdir = codex_home() / "sessions"
    if not sdir.exists():
        return []
    files = sorted(sdir.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27,})\.jsonl$", f.name)
        sid = m.group(1) if m else f.stem
        ts, upd_str = parse_ts(f.stat().st_mtime)
        if ql and ql not in f"{sid} {f.name}".lower():
            continue
        out.append({
            "harness": "codex", "id": sid, "title": "(无索引标题)",
            "updated": upd_str, "cwd": "", "_ts": ts,
        })
        if len(out) >= limit:
            break
    return out


def codex_find_file(sid: str) -> Optional[Path]:
    """按 id(支持精确与唯一前缀)在 sessions/** 与 archived_sessions 中定位 rollout 文件。"""
    ch = codex_home()
    roots = [ch / "sessions", ch / "archived_sessions"]
    # 精确匹配
    for r in roots:
        if not r.exists():
            continue
        it = r.rglob(f"rollout-*-{sid}.jsonl") if r.name == "sessions" else r.glob(f"rollout-*-{sid}.jsonl")
        for f in it:
            return f
    # 唯一前缀匹配
    matches = []
    for r in roots:
        if not r.exists():
            continue
        it = r.rglob(f"rollout-*-{sid}*.jsonl") if r.name == "sessions" else r.glob(f"rollout-*-{sid}*.jsonl")
        for f in it:
            matches.append(f)
            if len(matches) > 1:
                return None
    return matches[0] if len(matches) == 1 else None


def codex_show(sid: str, max_chars: int, tail_chars: int):
    f = codex_find_file(sid)
    if not f:
        return None
    m_id = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F-]{27,})\.jsonl$", f.name)
    real_id = m_id.group(1) if m_id else sid
    idx_items = _codex_index_map()
    idx_title, idx_upd = idx_items.get(real_id, ("", ""))
    _, upd_str = parse_ts(idx_upd) if idx_upd else parse_ts(f.stat().st_mtime)
    meta = {
        "harness": "codex", "id": real_id, "title": idx_title,
        "cwd": "", "updated": upd_str, "file": str(f),
    }
    convo = []
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            p = o.get("payload") or {}
            if t == "session_meta":
                meta["cwd"] = p.get("cwd", "")
                _, c_str = parse_ts(p.get("timestamp", ""))
                meta["created"] = c_str
                meta["originator"] = p.get("originator", "")
                if p.get("id"):
                    meta["id"] = p["id"]
            elif t == "response_item" and p.get("type") == "message":
                role = p.get("role")
                if role not in ("user", "assistant"):
                    continue
                txt = "\n".join(
                    c.get("text", "") for c in p.get("content", [])
                    if isinstance(c, dict) and c.get("type") in ("input_text", "output_text")
                ).strip()
                if not txt or (role == "user" and (looks_like_wrapper(txt) or is_noise(txt))):
                    continue
                _, t_str = parse_ts(o.get("timestamp", ""))
                convo.append((role, t_str, txt))
    if not meta["title"]:
        for role, _, txt in convo:
            if role == "user":
                meta["title"] = trunc(txt, 60)
                break
        if not meta["title"]:
            meta["title"] = "(无标题)"
    return meta, convo, max_chars, tail_chars


# ---------- Antigravity (~/.gemini/antigravity) ----------

def antigravity_home() -> Path:
    return _env_path("ANTIGRAVITY_HOME", HOME / ".gemini" / "antigravity")


def _antigravity_transcript_path(cid: str) -> Optional[Path]:
    logs = antigravity_home() / "brain" / cid / ".system_generated" / "logs"
    for name in ("transcript.jsonl", "transcript_full.jsonl"):
        p = logs / name
        if p.exists():
            return p
    return None


def _parse_antigravity_transcript(path: Path) -> Tuple[List[Tuple[str, str, str]], str]:
    """解析 Antigravity transcript.jsonl,返回 (convo, created_str)。"""
    convo = []
    created = ""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            step_type = o.get("type")
            content = o.get("content")
            if not content or not isinstance(content, str):
                continue
            _, t_str = parse_ts(o.get("created_at", ""))
            if not created and t_str:
                created = t_str
            if step_type == "USER_INPUT":
                txt = strip_antigravity_user_request(content)
                if txt and not looks_like_wrapper(txt) and not is_noise(txt):
                    convo.append(("user", t_str, txt))
            elif step_type == "PLANNER_RESPONSE":
                txt = content.strip()
                if txt:
                    convo.append(("assistant", t_str, txt))
    return convo, created


def antigravity_list(query: str, limit: int) -> List[dict]:
    ah = antigravity_home()
    dbf = ah / "conversation_summaries.db"
    ql = query.lower()
    out = []
    if dbf.exists():
        with closing(open_ro(dbf)) as db:
            rows = db.execute(
                "select conversation_id, title, preview, last_modified_time, workspace_uris, status, step_count "
                "from conversation_summaries order by last_modified_time desc"
            ).fetchall()
        for cid, title, preview, lmt, wu, status, steps in rows:
            hay = f"{cid or ''} {title or ''} {preview or ''} {wu or ''}".lower()
            if ql and ql not in hay:
                continue
            ts, upd_str = parse_ts(lmt)
            out.append({
                "harness": "antigravity", "id": cid, "title": title or "(无标题)",
                "updated": upd_str, "cwd": wu or "", "status": status, "steps": steps, "_ts": ts,
            })
            if len(out) >= limit:
                break
        return out
    # 无 summary db 时回退扫描 brain/<cid>/.system_generated/logs/transcript.jsonl
    brain = ah / "brain"
    if not brain.exists():
        return []
    cands = []
    for d in brain.iterdir():
        if not d.is_dir():
            continue
        tp = _antigravity_transcript_path(d.name)
        if tp:
            cands.append((tp.stat().st_mtime, d.name, tp))
    cands.sort(key=lambda x: x[0], reverse=True)
    for mtime, cid, _ in cands:
        if ql and ql not in cid.lower():
            continue
        ts, upd_str = parse_ts(mtime)
        out.append({
            "harness": "antigravity", "id": cid, "title": "(transcript)",
            "updated": upd_str, "cwd": "", "_ts": ts,
        })
        if len(out) >= limit:
            break
    return out


def antigravity_show(cid: str, max_chars: int, tail_chars: int):
    ah = antigravity_home()
    dbf = ah / "conversation_summaries.db"
    meta = None
    real_id = cid
    if dbf.exists():
        with closing(open_ro(dbf)) as db:
            row = db.execute(
                "select conversation_id, title, preview, last_modified_time, workspace_uris, status, step_count "
                "from conversation_summaries where conversation_id = ?", (cid,)
            ).fetchone()
            if not row:
                cands = db.execute(
                    "select conversation_id, title, preview, last_modified_time, workspace_uris, status, step_count "
                    "from conversation_summaries where conversation_id like ? "
                    "order by last_modified_time desc limit 2",
                    (f"{cid}%",),
                ).fetchall()
                if len(cands) == 1:
                    row = cands[0]
        if row:
            real_id = row[0]
            _, upd_str = parse_ts(row[3])
            meta = {
                "harness": "antigravity", "id": real_id, "title": row[1] or "(无标题)",
                "preview": row[2] or "", "updated": upd_str,
                "cwd": row[4] or "", "status": row[5], "steps": row[6],
            }
    # 尝试匹配 brain/<cid> 前缀(即使 summary db 中没有)
    tp = _antigravity_transcript_path(real_id)
    if tp is None and meta is None:
        brain = ah / "brain"
        if brain.exists():
            matches = [d.name for d in brain.iterdir() if d.is_dir() and d.name.startswith(cid)]
            if len(matches) == 1:
                real_id = matches[0]
                tp = _antigravity_transcript_path(real_id)
    if meta is None and tp is None:
        return None
    if meta is None:
        _, upd_str = parse_ts(tp.stat().st_mtime)
        meta = {"harness": "antigravity", "id": real_id, "title": "(无标题)", "updated": upd_str, "cwd": ""}

    convo = []  # type: List[Tuple[str, str, str]]
    if tp is not None:
        convo, created = _parse_antigravity_transcript(tp)
        meta["file"] = str(tp)
        if created:
            meta["created"] = created
        if meta["title"] == "(无标题)":
            for role, _, txt in convo:
                if role == "user":
                    meta["title"] = trunc(txt, 60)
                    break

    conv_db = ah / "conversations" / f"{real_id}.db"
    if conv_db.exists():
        meta["conversation_db"] = str(conv_db)
    if not convo:
        meta["note"] = "未找到 transcript.jsonl(旧会话正文为 protobuf);深挖请用 strings 子命令或回 Antigravity 原会话"
    return meta, convo, max_chars, tail_chars


# ---------- Claude Code (~/.claude) ----------

def claude_home() -> Path:
    return _env_path("CLAUDE_CONFIG_DIR", HOME / ".claude")


def _extract_claude_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text" and c.get("text"):
                parts.append(str(c["text"]))
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts).strip()
    return ""


def _claude_peek_meta(path: Path) -> Tuple[str, str, str]:
    """快速读取 Claude Code 会话 JSONL 前几十行获取 (cwd, title, session_id)。"""
    cwd, title, sid = "", "", path.stem
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 60:
                    break
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if not cwd and o.get("cwd"):
                    cwd = str(o["cwd"])
                if o.get("sessionId"):
                    sid = str(o["sessionId"])
                if not title and o.get("type") == "user":
                    msg = o.get("message") or {}
                    txt = _extract_claude_text(msg.get("content", ""))
                    if txt and not looks_like_wrapper(txt) and not is_noise(txt):
                        title = trunc(txt, 60)
                if cwd and title:
                    break
    except Exception:
        pass
    return cwd, title or "(无标题)", sid


def claude_list(query: str, limit: int) -> List[dict]:
    pdir = claude_home() / "projects"
    if not pdir.exists():
        return []
    files = []
    for f in pdir.rglob("*.jsonl"):
        if f.name.startswith("agent-"):
            continue
        try:
            files.append((f.stat().st_mtime, f))
        except OSError:
            continue
    files.sort(key=lambda x: x[0], reverse=True)
    ql = query.lower()
    out = []
    for mtime, f in files:
        cwd, title, sid = _claude_peek_meta(f)
        hay = f"{sid} {title} {cwd} {f.parent.name}".lower()
        if ql and ql not in hay:
            continue
        ts, upd_str = parse_ts(mtime)
        out.append({
            "harness": "claude", "id": sid, "title": title,
            "updated": upd_str, "cwd": cwd, "_ts": ts,
        })
        if len(out) >= limit:
            break
    return out


def claude_find_file(sid: str) -> Optional[Path]:
    pdir = claude_home() / "projects"
    if not pdir.exists():
        return None
    for f in pdir.rglob(f"{sid}.jsonl"):
        return f
    matches = []
    for f in pdir.rglob(f"{sid}*.jsonl"):
        if f.name.startswith("agent-"):
            continue
        matches.append(f)
        if len(matches) > 1:
            return None
    return matches[0] if len(matches) == 1 else None


def claude_show(sid: str, max_chars: int, tail_chars: int):
    f = claude_find_file(sid)
    if not f:
        return None
    _, upd_str = parse_ts(f.stat().st_mtime)
    meta = {
        "harness": "claude", "id": f.stem, "title": "",
        "cwd": "", "updated": upd_str, "file": str(f),
    }
    convo = []
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if not meta["cwd"] and o.get("cwd"):
                meta["cwd"] = str(o["cwd"])
            if o.get("sessionId"):
                meta["id"] = str(o["sessionId"])
            t = o.get("type")
            if t not in ("user", "assistant"):
                continue
            if o.get("isMeta"):
                continue
            msg = o.get("message") or {}
            role = msg.get("role") or t
            if role not in ("user", "assistant"):
                continue
            txt = _extract_claude_text(msg.get("content", ""))
            if not txt:
                continue
            if role == "user" and (looks_like_wrapper(txt) or is_noise(txt)):
                continue
            _, t_str = parse_ts(o.get("timestamp", ""))
            if not meta.get("created") and t_str:
                meta["created"] = t_str
            convo.append((role, t_str, txt))
    for role, _, txt in convo:
        if role == "user":
            meta["title"] = trunc(txt, 60)
            break
    if not meta["title"]:
        meta["title"] = "(无标题)"
    return meta, convo, max_chars, tail_chars


# ---------- OpenCode (~/.local/share/opencode) ----------

def opencode_storage() -> Path:
    return _env_path("OPENCODE_DATA_DIR", xdg_data_dir("opencode")) / "storage"


def opencode_list(query: str, limit: int) -> List[dict]:
    sdir = opencode_storage() / "session"
    if not sdir.exists():
        return []
    ql = query.lower()
    rows = []
    for f in sdir.rglob("*.json"):
        try:
            o = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        sid = o.get("id") or f.stem
        title = o.get("title") or "(无标题)"
        cwd = o.get("directory") or o.get("cwd") or ""
        tinfo = o.get("time") or {}
        tu = tinfo.get("updated") or tinfo.get("created") or f.stat().st_mtime
        tc = tinfo.get("created") or tu
        ts, upd_str = parse_ts(tu)
        _, cre_str = parse_ts(tc)
        if ql and ql not in f"{sid} {title} {cwd}".lower():
            continue
        rows.append({
            "harness": "opencode", "id": sid, "title": title,
            "updated": upd_str, "created": cre_str, "cwd": cwd, "_ts": ts,
        })
    rows.sort(key=lambda r: r["_ts"], reverse=True)
    return rows[:limit]


def opencode_find_session(sid: str) -> Optional[Path]:
    sdir = opencode_storage() / "session"
    if not sdir.exists():
        return None
    for f in sdir.rglob(f"{sid}.json"):
        return f
    matches = []
    for f in sdir.rglob(f"{sid}*.json"):
        matches.append(f)
        if len(matches) > 1:
            return None
    return matches[0] if len(matches) == 1 else None


def opencode_show(sid: str, max_chars: int, tail_chars: int):
    sf = opencode_find_session(sid)
    if not sf:
        return None
    try:
        o = json.loads(sf.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    real_id = o.get("id") or sf.stem
    tinfo = o.get("time") or {}
    _, cre_str = parse_ts(tinfo.get("created"))
    _, upd_str = parse_ts(tinfo.get("updated") or sf.stat().st_mtime)
    meta = {
        "harness": "opencode", "id": real_id, "title": o.get("title") or "(无标题)",
        "cwd": o.get("directory") or o.get("cwd") or "",
        "created": cre_str, "updated": upd_str, "file": str(sf),
    }
    mdir = opencode_storage() / "message" / real_id
    pdir = opencode_storage() / "part"
    msgs = []
    if mdir.exists():
        for mf in mdir.glob("*.json"):
            try:
                m = json.loads(mf.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            mt = (m.get("time") or {}).get("created") or mf.stat().st_mtime
            ts, t_str = parse_ts(mt)
            msgs.append((ts, t_str, m))
    msgs.sort(key=lambda x: x[0])
    convo = []
    for _, t_str, m in msgs:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        mid = m.get("id", "")
        parts = []
        mpdir = pdir / mid
        if mpdir.exists():
            for pf in sorted(mpdir.glob("*.json")):
                try:
                    p = json.loads(pf.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    continue
                if p.get("type") == "text" and p.get("text"):
                    parts.append(str(p["text"]))
        elif isinstance(m.get("text"), str):
            parts.append(m["text"])
        txt = "\n".join(parts).strip()
        if not txt:
            continue
        if role == "user" and (looks_like_wrapper(txt) or is_noise(txt)):
            continue
        convo.append((role, t_str, txt))
    return meta, convo, max_chars, tail_chars


# ---------- DSH / DeepSeek Harness (~/.dsh) ----------

def dsh_home() -> Path:
    return _env_path("DSH_HOME", HOME / ".dsh")


def _read_zstd_text(path: Path) -> Optional[str]:
    """只读解压 .zstd 文本: 依次尝试 Python 3.14 stdlib / zstandard / pyzstd / zstd CLI / node zlib。"""
    raw = path.read_bytes()
    try:
        from compression import zstd as std_zstd  # type: ignore  # Python 3.14+
        return std_zstd.decompress(raw).decode("utf-8", errors="replace")
    except Exception:
        pass
    try:
        import zstandard  # type: ignore
        return zstandard.ZstdDecompressor().decompress(raw).decode("utf-8", errors="replace")
    except Exception:
        pass
    try:
        import pyzstd  # type: ignore
        return pyzstd.decompress(raw).decode("utf-8", errors="replace")
    except Exception:
        pass
    if shutil.which("zstd"):
        try:
            r = subprocess.run(["zstd", "-dc", str(path)], capture_output=True, timeout=10, check=True)
            return r.stdout.decode("utf-8", errors="replace")
        except Exception:
            pass
    if shutil.which("node"):
        try:
            code = (
                "const fs=require('fs'),zlib=require('zlib');"
                "if(!zlib.zstdDecompressSync)process.exit(2);"
                "process.stdout.write(zlib.zstdDecompressSync(fs.readFileSync(process.argv[1])));"
            )
            r = subprocess.run(["node", "-e", code, str(path)], capture_output=True, timeout=10, check=True)
            return r.stdout.decode("utf-8", errors="replace")
        except Exception:
            pass
    return None


def _dsh_projcache_info(path: Path) -> dict:
    info = {"id": path.stem, "title": "", "cwd": "", "created": 0.0, "updated": 0.0, "turns": 0, "outline": []}
    try:
        o = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return info
    rec = o.get("record") or {}
    ident = rec.get("identity") or {}
    rows = rec.get("rows") or {}
    info["created"] = ident.get("createdAt") or 0
    info["cwd"] = (rows.get("workingDirectory") or {}).get("val") or ident.get("cwd") or ""
    title_val = (rows.get("title") or {}).get("val")
    if isinstance(title_val, str) and title_val.strip():
        info["title"] = title_val.strip()
    elif isinstance(title_val, dict):
        info["title"] = str(title_val.get("text") or title_val.get("title") or "").strip()
    if not info["title"]:
        ti = (rows.get("titleInput") or {}).get("val") or {}
        if isinstance(ti, dict) and ti.get("first"):
            info["title"] = trunc(str(ti["first"]), 60)
    stats = (rows.get("sessionStats") or {}).get("val") or {}
    if isinstance(stats, dict):
        info["turns"] = stats.get("turns") or 0
    outline = (rows.get("turnOutline") or {}).get("val") or {}
    if isinstance(outline, dict) and isinstance(outline.get("turns"), list):
        info["outline"] = outline["turns"]
    info["updated"] = path.stat().st_mtime
    return info


def _dsh_extract_text(val) -> str:
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, list):
        parts = []
        for item in val:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                t = item.get("text") or item.get("content")
                if isinstance(t, str) and t.strip():
                    parts.append(t)
        return "\n".join(parts).strip()
    if isinstance(val, dict):
        for k in ("text", "content", "message", "prompt"):
            if k in val:
                s = _dsh_extract_text(val[k])
                if s:
                    return s
    return ""


def dsh_list(query: str, limit: int) -> List[dict]:
    dh = dsh_home()
    if not dh.exists():
        return []
    merged = {}  # type: Dict[str, dict]
    pc_dir = dh / "storages" / "session_projcache" / "sessions"
    if pc_dir.exists():
        for f in pc_dir.glob("*.json"):
            info = _dsh_projcache_info(f)
            sid = info["id"]
            ts, upd_str = parse_ts(info["updated"] or info["created"])
            _, cre_str = parse_ts(info["created"])
            merged[sid] = {
                "harness": "dsh", "id": sid, "title": info["title"] or "(无标题)",
                "updated": upd_str, "created": cre_str, "cwd": info["cwd"], "_ts": ts,
            }
    sdir = dh / "sessions"
    if sdir.exists():
        for f in sdir.rglob("session*.jsonl*"):
            if not f.is_file():
                continue
            sid = f.parent.name if f.parent.name.startswith("session-") else f.stem
            if sid in merged:
                mts, mstr = parse_ts(f.stat().st_mtime)
                if mts > merged[sid]["_ts"]:
                    merged[sid]["_ts"] = mts
                    merged[sid]["updated"] = mstr
                continue
            ts, upd_str = parse_ts(f.stat().st_mtime)
            merged[sid] = {
                "harness": "dsh", "id": sid, "title": "(无标题)",
                "updated": upd_str, "cwd": "", "_ts": ts,
            }
    ql = query.lower()
    rows = []
    for item in merged.values():
        hay = f"{item['id']} {item['title']} {item['cwd']}".lower()
        if ql and ql not in hay:
            continue
        rows.append(item)
    rows.sort(key=lambda r: r["_ts"], reverse=True)
    return rows[:limit]


def _dsh_find_files(sid: str) -> Tuple[Optional[str], Optional[Path], Optional[Path]]:
    """定位 DSH 的 (real_id, projcache_json, session_jsonl_or_zstd),支持精确/去前缀/唯一前缀。"""
    dh = dsh_home()
    pc_dir = dh / "storages" / "session_projcache" / "sessions"
    sdir = dh / "sessions"
    needles = [sid] if sid.startswith("session-") else [sid, f"session-{sid}"]

    # 1. 精确匹配
    for n in needles:
        pc = pc_dir / f"{n}.json" if pc_dir.exists() else None
        pc = pc if (pc and pc.exists()) else None
        sf = None
        if sdir.exists():
            for cand in sdir.rglob(f"{n}/session*.jsonl*"):
                if cand.is_file():
                    sf = cand
                    break
        if pc or sf:
            return n, pc, sf

    # 2. 唯一前缀匹配
    candidates = {}  # type: Dict[str, List[Optional[Path]]]
    if pc_dir.exists():
        for f in pc_dir.glob("*.json"):
            if any(f.stem.startswith(n) for n in needles):
                candidates.setdefault(f.stem, [None, None])[0] = f
    if sdir.exists():
        for d in sdir.rglob("session-*"):
            if d.is_dir() and any(d.name.startswith(n) for n in needles):
                for cand in d.glob("session*.jsonl*"):
                    if cand.is_file():
                        candidates.setdefault(d.name, [None, None])[1] = cand
                        break
    if len(candidates) == 1:
        real_id, pair = next(iter(candidates.items()))
        return real_id, pair[0], pair[1]
    return None, None, None


def dsh_show(sid: str, max_chars: int, tail_chars: int):
    dh = dsh_home()
    if not dh.exists():
        return None
    real_id, pc_file, sf = _dsh_find_files(sid)
    if not real_id:
        return None
    meta = {"harness": "dsh", "id": real_id, "title": "", "cwd": "", "updated": ""}
    outline_turns = []
    if pc_file:
        info = _dsh_projcache_info(pc_file)
        meta["title"] = info["title"]
        meta["cwd"] = info["cwd"]
        _, cre_str = parse_ts(info["created"])
        _, upd_str = parse_ts(info["updated"])
        if cre_str:
            meta["created"] = cre_str
        meta["updated"] = upd_str
        outline_turns = info["outline"]

    convo = []  # type: List[Tuple[str, str, str]]
    if sf:
        meta["file"] = str(sf)
        if not meta["updated"]:
            _, meta["updated"] = parse_ts(sf.stat().st_mtime)
        raw_text = _read_zstd_text(sf) if sf.name.endswith(".zstd") else sf.read_text(encoding="utf-8", errors="replace")
        if raw_text is None:
            meta["note"] = "正文为 .zstd 压缩且本机缺少 zstd/node 解压工具;已回退展示 session_projcache 摘要"
        else:
            for line in raw_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                etype = str(o.get("type") or "")
                data = o.get("data") if isinstance(o.get("data"), dict) else {}
                _, t_str = parse_ts(o.get("time") or o.get("timestamp") or o.get("createdAt"))
                if etype == "session":
                    if not meta["cwd"] and o.get("cwd"):
                        meta["cwd"] = str(o["cwd"])
                    if not meta.get("created") and t_str:
                        meta["created"] = t_str
                    if o.get("agentPreset"):
                        meta["originator"] = str(o["agentPreset"])
                    continue
                role = o.get("role") or data.get("role")
                if not role:
                    el = etype.lower()
                    if el in ("user", "user/message", "turn/user", "input/user"):
                        role = "user"
                    elif el in ("assistant", "assistant/message", "turn/assistant", "llm/response"):
                        role = "assistant"
                if role not in ("user", "assistant"):
                    continue
                txt = _dsh_extract_text(data if data else o)
                if not txt:
                    continue
                if role == "user" and (looks_like_wrapper(txt) or is_noise(txt)):
                    continue
                convo.append((role, t_str, txt))

    if not convo and outline_turns:
        for item in outline_turns:
            txt = _dsh_extract_text(item)
            if txt and not looks_like_wrapper(txt) and not is_noise(txt):
                convo.append(("user", meta.get("updated", ""), txt))

    if not meta["title"]:
        for role, _, txt in convo:
            if role == "user":
                meta["title"] = trunc(txt, 60)
                break
        if not meta["title"]:
            meta["title"] = "(无标题)"
    return meta, convo, max_chars, tail_chars


# ---------- Windsurf (探测级) ----------

def windsurf_db_dir() -> Path:
    return _env_path("WINDSURF_HOME", HOME / ".codeium" / "windsurf") / "database"


def windsurf_list(query: str, limit: int) -> List[dict]:
    d = windsurf_db_dir()
    if not d.exists():
        return []
    ql = query.lower()
    out = []
    for f in sorted(d.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if ql and ql not in f"{f.name} {f}".lower():
            continue
        st = f.stat()
        ts, upd_str = parse_ts(st.st_mtime)
        out.append({
            "harness": "windsurf", "id": f.name,
            "title": f"(数据库文件 {st.st_size // 1024}KB)",
            "updated": upd_str, "cwd": str(f), "_ts": ts,
        })
        if len(out) >= limit:
            break
    return out


# ---------- Adapter 注册表 (扩展新 harness 只需在此注册) ----------

LIST_ADAPTERS = {
    "zcode": zcode_list,
    "codex": codex_list,
    "antigravity": antigravity_list,
    "claude": claude_list,
    "opencode": opencode_list,
    "dsh": dsh_list,
    "windsurf": windsurf_list,
}  # type: Dict[str, Callable[[str, int], List[dict]]]

SHOW_ADAPTERS = {
    "zcode": zcode_show,
    "codex": codex_show,
    "antigravity": antigravity_show,
    "claude": claude_show,
    "opencode": opencode_show,
    "dsh": dsh_show,
}  # type: Dict[str, Callable[[str, int, int], object]]


def auto_show(sid: str, max_chars: int, tail_chars: int):
    """按 ID 特征顺序自动探测所属 harness,找到唯一匹配即返回。"""
    if sid.startswith("sess_"):
        order = ["zcode", "codex", "antigravity", "claude", "opencode", "dsh"]
    elif sid.startswith("session-"):
        order = ["dsh", "zcode", "codex", "antigravity", "claude", "opencode"]
    elif sid.startswith("ses_"):
        order = ["opencode", "zcode", "codex", "antigravity", "claude", "dsh"]
    else:
        order = ["codex", "antigravity", "claude", "dsh", "zcode", "opencode"]
    hits = []
    for h in order:
        try:
            res = SHOW_ADAPTERS[h](sid, max_chars, tail_chars)
        except Exception:
            continue
        if res is not None:
            meta = res[0]
            # 精确 ID 匹配直接胜出
            if meta.get("id") == sid:
                return res
            hits.append(res)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        desc = ", ".join(f"{r[0]['harness']}:{r[0]['id']}" for r in hits)
        raise ValueError(f"ID 前缀 {sid!r} 命中多个会话({desc}),请补充 --harness 或更长 ID")
    return None


# ---------- 摘要渲染 ----------

def render_digest(found, last_n: int = 1) -> Optional[str]:
    if found is None:
        return None
    meta, convo, max_chars, tail_chars = found
    script_hint = f"python -I {Path(__file__).name}"
    L = []
    L.append(f"# 会话接管摘要 — {meta['title']}")
    L.append("")
    L.append("| 字段 | 值 |")
    L.append("|---|---|")
    for k in ("harness", "id", "cwd", "created", "updated", "status", "steps", "originator", "file", "conversation_db"):
        if meta.get(k) not in (None, ""):
            L.append(f"| {k} | {md_cell(meta[k])} |")
    if meta.get("note"):
        L.append("")
        L.append(f"> ⚠ {meta['note']}")
    users = [(t, x) for r, t, x in convo if r == "user"]
    assists = [(t, x) for r, t, x in convo if r == "assistant"]
    if users:
        L.append("")
        L.append(f"## 用户需求轨迹({len(users)} 条真实用户输入)")
        for i, (t, x) in enumerate(users, 1):
            L.append(f"{i}. [{t}] {trunc(x, max_chars)}")
    if assists:
        take = min(max(1, last_n), len(assists))
        L.append("")
        L.append(f"## 最后进展(共 {len(assists)} 条回复,取最后 {take} 条)")
        for idx, (t, x) in enumerate(assists[-take:], len(assists) - take + 1):
            if take > 1:
                L.append(f"### 回复 #{idx} [{t}]")
            L.append(trunc(x, tail_chars))
    if not convo and meta.get("preview"):
        L.append("")
        L.append("## 索引预览")
        L.append(trunc(meta["preview"], 500))
    L.append("")
    L.append("## 深挖方式")
    h = meta["harness"]
    if h == "zcode":
        L.append(f"- 同 harness 中: `ReadSession(sessionId=\"{meta['id']}\", view=\"messages\")` 分页读全文")
        L.append(f"- 跨 harness 中: `{script_hint} show --harness zcode --id {meta['id']} --full`")
    elif h in ("codex", "claude", "opencode", "dsh"):
        L.append(f"- `{script_hint} show --harness {h} --id {meta['id']} --full`")
        if meta.get("file"):
            L.append(f"- 或直接读源文件: `{meta['file']}`")
    elif h == "antigravity":
        if meta.get("file"):
            L.append(f"- `{script_hint} show --harness antigravity --id {meta['id']} --full`")
            L.append(f"- 或直接读 transcript: `{meta['file']}`")
        if meta.get("conversation_db"):
            L.append(f"- 二进制兜底: `{script_hint} strings --file \"{meta['conversation_db']}\"`")
    return "\n".join(L)


# ---------- strings 兜底 ----------

SQL_JUNK = re.compile(r"^(table|u?index|itable|utable|view|trigger|sqlite_|idx_|table sqlite)", re.I)


def is_identifier_junk(s: str) -> bool:
    """sqlite DDL 残留与纯标识符,不算内容。"""
    if SQL_JUNK.match(s):
        return True
    if any(k in s for k in ("CREATE TABLE", "PRIMARY KEY", "NOT NULL", "FOREIGN KEY", "REFERENCES", "CHECK (")):
        return True
    if len(s) < 40 and re.fullmatch(r"[A-Za-z0-9_.:/\\-]+", s):
        return True
    return False


def extract_strings(path, min_len: int, limit: int, max_bytes: int = 50 * 1024 * 1024) -> List[str]:
    p = fixpath(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    if p.is_dir():
        raise IsADirectoryError(f"目标是目录而非文件: {p}")
    with p.open("rb") as fh:
        raw = fh.read(max_bytes)
    text = raw.decode("utf-8", errors="ignore")
    pat = re.compile(
        r"[\u4e00-\u9fffA-Za-z0-9_\-./:\\()[\]\uff0c\u3002\uff1b\uff1a\u201c\u201d\u2018\u2019\u3001\uff01\uff1f\s]{%d,}"
        % min_len
    )
    seen, out = set(), []
    for m in pat.finditer(text):
        s = " ".join(m.group(0).split()).strip()
        if len(s) < min_len or re.fullmatch(r"[0-9a-fA-F-]{8,}", s) or is_identifier_junk(s):
            continue
        key = s[:64]
        if key in seen:
            continue
        seen.add(key)
        out.append(s[:300])
        if len(out) >= limit:
            break
    return out


# ---------- formats 探测 ----------

def get_candidates():
    return [
        ("zcode db [full]", zcode_db),
        ("zcode rollout [log]", lambda: _env_path("ZCODE_HOME", HOME / ".zcode") / "cli" / "rollout"),
        ("codex sessions [full]", lambda: codex_home() / "sessions"),
        ("codex index [full]", lambda: codex_home() / "session_index.jsonl"),
        ("codex archived [full]", lambda: codex_home() / "archived_sessions"),
        ("antigravity brain/transcripts [full]", lambda: antigravity_home() / "brain"),
        ("antigravity summaries [full]", lambda: antigravity_home() / "conversation_summaries.db"),
        ("antigravity conversations [protobuf]", lambda: antigravity_home() / "conversations"),
        ("claude-code projects [full]", lambda: claude_home() / "projects"),
        ("opencode storage [full]", opencode_storage),
        ("dsh sessions [full]", lambda: dsh_home() / "sessions"),
        ("dsh projcache [full]", lambda: dsh_home() / "storages" / "session_projcache" / "sessions"),
        ("windsurf database [probe]", windsurf_db_dir),
        ("cursor workspaceStorage [probe]", lambda: app_config_dir("Cursor") / "User" / "workspaceStorage"),
        ("vscode workspaceStorage [probe]", lambda: app_config_dir("Code") / "User" / "workspaceStorage"),
    ]


def print_formats() -> None:
    print(f"环境: {platform.system()} {platform.release()} | Python {platform.python_version()} | HOME={HOME}")
    for name, fn in get_candidates():
        try:
            p = fn()
            mark = "✓" if p.exists() else "✗"
        except Exception:
            p, mark = None, "✗"
        print(f"  {mark} {name}: {p}")


# ---------- main ----------

def main(argv=None) -> int:
    out_utf8()
    ap = argparse.ArgumentParser(description="跨 harness 会话接管(严格只读)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("list", help="枚举/检索历史会话")
    lp.add_argument("--query", default="", help="匹配标题/目录/ID/历史输入")
    lp.add_argument("--harness", default="all", choices=["all"] + list(LIST_ADAPTERS.keys()))
    lp.add_argument("--since", default="", help="仅显示该时间之后的会话(如 7d, 24h, 2026-11-01)")
    lp.add_argument("--limit", type=_positive_int, default=15)
    lp.add_argument("--json", action="store_true")

    sp = sub.add_parser("show", help="生成指定会话的标准化接管摘要")
    sp.add_argument("--harness", default="auto", choices=["auto"] + list(SHOW_ADAPTERS.keys()))
    sp.add_argument("--id", required=True, help="会话 ID(支持完整 ID 或唯一前缀)")
    sp.add_argument("--max-chars", type=_positive_int, default=300)
    sp.add_argument("--tail-chars", type=_positive_int, default=1500)
    sp.add_argument("--last-n", type=_positive_int, default=1, help="展示最后 N 条助手回复(默认 1)")
    sp.add_argument("--full", action="store_true", help="深挖快捷模式(等效 --max-chars 4000 --tail-chars 12000 --last-n 3)")
    sp.add_argument("--json", action="store_true")

    tp = sub.add_parser("strings", help="从未知/二进制文件提取可读文本兜底")
    tp.add_argument("--file", required=True)
    tp.add_argument("--min-len", type=_positive_int, default=6)
    tp.add_argument("--limit", type=_positive_int, default=80)
    tp.add_argument("--max-bytes", type=_positive_int, default=50 * 1024 * 1024)

    sub.add_parser("formats", help="探测本机各 harness 存储位置")
    args = ap.parse_args(argv)

    if args.cmd == "formats":
        print_formats()
        return 0

    if args.cmd == "list":
        try:
            since_ts = parse_since(args.since) if args.since else None
        except ValueError as e:
            print(f"错误: {e}", file=sys.stderr)
            return 1
        res = []
        hs = [args.harness] if args.harness != "all" else list(LIST_ADAPTERS.keys())
        # 为保证 --since 或跨 harness 合并排序准确,每个 harness 多拉一些再裁剪
        fetch_limit = max(args.limit * 2, 50) if (args.harness == "all" or since_ts) else args.limit
        for h in hs:
            try:
                items = LIST_ADAPTERS[h](args.query, fetch_limit)
                if since_ts is not None:
                    items = [it for it in items if it.get("_ts", 0.0) >= since_ts]
                res.extend(items)
            except Exception as e:
                res.append({"harness": h, "error": str(e), "_ts": -1.0})
        res.sort(key=lambda r: r.get("_ts", 0.0), reverse=True)
        res = res[: args.limit]
        clean_res = [{k: v for k, v in r.items() if k != "_ts"} for r in res]
        if args.json:
            print(json.dumps(clean_res, ensure_ascii=False, indent=2))
        else:
            if not clean_res:
                print("(未找到匹配的历史会话)")
            for r in clean_res:
                if "error" in r:
                    print(f"[{r['harness']}] 错误: {r['error']}")
                    continue
                extra = f" | {r['cwd']}" if r.get("cwd") else ""
                print(f"[{r['harness']}] {r['id']}  |  {r['updated']}  |  {r['title']}{extra}")
        return 0

    if args.cmd == "show":
        max_chars = 4000 if args.full else args.max_chars
        tail_chars = 12000 if args.full else args.tail_chars
        last_n = max(3, args.last_n) if args.full else args.last_n
        try:
            if args.harness == "auto":
                found = auto_show(args.id, max_chars, tail_chars)
            else:
                found = SHOW_ADAPTERS[args.harness](args.id, max_chars, tail_chars)
        except Exception as e:
            print(f"错误: {e}", file=sys.stderr)
            return 1
        if found is None:
            print(f"未找到会话 {args.id} (harness={args.harness})", file=sys.stderr)
            return 2
        if args.json:
            meta, convo, _, _ = found
            print(json.dumps(
                {"meta": meta, "convo": [{"role": r, "time": t, "text": x} for r, t, x in convo]},
                ensure_ascii=False, indent=2,
            ))
        else:
            print(render_digest(found, last_n=last_n))
        return 0

    if args.cmd == "strings":
        try:
            for s in extract_strings(args.file, args.min_len, args.limit, args.max_bytes):
                print(s)
        except Exception as e:
            print(f"错误: {e}", file=sys.stderr)
            return 1
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
