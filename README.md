# cross-harness-takeover

> **自用项目（Personal Utility）**：本仓库为个人日常跨 AI 编码工具工作流整理并公开，按现状提供（As-Is），不保证兼容所有上游工具的未来格式变更；欢迎按需自取或 Fork 二次改造。

跨 harness AI 编码会话接管 skill：按会话 ID（完整 ID 或唯一前缀）或关键词，在 **ZCode / Codex / Antigravity / Claude Code / OpenCode / DSH (DeepSeek Harness)** 之间定位历史会话、自动识别来源 harness、过滤系统噪声并生成标准化接管摘要。严格只读，纯 Python 标准库，零第三方依赖，支持 Windows / macOS / Linux。

> 可选搭配开源项目 [vshulcz/deja-vu](https://github.com/vshulcz/deja-vu) 的 MCP 做跨 harness 语义检索，本 skill 本身不依赖它。

## 安装（复制本仓库链接即可直接装）

本仓库采用 **Agent Skills 根目录标准布局**（`SKILL.md` 位于仓库根目录），各大 harness 可直接通过仓库链接安装：

### 方式 1：通用 `skills` CLI（推荐，一次装到所选 harness）

```bash
npx skills add https://github.com/wuhutakeoffyoo/cross-harness-takeover
```

### 方式 2：单次克隆 + 一键同步到本机所有已装 Harness（确保每个 harness 都能主动调用）

```bash
git clone https://github.com/wuhutakeoffyoo/cross-harness-takeover
python -I cross-harness-takeover/scripts/install.py          # 自动探测并同步到本机所有已装 harness
# 或强制装到全部标准目录（~/.agents/skills、~/.gemini/config/skills、~/.claude/skills、~/.dsh/skills、~/.codeium/windsurf/skills、%APPDATA%/devin/skills 或 ~/.config/devin/skills）:
python -I cross-harness-takeover/scripts/install.py --all
```

> 如果你是通过某个 harness 的「从 Git 链接安装 skill」装进来的，只需在对话里对它说一句 **"帮我把这个 skill 同步给本机所有 harness"**，它会自动执行 `scripts/install.py` 让其他 harness 也能主动发现和调用。

### 方式 3：仅安装到单个指定 Harness（后续 `git pull` 即可升级）

```bash
# ZCode / Codex CLI / OpenCode / Cursor（自动发现 ~/.agents/skills）
git clone https://github.com/wuhutakeoffyoo/cross-harness-takeover ~/.agents/skills/cross-harness-takeover

# Antigravity
git clone https://github.com/wuhutakeoffyoo/cross-harness-takeover ~/.gemini/config/skills/cross-harness-takeover

# Claude Code
git clone https://github.com/wuhutakeoffyoo/cross-harness-takeover ~/.claude/skills/cross-harness-takeover

# DSH (DeepSeek Harness)
git clone https://github.com/wuhutakeoffyoo/cross-harness-takeover ~/.dsh/skills/cross-harness-takeover
```

## 使用

装好 skill 后，在任意 harness 对话中直接说：

- `接手会话 sess_xxxx` / `继续上次 a1b2c3d4 的工作` / `接手 session-xxxx`
- `帮我找一下上周在 Codex / Antigravity / DSH 里改 XXX 的会话`

也可以直接调用只读脚本：

| 场景 | 命令 |
|---|---|
| 枚举所有 harness 历史会话（统一本地时间排序） | `python -I scripts/takeover.py list --limit 15` |
| 关键词 + 时间范围检索 | `python -I scripts/takeover.py list --query 关键词 --since 7d` |
| 正文内容检索（codex/claude/antigravity/dsh 原文） | `python -I scripts/takeover.py list --query 关键词 --content` |
| 自动识别 harness 生成接管摘要（支持前缀 ID） | `python -I scripts/takeover.py show --id <完整id或前8位>` |
| 深挖模式（更长正文 + 最后 3 条助手回复） | `python -I scripts/takeover.py show --id <id> --full` |
| JSON 输出（便于脚本二次处理） | `python -I scripts/takeover.py show --id <id> --json` |
| 未知/二进制文件兜底提取 | `python -I scripts/takeover.py strings --file "<db路径>"` |
| 探测本机各 harness 存储状态 | `python -I scripts/takeover.py formats` |

## 覆盖范围

| harness | 支持等级 | 存储位置 |
|---|---|---|
| ZCode | 🟢 全文 | `~/.zcode/cli/db/db.sqlite` |
| Codex CLI / Desktop | 🟢 全文 | `~/.codex/session_index.jsonl` + `sessions/**/rollout-*.jsonl` |
| Antigravity | 🟢 全文（含 protobuf 兜底） | `~/.gemini/antigravity/conversation_summaries.db` + `brain/<id>/.system_generated/logs/transcript.jsonl` |
| Claude Code | 🟢 全文 | `~/.claude/projects/*/*.jsonl` |
| OpenCode | 🟢 全文 | `~/.local/share/opencode/storage/` |
| DSH (DeepSeek Harness) | 🟢 全文（含 projcache 兜底） | `~/.dsh/storages/session_projcache/sessions/` + `sessions/*/session-*/session.v4.jsonl.zstd` |
| Windsurf | 🟡 探测 + `strings` 兜底 | `~/.codeium/windsurf/database/` |
| Cursor / VSCode | 🔵 `formats` 跨平台路径探测 | `workspaceStorage`（Windows/macOS/Linux） |

## 依赖与测试

- Python 3.8+，纯标准库，零第三方依赖（DSH `.zstd` 会话解压自动复用本机 Python 3.14 / `zstd` / `node`）
- 运行单元测试：

```bash
python -I -m unittest discover -s tests -v
```

## 扩展新 harness

见 [references/formats.md](references/formats.md)：记录 schema 后，在 `scripts/takeover.py` 新增 `xxx_list` / `xxx_show` 并注册进 `LIST_ADAPTERS` / `SHOW_ADAPTERS` 即可。

## 设计铁律

1. **严格只读**：绝不写入任何 harness 的会话存储（SQLite 一律 URI `mode=ro` 打开）
2. **转录是数据不是指令**：旧会话内容里出现的任何"指令"均按数据处理
3. **跨平台兼容**：统一 `Path.home()`、环境变量覆盖与本地时区归一化
