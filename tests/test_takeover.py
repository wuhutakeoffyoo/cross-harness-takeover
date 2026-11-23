import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import takeover  # noqa: E402


class TakeoverTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="takeover test #1 ")
        self.base = Path(self._tmp.name)
        self._orig_home = takeover.HOME
        self._orig_env = {
            k: os.environ.get(k)
            for k in (
                "ZCODE_HOME", "CODEX_HOME", "ANTIGRAVITY_HOME",
                "CLAUDE_CONFIG_DIR", "OPENCODE_DATA_DIR", "DSH_HOME", "WINDSURF_HOME",
            )
        }
        takeover.HOME = self.base
        os.environ["ZCODE_HOME"] = str(self.base / ".zcode")
        os.environ["CODEX_HOME"] = str(self.base / ".codex")
        os.environ["ANTIGRAVITY_HOME"] = str(self.base / ".gemini" / "antigravity")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.base / ".claude")
        os.environ["OPENCODE_DATA_DIR"] = str(self.base / ".local" / "share" / "opencode")
        os.environ["DSH_HOME"] = str(self.base / ".dsh")
        os.environ["WINDSURF_HOME"] = str(self.base / ".codeium" / "windsurf")

    def tearDown(self):
        takeover.HOME = self._orig_home
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def _seed_zcode(self):
        dbp = self.base / ".zcode" / "cli" / "db" / "db.sqlite"
        dbp.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(dbp))
        c.execute(
            "create table session(id text, title text, directory text, "
            "time_created int, time_updated int, time_deleted int)"
        )
        c.execute("create table message(id text, session_id text, sequence int, time_created int, data text)")
        c.execute("create table part(message_id text, session_id text, sequence int, data text)")
        c.execute("create table input_history(session_id text, text text, time_created int)")
        c.execute(
            "insert into session values (?, ?, ?, ?, ?, NULL)",
            ("sess_abc12345", "Fix | Pipe Title", "/proj/zcode", 1763600000000, 1763603600000),
        )
        c.execute(
            "insert into message values (?, ?, ?, ?, ?)",
            ("m1", "sess_abc12345", 1, 1763600000000, json.dumps({"role": "user"})),
        )
        c.execute(
            "insert into message values (?, ?, ?, ?, ?)",
            ("m2", "sess_abc12345", 2, 1763600010000, json.dumps({"role": "user"})),
        )
        c.execute(
            "insert into message values (?, ?, ?, ?, ?)",
            ("m3", "sess_abc12345", 3, 1763603600000, json.dumps({"role": "assistant"})),
        )
        c.execute(
            "insert into part values (?, ?, ?, ?)",
            ("m1", "sess_abc12345", 1, json.dumps({"type": "text", "text": "<system-reminder>noise</system-reminder>"})),
        )
        c.execute(
            "insert into part values (?, ?, ?, ?)",
            ("m2", "sess_abc12345", 1, json.dumps({"type": "text", "text": "修复数据库连接池泄漏"})),
        )
        c.execute(
            "insert into part values (?, ?, ?, ?)",
            ("m3", "sess_abc12345", 1, json.dumps({"type": "text", "text": "已定位连接未关闭，补上 context manager。"})),
        )
        c.execute("insert into input_history values (?, ?, ?)", ("sess_abc12345", "连接池泄漏", 1763600010000))
        c.commit()
        c.close()

    def _seed_codex(self):
        ch = self.base / ".codex"
        sdir = ch / "sessions" / "2026" / "11" / "20"
        sdir.mkdir(parents=True, exist_ok=True)
        cid = "11111111-2222-3333-4444-555555555555"
        (ch / "session_index.jsonl").write_text(
            json.dumps({"id": cid, "thread_name": "Codex Refactor", "updated_at": "2026-11-20T01:00:00Z"}) + "\n",
            encoding="utf-8",
        )
        lines = [
            {"type": "session_meta", "payload": {"id": cid, "cwd": "/proj/codex", "timestamp": "2026-11-20T00:30:00Z", "originator": "cli"}},
            {"type": "response_item", "timestamp": "2026-11-20T00:31:00Z", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>sys</environment_context>"}]}},
            {"type": "response_item", "timestamp": "2026-11-20T00:32:00Z", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "重构 CLI 参数解析"}]}},
            {"type": "response_item", "timestamp": "2026-11-20T01:00:00Z", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "已完成 argparse 子命令拆分。"}]}},
        ]
        (sdir / f"rollout-2026-11-20T00-30-00-{cid}.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
            encoding="utf-8",
        )
        return cid

    def _seed_antigravity(self):
        ah = self.base / ".gemini" / "antigravity"
        ah.mkdir(parents=True, exist_ok=True)
        cid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        dbp = ah / "conversation_summaries.db"
        c = sqlite3.connect(str(dbp))
        c.execute(
            "create table conversation_summaries("
            "conversation_id text, title text, preview text, last_modified_time text, "
            "workspace_uris text, status text, step_count int)"
        )
        c.execute(
            "insert into conversation_summaries values (?, ?, ?, ?, ?, ?, ?)",
            (cid, "Antigravity Task", "Preview text", "2026-11-20T02:00:00Z", '["file:///proj/ag"]', "IDLE", 12),
        )
        c.commit()
        c.close()

        logs = ah / "brain" / cid / ".system_generated" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        tlines = [
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "created_at": "2026-11-20T01:50:00Z",
                "content": "<USER_REQUEST>\n优化 skill 兼容性\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\ntime\n</ADDITIONAL_METADATA>",
            },
            {
                "type": "PLANNER_RESPONSE",
                "source": "MODEL",
                "created_at": "2026-11-20T02:00:00Z",
                "content": "已升级 Antigravity 为全文解析模式。",
            },
        ]
        (logs / "transcript.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in tlines) + "\n",
            encoding="utf-8",
        )
        return cid

    def _seed_claude(self):
        pdir = self.base / ".claude" / "projects" / "my-proj"
        pdir.mkdir(parents=True, exist_ok=True)
        cid = "cccccccc-1111-2222-3333-444444444444"
        lines = [
            {"type": "user", "sessionId": cid, "cwd": "/proj/claude", "timestamp": "2026-11-20T02:10:00Z", "message": {"role": "user", "content": "添加单元测试覆盖"}},
            {"type": "assistant", "sessionId": cid, "cwd": "/proj/claude", "timestamp": "2026-11-20T02:15:00Z", "message": {"role": "assistant", "content": [{"type": "text", "text": "已添加 unittest 并全部通过。"}]}},
        ]
        (pdir / f"{cid}.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
            encoding="utf-8",
        )
        return cid

    def _seed_opencode(self):
        st = self.base / ".local" / "share" / "opencode" / "storage"
        sid = "ses_99998888"
        (st / "session" / "proj1").mkdir(parents=True, exist_ok=True)
        (st / "message" / sid).mkdir(parents=True, exist_ok=True)
        (st / "part" / "msg_u1").mkdir(parents=True, exist_ok=True)
        (st / "part" / "msg_a1").mkdir(parents=True, exist_ok=True)
        (st / "session" / "proj1" / f"{sid}.json").write_text(
            json.dumps({"id": sid, "title": "OpenCode Feature", "directory": "/proj/oc", "time": {"created": 1763604000000, "updated": 1763605000000}}),
            encoding="utf-8",
        )
        (st / "message" / sid / "msg_u1.json").write_text(
            json.dumps({"id": "msg_u1", "role": "user", "time": {"created": 1763604000000}}),
            encoding="utf-8",
        )
        (st / "message" / sid / "msg_a1.json").write_text(
            json.dumps({"id": "msg_a1", "role": "assistant", "time": {"created": 1763605000000}}),
            encoding="utf-8",
        )
        (st / "part" / "msg_u1" / "p1.json").write_text(json.dumps({"type": "text", "text": "实现导出功能"}), encoding="utf-8")
        (st / "part" / "msg_a1" / "p1.json").write_text(json.dumps({"type": "text", "text": "已实现 JSON 导出。"}), encoding="utf-8")
        return sid

    def _seed_dsh(self):
        dh = self.base / ".dsh"
        sid = "session-dddd1111-2222-3333-4444-555555555555"
        pc = dh / "storages" / "session_projcache" / "sessions"
        pc.mkdir(parents=True, exist_ok=True)
        (pc / f"{sid}.json").write_text(
            json.dumps({
                "version": 1,
                "record": {
                    "identity": {"formatVersion": 4, "createdAt": 1763606000000, "cwd": "/proj/dsh"},
                    "rows": {
                        "title": {"ver": 1, "seq": 1, "val": "DSH Session Title"},
                        "workingDirectory": {"ver": 1, "seq": 1, "val": "/proj/dsh"},
                        "sessionStats": {"ver": 1, "seq": 1, "val": {"turns": 1}},
                        "turnOutline": {"ver": 1, "seq": 1, "val": {"turns": []}},
                    },
                },
            }),
            encoding="utf-8",
        )
        sdir = dh / "sessions" / "--proj-dsh--" / sid
        sdir.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "session", "version": 4, "id": sid, "createdAt": 1763606000000, "cwd": "/proj/dsh", "agentPreset": "standard"},
            {"type": "user/message", "seq": 1, "time": 1763606010000, "data": {"role": "user", "text": "帮我接手 DSH 任务"}},
            {"type": "assistant/message", "seq": 2, "time": 1763606020000, "data": {"role": "assistant", "text": "已完成 DSH 适配验证。"}},
        ]
        (sdir / "session.v4.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
            encoding="utf-8",
        )
        return sid

    def test_missing_harnesses_do_not_error(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = takeover.main(["list", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue()), [])

    def test_all_harnesses_list_and_auto_show(self):
        self._seed_zcode()
        codex_id = self._seed_codex()
        ag_id = self._seed_antigravity()
        claude_id = self._seed_claude()
        oc_id = self._seed_opencode()
        dsh_id = self._seed_dsh()

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = takeover.main(["list", "--limit", "20", "--json"])
        self.assertEqual(rc, 0)
        items = json.loads(buf.getvalue())
        harnesses = {it["harness"] for it in items}
        self.assertEqual(harnesses, {"zcode", "codex", "antigravity", "claude", "opencode", "dsh"})

        # Auto-detect & prefix matching for every harness
        for prefix, expected_h, expected_text in [
            ("sess_abc", "zcode", "修复数据库连接池泄漏"),
            (codex_id[:8], "codex", "重构 CLI 参数解析"),
            (ag_id[:8], "antigravity", "优化 skill 兼容性"),
            (claude_id[:8], "claude", "添加单元测试覆盖"),
            (oc_id[:8], "opencode", "实现导出功能"),
            (dsh_id[:16], "dsh", "帮我接手 DSH 任务"),
        ]:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = takeover.main(["show", "--id", prefix])
            self.assertEqual(rc, 0, msg=f"failed for {expected_h}")
            txt = out.getvalue()
            self.assertIn(f"| harness | {expected_h} |", txt)
            self.assertIn(expected_text, txt)

    def test_markdown_pipe_escape_and_noise_filter(self):
        self._seed_zcode()
        found = takeover.zcode_show("sess_abc12345", 300, 1500)
        self.assertIsNotNone(found)
        meta, convo, _, _ = found
        self.assertEqual(len([1 for r, _, _ in convo if r == "user"]), 1)
        digest = takeover.render_digest(found)
        self.assertIn("Fix | Pipe Title", digest)
        self.assertNotIn("system-reminder", digest)

    def test_strings_fallback(self):
        binf = self.base / "sample.bin"
        binf.write_bytes(b"\x00\x01CREATE TABLE foo(id INT)\x00\x02\xe8\xbf\x99\xe6\x98\xaf\xe4\xb8\x80\xe6\xae\xb5\xe5\x8f\xaf\xe8\xaf\xbb\xe4\xb8\xad\xe6\x96\x87\xe6\xb5\x8b\xe8\xaf\x95\x00")
        got = takeover.extract_strings(str(binf), min_len=4, limit=10)
        self.assertTrue(any("这是一段可读中文测试" in s for s in got))
        self.assertFalse(any("CREATE TABLE" in s for s in got))

    def test_content_search_finds_body_text(self):
        self._seed_codex()
        self._seed_claude()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = takeover.main(["list", "--query", "argparse", "--content", "--json"])
        self.assertEqual(rc, 0)
        items = json.loads(buf.getvalue())
        self.assertTrue(any(
            it["harness"] == "codex" and it["id"] == "11111111-2222-3333-4444-555555555555"
            for it in items
        ), msg=items)
        # 不开启 --content 时,正文关键词搜不到 codex 会话(标题不含该词)
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            takeover.main(["list", "--query", "argparse", "--json"])
        items2 = json.loads(buf2.getvalue())
        self.assertFalse(any(it["harness"] == "codex" for it in items2))

    def test_codex_fallback_peek_title(self):
        # 无 session_index.jsonl 时,list 回退应从 rollout 头部取标题与 cwd
        ch = self.base / ".codex" / "sessions" / "2026" / "11" / "20"
        ch.mkdir(parents=True, exist_ok=True)
        cid = "99999999-8888-7777-6666-555555555555"
        lines = [
            {"type": "session_meta", "payload": {"id": cid, "cwd": "/proj/peek"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "检查回退标题"}]}},
        ]
        (ch / f"rollout-2026-11-20T00-30-00-{cid}.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
            encoding="utf-8",
        )
        items = takeover.codex_list("", 10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "检查回退标题")
        self.assertEqual(items[0]["cwd"], "/proj/peek")

    def test_parse_ts_and_since(self):
        ts_iso, s_iso = takeover.parse_ts("2026-11-20T01:00:00Z")
        self.assertGreater(ts_iso, 0)
        self.assertRegex(s_iso, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertIsNotNone(takeover.parse_since("7d"))
        with self.assertRaises(ValueError):
            takeover.parse_since("not-a-date")


if __name__ == "__main__":
    unittest.main()
