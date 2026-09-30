"""SSE 解析工具。

清言对话流（/chatglm/backend-api/assistant/stream）的帧格式：

    data: {"id":"...","conversation_id":"...","parts":[...],"status":"init"|"finish"|"error"}

**三条实证规则（勿凭直觉改，PROTOCOL §3.6）**：
  1. 上游**不发** ``data: [DONE]``；正常收尾的标志是顶层 ``status:"finish"``。
  2. ``parts[].status=="init"`` → ``content[].text`` 是**增量片段**；
     ``parts[].status=="finish"`` → 是**该段落完整全文**。
  3. 流被截断时**不得**补结束帧伪装成正常收尾（AGENTS.md R36），必须抛 GlmStreamTruncated。

本模块只做「解析 / 聚合」，与 HTTP 无关，便于用抓包文件离线做回归测试。
"""

import json
from typing import Iterator

from .errors import GlmStreamTruncated

# 视为「正文」的 content 类型（think 单独归入 reasoning）
TEXT_TYPES = ("text", "code", "execution_output")
THINK_TYPES = ("think", "tool_result", "quote_result")


def iter_sse_data(lines) -> Iterator[str]:
    """从任意行迭代器里抽出每个 ``data:`` 的载荷（多行 data 用 \\n 拼接）。

    非 data 行（event:/id:/retry:/空行）忽略；``[DONE]`` 不产出（清言本来也不发）。
    """
    buf: list[str] = []
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = line.rstrip("\r\n")
        if line == "":
            if buf:
                yield "\n".join(buf)
                buf = []
            continue
        if line.startswith("data:"):
            v = line[5:]
            buf.append(v[1:] if v.startswith(" ") else v)
        # 其它字段忽略
    if buf:
        yield "\n".join(buf)


def parse_frame(payload: str) -> dict | None:
    """把一帧 JSON 解成 dict；非法 / 空载荷返回 None（容忍半帧）。"""
    payload = payload.strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def frame_state(obj: dict) -> str:
    """取顶层状态：init / finish / error / ""。"""
    return str(obj.get("status") or "")


def frame_conversation_id(obj: dict) -> str:
    """从一帧里取 conversation_id（首轮会话 ID 由首帧回传，这是唯一来源）。"""
    if cid := obj.get("conversation_id"):
        return str(cid)
    meta = obj.get("meta_data")
    if isinstance(meta, dict) and meta.get("conversation_id"):
        return str(meta["conversation_id"])
    return ""


def extract_history_id(obj: dict) -> str:
    """取 assistant 消息主键 history_id（== parts[].id）。用于 stop_stream / update_status。"""
    for p in obj.get("parts") or []:
        if isinstance(p, dict) and p.get("id"):
            return str(p["id"])
    return str(obj.get("id") or "")


def _part_key(part: dict, idx: int) -> str:
    """段落稳定标识：优先 logic_id（同一逻辑段落跨帧恒定），退回 id，再退回序号。"""
    return str(part.get("logic_id") or part.get("id") or f"#{idx}")


def render_part(part: dict) -> tuple[str, str, bool]:
    """渲染一个段落，返回 (正文, 思考文本, 是否为全文)。

    ``part.status=="finish"`` 时返回的是该段全文，否则是本帧增量。
    两种情况的渲染规则必须一致，否则聚合会算错。
    """
    finished = part.get("status") == "finish"
    texts: list[str] = []
    thinks: list[str] = []
    for c in part.get("content") or []:
        if not isinstance(c, dict):
            continue
        t = c.get("type")
        if t in TEXT_TYPES:
            texts.append(str(c.get("text") or c.get("code") or ""))
        elif t in THINK_TYPES:
            thinks.append(str(c.get("think") or c.get("content") or ""))
    return "".join(texts), "".join(thinks), finished


class ChatAggregate:
    """把一次对话流的帧聚合成完整回答（非流式路径用）。

    规则（与 PROTOCOL §3.6 一致）：
      - ``parts[].status=="init"``  → 累加
      - ``parts[].status=="finish"`` → 覆盖为该段全文（权威值，修正可能的丢帧）
    """

    def __init__(self) -> None:
        self.conversation_id = ""
        self.history_id = ""
        self.order: list[str] = []            # 段落出现顺序（logic_id）
        self.texts: dict[str, str] = {}
        self.thinks: dict[str, str] = {}
        self.finished = False                 # 是否见过顶层 status=="finish"
        self.error: dict | None = None        # 顶层 status=="error" 时的 error 对象
        self.last_frame: dict | None = None
        self.frames = 0

    def feed(self, obj: dict) -> None:
        self.frames += 1
        self.last_frame = obj
        if not self.conversation_id:
            self.conversation_id = frame_conversation_id(obj)
        if not self.history_id:
            self.history_id = extract_history_id(obj)
        st = frame_state(obj)
        if st == "error":
            self.error = obj.get("error") or obj.get("last_error") or {}
        elif st == "finish":
            self.finished = True
        for i, part in enumerate(obj.get("parts") or []):
            if not isinstance(part, dict):
                continue
            key = _part_key(part, i)
            if key not in self.texts:
                self.order.append(key)
            text, think, whole = render_part(part)
            if whole:  # finish 帧是该段权威全文，直接覆盖
                self.texts[key] = text
                self.thinks[key] = think
            else:      # init 帧是增量，累加
                self.texts[key] = self.texts.get(key, "") + text
                self.thinks[key] = self.thinks.get(key, "") + think

    def as_result(self) -> dict:
        """输出结构化结果。text / thinking 按段落顺序拼接。"""
        text = "\n".join(t for k in self.order if (t := self.texts.get(k, "")))
        thinking = "\n".join(t for k in self.order if (t := self.thinks.get(k, "")))
        parts = [{"logic_id": k, "text": self.texts.get(k, ""), "thinking": self.thinks.get(k, "")}
                 for k in self.order]
        return {
            "conversation_id": self.conversation_id,
            "history_id": self.history_id,
            "text": text,
            "thinking": thinking,
            "parts": parts,
            "finished": self.finished,
            "error": self.error,
            "frames": self.frames,
        }


def ensure_finished(agg: ChatAggregate) -> None:
    """收尾校验：没见过 finish 也没见过 error ⇒ 流被截断，必须报错。

    上游正常收尾 = 顶层 ``status:"finish"``；上游本来就不发 ``[DONE]``。
    """
    if agg.finished or agg.error is not None:
        return
    raise GlmStreamTruncated(f"共收 {agg.frames} 帧，末帧 status={frame_state(agg.last_frame or {})!r}")
