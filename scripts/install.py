#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install.py — 将 cross-harness-takeover 一键同步到本机所有已安装的 harness skill 目录。

用法:
  python -I scripts/install.py              # 自动探测本机已装 harness 并同步
  python -I scripts/install.py --all        # 强制安装到全部标准 skill 目录
  python -I scripts/install.py --dry-run    # 仅预览将安装的位置,不写入
  python -I scripts/install.py --uninstall  # 从各 harness skill 目录卸载(保留当前源码目录)
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

SKILL_NAME = "cross-harness-takeover"
PAYLOAD_FILES = ("SKILL.md",)
PAYLOAD_DIRS = ("scripts", "references")


def out_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def skill_source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _devin_config_dir(home: Path) -> Path:
    """Devin 全局配置目录: Windows %APPDATA%\\devin, macOS Application Support, Linux XDG。"""
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))) / "devin"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "devin"
    return Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))) / "devin"


def get_targets(home: Path) -> List[Dict[str, object]]:
    """返回所有标准 harness skill 发现目录及其探测条件。"""
    return [
        {
            "key": "agents",
            "harnesses": "ZCode / Codex / OpenCode / Cursor",
            "dest": home / ".agents" / "skills" / SKILL_NAME,
            "probes": [
                home / ".agents",
                home / ".zcode",
                home / ".codex",
                home / ".local" / "share" / "opencode",
                home / ".config" / "opencode",
                home / ".cursor",
            ],
        },
        {
            "key": "antigravity",
            "harnesses": "Antigravity (Gemini)",
            "dest": home / ".gemini" / "config" / "skills" / SKILL_NAME,
            "probes": [
                home / ".gemini" / "antigravity",
                home / ".gemini" / "config",
            ],
        },
        {
            "key": "claude",
            "harnesses": "Claude Code",
            "dest": home / ".claude" / "skills" / SKILL_NAME,
            "probes": [
                home / ".claude",
            ],
        },
        {
            "key": "dsh",
            "harnesses": "DSH (DeepSeek Harness)",
            "dest": home / ".dsh" / "skills" / SKILL_NAME,
            "probes": [
                home / ".dsh",
            ],
        },
        {
            "key": "windsurf",
            "harnesses": "Windsurf",
            "dest": home / ".codeium" / "windsurf" / "skills" / SKILL_NAME,
            "probes": [
                home / ".codeium" / "windsurf",
            ],
        },
        {
            "key": "devin",
            "harnesses": "Devin CLI / Desktop",
            "dest": _devin_config_dir(home) / "skills" / SKILL_NAME,
            "probes": [
                home / "AppData" / "Roaming" / "devin",
                home / ".config" / "devin",
                home / "AppData" / "Local" / "Programs" / "Devin",
            ],
        },
    ]


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


def _ignore_junk(_dir: str, names: List[str]) -> List[str]:
    return [n for n in names if n in ("__pycache__", ".DS_Store", "Thumbs.db") or n.endswith((".pyc", ".pyo"))]


def copy_skill(src: Path, dest: Path) -> None:
    if not (src / "SKILL.md").exists():
        raise FileNotFoundError(f"源目录缺少 SKILL.md: {src}")
    dest.mkdir(parents=True, exist_ok=True)
    for fname in PAYLOAD_FILES:
        sf = src / fname
        if sf.exists():
            shutil.copy2(sf, dest / fname)
    for dname in PAYLOAD_DIRS:
        sd = src / dname
        if not sd.exists():
            continue
        dd = dest / dname
        if dd.exists():
            shutil.rmtree(dd)
        shutil.copytree(sd, dd, ignore=_ignore_junk)


def run_install(
    src: Path,
    home: Path,
    install_all: bool = False,
    target_keys: List[str] = None,
    dry_run: bool = False,
    uninstall: bool = False,
) -> List[Dict[str, str]]:
    targets = get_targets(home)
    key_filter = set(target_keys) if target_keys else None
    results = []

    for t in targets:
        key = str(t["key"])
        harnesses = str(t["harnesses"])
        dest = t["dest"]  # type: Path
        probes = t["probes"]  # type: List[Path]
        if key_filter and key not in key_filter:
            continue

        detected = any(p.exists() for p in probes)
        if _same_path(src, dest):
            results.append({
                "key": key, "harnesses": harnesses, "dest": str(dest),
                "action": "source", "detail": "当前源码目录本身,已就位",
            })
            continue

        if uninstall:
            if not dest.exists():
                results.append({
                    "key": key, "harnesses": harnesses, "dest": str(dest),
                    "action": "skip", "detail": "未安装",
                })
                continue
            if not dry_run:
                shutil.rmtree(dest)
            results.append({
                "key": key, "harnesses": harnesses, "dest": str(dest),
                "action": "would_remove" if dry_run else "removed",
                "detail": "将删除" if dry_run else "已卸载",
            })
            continue

        if not install_all and not key_filter and not detected:
            results.append({
                "key": key, "harnesses": harnesses, "dest": str(dest),
                "action": "skip", "detail": "未检测到对应 harness(加 --all 可强制安装)",
            })
            continue

        if not dry_run:
            copy_skill(src, dest)
        results.append({
            "key": key, "harnesses": harnesses, "dest": str(dest),
            "action": "would_install" if dry_run else "installed",
            "detail": "将安装/同步" if dry_run else "已安装/同步",
        })
    return results


def main(argv=None) -> int:
    out_utf8()
    ap = argparse.ArgumentParser(description="将 cross-harness-takeover 同步到本机所有 harness")
    ap.add_argument("--all", action="store_true", help="安装到全部标准 skill 目录,不跳过未检测到的 harness")
    ap.add_argument("--targets", default="", help="仅操作指定目标,逗号分隔: agents,antigravity,claude,dsh,windsurf,devin")
    ap.add_argument("--dry-run", action="store_true", help="仅预览操作,不修改文件系统")
    ap.add_argument("--uninstall", action="store_true", help="从已安装目录卸载(不会删除当前源码目录)")
    ap.add_argument("--json", action="store_true", help="以 JSON 格式输出结果")
    args = ap.parse_args(argv)

    target_keys = [x.strip() for x in args.targets.split(",") if x.strip()] if args.targets else None
    valid_keys = {"agents", "antigravity", "claude", "dsh", "windsurf", "devin"}
    if target_keys:
        unknown = set(target_keys) - valid_keys
        if unknown:
            print(f"错误: 未知目标 {', '.join(sorted(unknown))} (可选: {', '.join(sorted(valid_keys))})", file=sys.stderr)
            return 1

    src = skill_source_root()
    home = Path.home()
    try:
        results = run_install(
            src=src,
            home=home,
            install_all=args.all,
            target_keys=target_keys,
            dry_run=args.dry_run,
            uninstall=args.uninstall,
        )
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    icons = {
        "installed": "✓",
        "source": "✓",
        "removed": "✓",
        "would_install": "→",
        "would_remove": "→",
        "skip": "-",
    }
    print(f"源目录: {src}")
    for r in results:
        ic = icons.get(r["action"], " ")
        print(f"  {ic} [{r['key']}] {r['harnesses']}: {r['detail']} ({r['dest']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
