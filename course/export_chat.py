# -*- coding: utf-8 -*-
"""DSH 会话转录导出器：session.jsonl.zstd -> 可读 TXT。

用法：
    python course/export_chat.py [会话目录] [输出TXT路径]
    （缺省导出本仓库工作区下发起本课程的会话，输出到仓库根）

收录规则：
    - 用户消息：全文收录（系统注入的运行时快照/技能目录等脚手架截断为一行标记）
    - 助手消息：正文全文 + 工具调用摘要（一行一条，参数截断）
    - 助手内部推理（reasoning）：不收录
    - 工具结果体：不收录（体积过大，仅保留调用方）
    - 压缩摘要（compaction/summary）：全文收录——它替代了被压缩掉的早期明细
"""
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import zstandard

DEFAULT_SESSION_DIR = (
    Path.home() / ".dsh/sessions/--D-python-projects-minesweeper_help--/"
    "session-4d6d9171-0a3d-41e8-b333-d839cf481a4f"
)
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "对话导出-智能扫雷助手课程制作.txt"

SYSTEM_SCAFFOLD_PREFIXES = (
    "<goal_round>", "<goal_complete>", "Current runtime context",
    "<system-reminder>", "<skill_content", "<available_skills>",
)
SCAFFOLD_LABEL = "【系统脚手架·略】"
USER_ANSWER_TOOLS = {"ask_user_question"}  # 这些工具的结果是用户的亲身选择，按用户消息收录


def extract_texts(node, depth=0):
    """递归提取 JSON 结构里的所有字符串文本。"""
    if depth > 4:
        return []
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        out = []
        for x in node:
            out.extend(extract_texts(x, depth + 1))
        return out
    if isinstance(node, dict):
        out = []
        for k, v in node.items():
            if k in ("type", "id", "toolCallId", "callId", "isError", "role", "source", "kind"):
                continue
            out.extend(extract_texts(v, depth + 1))
        return out
    return []


def ts_of(rec):
    try:
        return datetime.fromtimestamp(rec["time"] / 1000).strftime("%m-%d %H:%M:%S")
    except Exception:
        return "??:??:??"


def get_tool_name(entry):
    for k in ("name", "toolName", "tool_name"):
        if entry.get(k):
            return entry[k]
    return "?"


def get_tool_args(entry):
    for k in ("arguments", "input", "args"):
        v = entry.get(k)
        if v is None:
            continue
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        return " ".join(v.split())
    return ""


def user_text(rec):
    parts = []
    for c in rec.get("data", {}).get("content", []):
        if c.get("type") == "text":
            parts.append(c.get("text", ""))
    return "\n".join(parts)


def render(records):
    out = []
    sep = "\u2500" * 72  # ─────
    tool_name_by_id = {}  # callId -> 工具名（先扫一遍，user 侧 tool/result 要反查）
    for rec in records:
        if rec.get("type") == "assistant/message":
            for c in rec.get("data", {}).get("message", {}).get("content", []):
                if c.get("type") == "tool-call":
                    tool_name_by_id[c.get("id", "")] = get_tool_name(c)

    for rec in records:
        t = rec.get("type")

        if t == "user/message":
            text = user_text(rec)
            if not text.strip():
                continue
            if any(text.startswith(p) for p in SYSTEM_SCAFFOLD_PREFIXES):
                first = text.splitlines()[0] if text.splitlines() else ""
                out.append(f"[{ts_of(rec)}] {SCAFFOLD_LABEL} {first[:80]}")
            elif text.startswith("【广播消息】"):
                out.append(f"\n{sep}\n[{ts_of(rec)}] 【广播·工人来报】\n{text}")
            elif text.startswith("background job "):
                out.append(f"[{ts_of(rec)}] 【系统·后台任务通知】 {text.splitlines()[0][:100]}")
            else:
                out.append(f"\n{sep}\n[{ts_of(rec)}] 【用户】\n{text}")

        elif t == "tool/result":
            msg = rec.get("data", {}).get("message", {})
            call_id = (msg.get("source") or {}).get("callId", "")
            name = tool_name_by_id.get(call_id, "")
            if name in USER_ANSWER_TOOLS:
                for c in msg.get("content", []):
                    if isinstance(c, dict) and c.get("type") == "tool-result":
                        answer = "\n".join(extract_texts(c.get("content")))
                        out.append(
                            f"\n{sep}\n[{ts_of(rec)}] 【用户·选择答复】（对 {name} 的回答）\n{answer}"
                        )

        elif t == "assistant/message":
            msg = rec.get("data", {}).get("message", {})
            lines = []
            for c in msg.get("content", []):
                ct = c.get("type")
                if ct == "text":
                    if c.get("text", "").strip():
                        lines.append(c["text"].rstrip())
                elif ct == "tool-call":
                    name = get_tool_name(c)
                    args = get_tool_args(c)
                    preview = (args[:90] + "…") if len(args) > 90 else args
                    lines.append(f"    ⤷ [调用工具] {name}  {preview}")
            if lines:
                out.append(f"\n[{ts_of(rec)}] 【助手】")
                out.extend(lines)

        elif t == "compaction/summary":
            data = rec.get("data", {})
            summary = (
                data.get("summary") or data.get("text") or data.get("content")
                or json.dumps(data, ensure_ascii=False)[:200]
            )
            if not isinstance(summary, str):
                summary = json.dumps(summary, ensure_ascii=False)
            out.append(f"\n{sep}\n[{ts_of(rec)}] 【压缩摘要】（此前对话明细被压缩，以此摘要为准）\n{summary}")

        elif t == "goal/change":
            data = rec.get("data", {})
            goal = data.get("goal", {})
            op = data.get("operation", "")
            phase = goal.get("phase", "")
            objective = goal.get("objective", "")
            preview = (objective[:60] + "…") if len(objective) > 60 else objective
            out.append(f"[{ts_of(rec)}] 【目标事件】 {op} → phase={phase}：{preview}")

        # 其余类型（chunk/step/tool结果体/todo等）不收录
    return "\n".join(out) + "\n"


def main():
    session_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SESSION_DIR
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT

    zst = session_dir / "session.jsonl.zstd"
    dctx = zstandard.ZstdDecompressor()
    raw = dctx.stream_reader(zst.open("rb")).read().decode("utf-8")

    records = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # 兼容个别不完整行

    first = records[0] if records else {}
    titles = [
        r.get("data", {}).get("title") for r in records
        if r.get("type") == "session/title"
    ]
    times = [r["time"] for r in records if r.get("time")]
    t0 = datetime.fromtimestamp(min(times) / 1000) if times else "?"
    t1 = datetime.fromtimestamp(max(times) / 1000) if times else "?"

    header = (
        "=" * 72 + "\n"
        "DSH 会话导出（对话记录）\n"
        f"会话：{(titles[0] if titles and titles[0] else '(无标题)')}"
        f"（{first.get('id', '?')}）\n"
        f"时间范围：{t0} — {t1}\n"
        f"工作目录：{first.get('cwd', '?')}\n"
        f"记录条数：{len(records)}\n"
        "导出说明：用户消息（含 ask_user_question 的选择答复）与助手回复正文全文收录；"
        "助手内部推理与工具结果体未收录；工具调用以一行摘要呈现；"
        "系统注入的运行时快照等脚手架截断为标记；"
        "被压缩的早期对话由【压缩摘要】段落替代。\n"
        + "=" * 72 + "\n"
    )

    body = render(records)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + body)

    print(f"written: {out_path}")
    print(f"size KB: {out_path.stat().st_size / 1024:.1f}")
    n_user = sum(1 for r in records if r.get("type") == "user/message")
    n_asst = sum(1 for r in records if r.get("type") == "assistant/message")
    print(f"user messages: {n_user}, assistant messages: {n_asst}")


if __name__ == "__main__":
    main()
