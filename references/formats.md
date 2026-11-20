# 各 harness 会话存储格式与接入参考

所有路径默认基于用户主目录 `~`（`Path.home()`），并支持通过环境变量覆盖以便自定义目录或测试。
所有 SQLite 数据库均通过 `Path.resolve().as_uri() + "?mode=ro"` 以**严格只读模式**打开，兼容路径中的空格、`#` 与中文字符，且在 WAL 模式下与运行中的 harness 并发读安全。

## 环境变量覆盖

| harness | 环境变量 | 默认路径 |
|---|---|---|
| ZCode | `ZCODE_HOME` | `~/.zcode` |
| Codex | `CODEX_HOME` | `~/.codex` |
| Antigravity | `ANTIGRAVITY_HOME` | `~/.gemini/antigravity` |
| Claude Code | `CLAUDE_CONFIG_DIR` | `~/.claude` |
| OpenCode | `OPENCODE_DATA_DIR` | `$XDG_DATA_HOME/opencode`（默认 `~/.local/share/opencode`） |
| DSH (DeepSeek Harness) | `DSH_HOME` | `~/.dsh` |
| Windsurf | `WINDSURF_HOME` | `~/.codeium/windsurf` |

## 1. ZCode 🟢

- 权威存储：`$ZCODE_HOME/cli/db/db.sqlite`（SQLite WAL）
  - `session`：`id`（`sess_xxx`）、`title`、`directory`、`time_created`/`time_updated`（epoch **毫秒**）、`time_deleted`（非空表示已删）
  - `message`：`id`、`session_id`、`sequence`、`data`（JSON，含 `role`）
  - `part`：`message_id`、`session_id`、`sequence`、`data`（JSON，`type == "text"` 的 `.text` 为正文）
  - `input_history`：`session_id`、`text`、`time_created`（用于 `--query` 全文检索；若表缺失则自动降级）
- 附注：`$ZCODE_HOME/cli/rollout/model-io-sess_*.jsonl` 仅为部分模型 IO 日志，不完整，勿依赖。

## 2. Codex CLI / Codex Desktop 🟢

- 索引：`$CODEX_HOME/session_index.jsonl`，每行 `{id, thread_name, updated_at}`；同一 `id` 多条记录取 `updated_at` 最大者。若索引文件缺失，`list` 自动回退扫描 `sessions/` 目录。
- 正文：`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<时间>-<id>.jsonl`；归档在 `$CODEX_HOME/archived_sessions/rollout-*.jsonl`。
- 每行 `{timestamp, ordinal, type, payload}`：
  - `type=session_meta`：`payload` 含 `id, timestamp, cwd, originator, cli_version`
  - `type=response_item` 且 `payload.type=message`：`role` ∈ `user`/`assistant`/`developer`；`content[].{type: input_text|output_text, text}`
  - 其余类型（`event_msg` / `token_usage_record` / `world_state` / `turn_context`）忽略。
- 过滤：`user` 消息中以 `<environment_context>`、`<user_instructions>`、`<turn_context>` 等开头的系统包裹段自动过滤。

## 3. Antigravity（Gemini）🟢

- 索引：`$ANTIGRAVITY_HOME/conversation_summaries.db` → `conversation_summaries` 表：
  `conversation_id, title, preview, step_count, last_modified_time(ISO), workspace_uris, status, ...`
- 正文（首选，全文明文）：`$ANTIGRAVITY_HOME/brain/<conversation_id>/.system_generated/logs/transcript.jsonl`（以及 `transcript_full.jsonl`）：
  - `type == "USER_INPUT"`：正文常包裹在 `<USER_REQUEST>...</USER_REQUEST>` 内并附带 `<ADDITIONAL_METADATA>`，脚本自动提取真实请求文本。
  - `type == "PLANNER_RESPONSE"`：非空 `content` 即助手对用户的可见回复。
- 正文兜底（旧版或无 transcript 时）：`$ANTIGRAVITY_HOME/conversations/<conversation_id>.db` 的 `steps` 表（`step_payload` 为 protobuf 二进制），可通过 `strings` 子命令捞取可读文本。

## 4. Claude Code 🟢

- 存储：`$CLAUDE_CONFIG_DIR/projects/<编码后的cwd>/<session-uuid>.jsonl`（自动跳过 `agent-*.jsonl` 子代理日志）
- 每行 JSON 含 `type`（`user`/`assistant`）、`sessionId`、`cwd`、`timestamp`、`isMeta`、`message.{role, content}`：
  - `content` 可为纯字符串或 `[{type: "text", text: "..."}]` 数组。
  - `isMeta == true` 或系统噪声消息自动跳过。

## 5. OpenCode 🟢

- 存储：`$OPENCODE_DATA_DIR/storage/`
  - 会话元数据：`storage/session/<project>/<ses_xxx>.json`（含 `id, title, directory, time.{created, updated}`）
  - 消息：`storage/message/<ses_xxx>/*.json`（含 `id, role, time.created`）
  - 文本分片：`storage/part/<msg_id>/*.json`（`type == "text"` 的 `.text`）

## 6. DSH（DeepSeek Harness）🟢

- 索引/投影缓存：`$DSH_HOME/storages/session_projcache/sessions/session-<uuid>.json`
  - 含 `record.identity.{createdAt, cwd}` 与 `record.rows.{title, titleInput, workingDirectory, turnOutline, goal, sessionStats}`。
- 正文：`$DSH_HOME/sessions/<编码后的cwd>/session-<uuid>/session.v4.jsonl.zstd`（或未压缩 `session*.jsonl`）
  - `.zstd` 按顺序只读尝试 Python 3.14 `compression.zstd` / `zstandard` / `pyzstd` / `zstd` CLI / `node` 内置 `zlib.zstdDecompressSync` 解压；若环境均不可用，自动回退展示 `session_projcache` 的元信息与 `turnOutline`。
- Skill 发现目录：`~/.dsh/skills/`（及 `~/.agents/skills/`）。

## 7. Windsurf / Cursor / VSCode（探测级 🟡/🔵）

- **Windsurf**：`$WINDSURF_HOME/database/`，支持 `formats` 探测、`list` 枚举文件及 `strings` 兜底。
- **Cursor / VSCode**：跨平台 `workspaceStorage` 路径已纳入 `formats` 探测：
  - Windows：`%APPDATA%/Cursor|Code/User/workspaceStorage`
  - macOS：`~/Library/Application Support/Cursor|Code/User/workspaceStorage`
  - Linux：`$XDG_CONFIG_HOME/Cursor|Code/User/workspaceStorage`（默认 `~/.config/...`）

## 通过 Git 链接安装到各 Harness

本仓库采用 **Agent Skills 单 skill 根目录标准布局**（`SKILL.md`、`scripts/`、`references/` 位于仓库根目录），复制仓库链接即可直接被主流 harness 识别安装：

- **通用 CLI（推荐）**：`npx skills add https://github.com/wuhutakeoffyoo/cross-harness-takeover`
- **ZCode / Codex / OpenCode / Cursor**：`git clone https://github.com/wuhutakeoffyoo/cross-harness-takeover ~/.agents/skills/cross-harness-takeover`
- **Antigravity**：`git clone https://github.com/wuhutakeoffyoo/cross-harness-takeover ~/.gemini/config/skills/cross-harness-takeover`
- **Claude Code**：`git clone https://github.com/wuhutakeoffyoo/cross-harness-takeover ~/.claude/skills/cross-harness-takeover`
- **DSH (DeepSeek Harness)**：`git clone https://github.com/wuhutakeoffyoo/cross-harness-takeover ~/.dsh/skills/cross-harness-takeover`

## 扩展新 harness 的步骤

1. 运行 `python -I scripts/takeover.py formats` 确认目标目录存在，摸清 schema（表结构/JSON 形状/时间戳单位）。
2. 在 `scripts/takeover.py` 新增：
   - `xxx_list(query: str, limit: int) -> List[dict]`（每项含 `harness, id, title, updated, cwd, _ts`）
   - `xxx_show(sid: str, max_chars: int, tail_chars: int)`（返回 `(meta, convo, max_chars, tail_chars)`，找不到返回 `None`）
3. 注册进 `LIST_ADAPTERS` / `SHOW_ADAPTERS`，并在 `get_candidates()` 中补充探测项。
4. 在 `tests/test_takeover.py` 补一条合成数据单元测试并运行 `python -I -m unittest discover -s tests -v`。
5. 更新本文档与 `README.md`。
