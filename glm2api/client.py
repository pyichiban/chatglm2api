"""清言上游 HTTP 客户端（依赖仅 requests）。

设计要点（全部有实证依据，见 PROTOCOL.md）：
  * 统一入口 _request/_json，自动注入签名三件套与伪装头。
  * ensure_token：access 缺失/过期即用 refresh 换，且**换完必须落盘**（会轮换 refresh_token）。
  * 流式请求**不设 timeout**（AGENTS.md R35）；非流式请求保留超时。
  * 流被截断**不补结束帧**，抛 GlmStreamTruncated（AGENTS.md R36）。
"""

import json
import random
import secrets
import time
from typing import Any, Iterator

import requests

from . import constants as C
from .credential import Credential, save_credential
from .errors import (GlmAuthError, GlmRateLimitError, GlmStreamTruncated, GlmTransientRejection,
                     GlmUpstreamError)
from .sign import build_headers, sign_now
from .sse import ChatAggregate, ensure_finished, iter_sse_data, parse_frame

JSON_ACCEPT = "application/json, text/plain, */*"
SSE_ACCEPT = "text/event-stream"
AUTH_STATUS = (40102, 40103)  # 上游用这两个业务码表达登录态失效（PROTOCOL §2）


def _normalize_one(message: Any) -> dict:
    """把一条消息规范成清言格式 {"role","content":[{"type":"text","text":...}]}。

    ⚠️ messages **只带当前这一条**：多轮上下文由服务端按 conversation_id 维护，
    客户端拼历史会与上游语义冲突（PROTOCOL §3.6）。因此这里拒绝多条。
    """
    if isinstance(message, str):
        return {"role": "user", "content": [{"type": "text", "text": message}]}
    if not isinstance(message, dict):
        raise GlmUpstreamError(0, f"消息格式不支持: {type(message).__name__}")
    role = str(message.get("role") or "user")
    content = message.get("content")
    if isinstance(content, str):
        items = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        items = []
        for c in content:
            if isinstance(c, str):
                items.append({"type": "text", "text": c})
            elif isinstance(c, dict) and c.get("type") in (None, "text"):
                items.append({"type": "text", "text": str(c.get("text") or "")})
            elif isinstance(c, dict):
                items.append(c)
    else:
        items = [{"type": "text", "text": ""}]
    return {"role": role, "content": items}


def build_chat_body(assistant_id: str, message: Any, conversation_id: str = "",
                    model: str = C.DEFAULT_MODEL, chat_mode: str = "",
                    reasoning_effort: str | None = None, is_networking: bool = True,
                    extra_meta: dict | None = None) -> dict:
    """构造 /chatglm/backend-api/assistant/stream 的请求体（字段对齐抓包 sid 356/480）。

    conversation_id 传空串 = 首轮，服务端懒创建会话并在首帧回传真实 ID（PROTOCOL §3.4）。
    """
    if isinstance(message, list):
        if len(message) != 1:
            raise GlmUpstreamError(0, f"messages 只能带当前这一条（收到 {len(message)} 条）；"
                                      "多轮上下文请用 conversation_id 由服务端维护")
        message = message[0]
    meta = {
        "cogview": {"rm_label_watermark": False},
        "is_test": False,
        "input_question_type": "xxxx",
        "channel": "",
        "draft_id": "",
        "chat_mode": chat_mode,
        "selected_model": model,
        "is_networking": is_networking,
        "quote_log_id": "",
        "platform": "pc",
    }
    if reasoning_effort is not None:
        meta["reasoning_effort"] = reasoning_effort  # 深度=thinking / 极致=deep_thinking（PROTOCOL §3.2）
    if extra_meta:
        meta.update(extra_meta)
    return {
        "assistant_id": assistant_id,
        "conversation_id": conversation_id or "",
        "project_id": "",
        "chat_type": C.CHAT_TYPE,
        "meta_data": meta,
        "messages": [_normalize_one(message)],
    }


def unwrap(payload: dict) -> dict:
    """校验响应信封并取出 result；status != 0 时抛异常。"""
    status = payload.get("status")
    if status == 0:
        return payload.get("result") or {}
    msg = str(payload.get("message") or "unknown error")
    if status in AUTH_STATUS:
        raise GlmAuthError(f"登录态失效 status={status} {msg}")
    raise GlmUpstreamError(200, f"status={status} {msg}", json.dumps(payload, ensure_ascii=False)[:300])


class GlmClient:
    """清言网页版私有接口客户端。一个实例对应一份凭据。"""

    def __init__(self, credential: Credential, *, base: str = C.BASE, verify: bool = True,
                 proxies: dict | None = None, max_retries: int | None = None,
                 retry_base_delay: float | None = None):
        self.cred = credential
        self.base = base.rstrip("/")
        # 风控瞬时拒绝（40012）的退避重试预算。批量调用时可调大；见 README §4.4。
        self.max_retries = C.MAX_RETRIES if max_retries is None else max_retries
        self.retry_base_delay = C.RETRY_BASE_DELAY if retry_base_delay is None else retry_base_delay
        # 非流式用的超时；流式请求会显式传 timeout=None 覆盖它（见 _stream_post）
        self.timeout = (C.CONNECT_TIMEOUT, C.READ_TIMEOUT)
        self.session = requests.Session()
        if not verify:
            self.session.verify = False
        if proxies:
            self.session.proxies.update(proxies)
        self._sync_cookies()

    # ── Cookie ────────────────────────────────────────────────
    def _sync_cookies(self) -> None:
        """把当前凭据写入 session 的 cookie jar。

        用 jar 而**不是**显式 Cookie 头：显式头会覆盖 jar，使服务端下发/轮转的
        WAF cookie（acw_tc / cdn_sec_tc）无法带回下一次请求。清言走阿里云 WAF，
        POST 类接口（recent_list / assistant/stream）依赖该 cookie 链路，
        缺失时持续返回 40012 bad request（实测，见 README §4.4）。
        这里只覆盖业务 cookie，不触碰 jar 里的 WAF cookie。
        """
        for k, v in (("chatglm_token", self.cred.access_token),
                     ("chatglm_refresh_token", self.cred.refresh_token),
                     ("chatglm_user_id", self.cred.user_id)):
            self.session.cookies.pop(k, None)  # 先清旧的（含 .chatglm.cn 域），避免重复
            if v:
                self.session.cookies.set(k, v, domain="chatglm.cn", path="/")
        for k, v in self.cred.extra_cookies.items():
            self.session.cookies.set(k, v, domain="chatglm.cn", path="/")

    # ── 生命周期 ──────────────────────────────────────────────
    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "GlmClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<GlmClient uid={self.cred.user_id or '?'} device={self.cred.device_id or '?'}>"

    # ── 请求底层 ──────────────────────────────────────────────
    def _headers(self, accept: str) -> dict[str, str]:
        h = build_headers(token=self.cred.access_token, accept=accept)
        if self.cred.device_id:
            h["X-Device-Id"] = self.cred.device_id  # 与 JWT.device_id 一致（PROTOCOL §1.2）
        h["X-Request-Id"] = secrets.token_hex(16)
        return h

    def _url(self, path: str) -> str:
        return self.base + path

    def _raw_request(self, method: str, path: str, *, body: Any = None, params: dict | None = None,
                     accept: str = JSON_ACCEPT) -> requests.Response:
        """发一次带签名的请求（含 401 自愈重试），**不做风控退避**。"""
        self.ensure_token()
        resp = self.session.request(
            method, self._url(path), headers=self._headers(accept),
            data=json.dumps(body).encode() if body is not None else None,
            params=params, timeout=self.timeout,
        )
        if resp.status_code == 401:
            self.ensure_token(force=True)  # 本地 exp 未到但上游已作废，靠 401 触发自愈（不变量 19）
            resp.close()
            resp = self.session.request(
                method, self._url(path), headers=self._headers(accept),
                data=json.dumps(body).encode() if body is not None else None,
                params=params, timeout=self.timeout,
            )
        return resp

    @staticmethod
    def _is_transient(resp: requests.Response) -> bool:
        """HTTP 400 + 信封 status=40012 = 风控瞬时拒绝，可退避重试（实测，见 constants）。"""
        if resp.status_code != 400:
            return False
        try:
            return resp.json().get("status") == C.STATUS_TRANSIENT
        except ValueError:
            return False

    def _request(self, method: str, path: str, *, body: Any = None, params: dict | None = None,
                 accept: str = JSON_ACCEPT, retries: int | None = None) -> requests.Response:
        """带风控退避重试的请求。

        清言 POST 类接口会以 40012 瞬时拒绝；实测连发 10 次全失败、退避后成功，
        所以这里必须退避重试而不是直接报错。GET 类接口不受影响（不会触发重试）。
        """
        retries = self.max_retries if retries is None else retries
        for attempt in range(retries + 1):
            resp = self._raw_request(method, path, body=body, params=params, accept=accept)
            if attempt < retries and self._is_transient(resp):
                resp.close()
                # 指数退避 + 抖动，避免多客户端同频重试再次被拒
                time.sleep(self._backoff(attempt))
                continue
            return resp
        return resp  # pragma: no cover —— 循环必返回

    def _backoff(self, attempt: int) -> float:
        """指数退避 + 50%~150% 抖动。"""
        return self.retry_base_delay * (2 ** attempt) * (0.5 + random.random())

    def _json(self, method: str, path: str, *, body: Any = None, params: dict | None = None,
              retries: int | None = None) -> dict:
        """发请求并解信封，返回 result。"""
        resp = self._request(method, path, body=body, params=params, retries=retries)
        try:
            raw = resp.content
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            raise GlmUpstreamError(resp.status_code, "响应不是合法 JSON（可能被 WAF 拦截）",
                                   resp.text[:300]) from None
        if resp.status_code == 429:
            raise GlmRateLimitError(f"被限流: {payload.get('message')}")
        if resp.status_code == 401:
            raise GlmAuthError(f"HTTP 401: {payload.get('message')}")
        if payload.get("status") == C.STATUS_TRANSIENT:
            raise GlmTransientRejection(
                resp.status_code, f"风控瞬时拒绝（已退避重试 {retries or self.max_retries} 次仍失败）: "
                                   f"{payload.get('message')}；稍后重试或降低请求频率")
        if resp.status_code >= 400:
            raise GlmUpstreamError(resp.status_code, str(payload.get("message") or "http error"),
                                   resp.text[:300])
        return unwrap(payload)

    # ── 凭据 ──────────────────────────────────────────────────
    def ensure_token(self, force: bool = False) -> str:
        """确保 access token 可用；过期或 force 时用 refresh 换，并**落盘轮换后的 refresh_token**。"""
        if force or self.cred.access_expired():
            self.refresh()
        return self.cred.access_token

    def refresh(self) -> str:
        """用 refresh_token 换 access_token。

        ⚠️ 上游**会同时轮换 refresh_token**（PROTOCOL §1.3 铁律 1）：
        必须把响应里的新 refresh_token 落盘，否则下次启动用旧值 → 账号失效。
        """
        h = build_headers(token=self.cred.refresh_token, accept=JSON_ACCEPT)
        if self.cred.device_id:
            h["X-Device-Id"] = self.cred.device_id
        h["X-Request-Id"] = secrets.token_hex(16)
        # 刷新只用根凭据：access 可能已失效不可带；用独立 session 避免污染业务 jar
        rs = requests.Session()
        rs.verify = self.session.verify
        rs.proxies = self.session.proxies
        rs.cookies.set("chatglm_refresh_token", self.cred.refresh_token, domain="chatglm.cn", path="/")
        if self.cred.user_id:
            rs.cookies.set("chatglm_user_id", self.cred.user_id, domain="chatglm.cn", path="/")
        try:
            # 刷新接口同样会被风控瞬时拒绝（实测 40012），必须退避重试
            for attempt in range(self.max_retries + 1):
                h["X-Timestamp"], h["X-Nonce"], h["X-Sign"] = sign_now()  # 重试需换新签名
                resp = rs.post(self._url(C.EP_REFRESH), headers=h, data=b"{}", timeout=self.timeout)
                if attempt < self.max_retries and self._is_transient(resp):
                    resp.close()
                    time.sleep(self._backoff(attempt))
                    continue
                break
        finally:
            rs.close()
        if resp.status_code == 401 or resp.status_code == 403:
            raise GlmAuthError(f"refresh 被拒（HTTP {resp.status_code}）：refresh_token 可能已失效")
        try:
            payload = resp.json()
        except ValueError:
            raise GlmUpstreamError(resp.status_code, "refresh 响应不是 JSON", resp.text[:300]) from None
        res = payload.get("result") or {}
        if payload.get("status") == C.STATUS_TRANSIENT:
            raise GlmTransientRejection(resp.status_code,
                                        f"refresh 被风控瞬时拒绝（已退避重试 {self.max_retries} 次）")
        if payload.get("status") != 0 or not res.get("access_token"):
            raise GlmAuthError(f"refresh 失败: status={payload.get('status')} {payload.get('message')}")
        if res.get("is_guest"):
            raise GlmAuthError("访客账号不可用：请用真实账号登录清言后重新取 refresh_token")

        self.cred.access_token = res["access_token"]
        if res.get("refresh_token"):
            self.cred.refresh_token = res["refresh_token"]  # 轮换后的新根凭据
        if res.get("user_id"):
            self.cred.user_id = res["user_id"]
        self.cred.absorb_jwt()
        self._sync_cookies()  # 轮换后的新 access/refresh 同步进 jar

        # 落盘失败必须显式报错：内存与磁盘不一致时重启会拿已作废的 refresh token（不变量 20）
        if self.cred.source and self.cred.source != "env":
            save_credential(self.cred)
        return self.cred.access_token

    # ── 用户与模型 ────────────────────────────────────────────
    def user_info(self) -> dict:
        """GET user/info。积分余额 = result["member_info"]["left_score"]，单位「分」（100 分 = 1 积分）。"""
        return self._json("GET", C.EP_USER_INFO)

    def left_score(self) -> float:
        """积分余额（单位「分」）。访客无 member_info 字段，返回 0.0。"""
        mi = self.user_info().get("member_info") or {}
        try:
            return float(mi.get("left_score") or 0)
        except (TypeError, ValueError):
            return 0.0

    def models(self) -> dict:
        """GET operation/detail?tag=available_models，返回 result 原样（含 models / default）。"""
        return self._json("GET", C.EP_MODELS, params={"tag": "available_models"})

    # ── 会话列表 ──────────────────────────────────────────────
    def list_conversations(self, page: int = 1, page_size: int = C.DEFAULT_PAGE_SIZE) -> dict:
        """POST conversation/recent_list。返回 {has_more, conversation_list}（翻页是纯页码）。"""
        return self._json("POST", C.EP_CONV_LIST, body={"page": page, "page_size": page_size})

    def iter_conversations(self, page_size: int = C.DEFAULT_PAGE_SIZE) -> Iterator[dict]:
        """自动翻页，逐条 yield conversation_list 元素，直到 has_more=False。"""
        page = 1
        while True:
            res = self.list_conversations(page=page, page_size=page_size)
            for conv in res.get("conversation_list") or []:
                yield conv
            if not res.get("has_more"):
                return
            page += 1

    # ── 对话历史（结构未实证，动态透传）──────────────────────
    def history(self, conversation_id: str, assistant_id: str = C.DEFAULT_ASSISTANT,
                page_size: int = C.DEFAULT_HISTORY_PAGE_SIZE, **extra) -> dict:
        """GET agent-api/conversation/page_messages_v2。

        ⚠️ **已知缺口（PROTOCOL §3.5 / HANDOFF §4）**：``list`` 元素结构与翻页参数名
        **在本次抓包中没有证据**（5 次调用全返回空数组）。因此本函数：
          * 只透传 ``result``（含 list / little_more / greate_more），**不写死 list 元素字段**；
          * 额外参数经 ``**extra`` 原样拼进 query，便于日后补测翻页参数名而无需改签名。
        待补：在真实账号上「打开一条有真实消息的会话并向下翻页」再抓一次包。
        """
        params = {"assistant_id": assistant_id, "conversation_id": conversation_id,
                  "page_size": page_size, **extra}
        return self._json("GET", C.EP_HISTORY, params=params)

    # ── 会话生命周期 ──────────────────────────────────────────
    def chat(self, assistant_id: str, message: Any, conversation_id: str | None = None,
             stream: bool = False, **opts):
        """发一条消息。

        stream=True  → 原样透传上游 SSE（逐块 yield bytes，不解析/不改写/不补 [DONE]）
        stream=False → 缓存池聚合后一次性返回 dict（含 conversation_id / text / thinking）

        首轮 conversation_id 传 None：上游空串 → 会话由服务端懒创建，真实 ID 从首帧取回。
        """
        body = build_chat_body(assistant_id, message, conversation_id or "", **opts)
        if stream:
            return self._chat_stream(body)
        return self._chat_once(body)

    def create_conversation(self, message: Any, assistant_id: str = C.DEFAULT_ASSISTANT, **opts) -> str:
        """没有「创建会话」端点（PROTOCOL §3.4）：会话由首次发消息时服务端懒创建。

        本方法即「发首条消息并返回首帧回传的 conversation_id」。
        """
        res = self.chat(assistant_id, message, conversation_id=None, stream=False,
                        **opts)
        if not res.get("conversation_id"):
            raise GlmUpstreamError(0, "上游未返回 conversation_id（无法确认会话已创建）")
        return res["conversation_id"]

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        """POST conversation/modify_title。"""
        self._json("POST", C.EP_MODIFY_TITLE, body={"conversation_id": conversation_id, "title": title})

    def conversation_title(self, conversation_id: str) -> str:
        """GET conversation/title，返回自动生成的标题。"""
        return str(self._json("GET", C.EP_CONV_TITLE, params={"conversation_id": conversation_id})
                   .get("title") or "")

    def delete_conversations(self, conversation_ids: list[str],
                             assistant_id: str = C.DEFAULT_ASSISTANT) -> None:
        """POST conversation/bulk_delete。删单个也走 bulk（无单删端点）。"""
        self._json("POST", C.EP_BULK_DELETE,
                   body={"conversation_ids": list(conversation_ids), "assistant_id": assistant_id})

    def delete_conversation(self, conversation_id: str, assistant_id: str = C.DEFAULT_ASSISTANT) -> None:
        """单删便捷封装（内部仍是 bulk_delete）。"""
        self.delete_conversations([conversation_id], assistant_id)

    def stop_stream(self, history_id: str) -> None:
        """POST stream/stop_stream（history_id == 流的 parts[].id）。"""
        self._json("POST", C.EP_STOP_STREAM, body={"history_id": history_id})

    def update_status(self, history_id: str) -> None:
        """POST stream/update_status。"""
        self._json("POST", C.EP_UPDATE_STATUS, body={"history_id": history_id})

    # ── 非流式：缓存池聚合 ────────────────────────────────────
    def _chat_once(self, body: dict) -> dict:
        """流式接口 + 内部聚合，对外表现为非流式（HANDOFF §3.2）。"""
        agg = ChatAggregate()
        with self._stream_response(body) as resp:
            for payload in iter_sse_data(resp.iter_lines()):
                if obj := parse_frame(payload):
                    agg.feed(obj)
        ensure_finished(agg)  # 没有 finish 也没有 error ⇒ 截断，抛 GlmStreamTruncated
        return agg.as_result()

    # ── 流式：原样透传 ────────────────────────────────────────
    def _chat_stream(self, body: dict) -> Iterator[bytes]:
        """逐块 yield 上游原始字节：不解析、不改写、不补 [DONE]。"""
        with self._stream_response(body) as resp:
            for chunk in resp.iter_content(chunk_size=None):
                if chunk:
                    yield chunk

    def _stream_response(self, body: dict, retries: int | None = None):
        """建立 SSE 连接并返回 response 上下文。

        ⚠️ 流式请求**必须不设 timeout**（AGENTS.md R35 实证：带超时会让长回答被掐断
        且无终止帧）。requests 的默认 timeout 即为 None，这里显式写明以防误改。

        风控瞬时拒绝（40012）同样会命中流式接口，但这里**只在建连阶段退避重试**：
        一旦 SSE 已开始输出就绝不能重试，否则会重复计费 / 产生重复消息。
        """
        self.ensure_token()
        retries = self.max_retries if retries is None else retries
        for attempt in range(retries + 1):
            resp = self.session.post(
                self._url(C.EP_CHAT), headers=self._headers(SSE_ACCEPT),
                data=json.dumps(body).encode(), stream=True, timeout=None,  # ← 刻意不设超时
            )
            if attempt < retries and self._is_transient(resp):
                resp.close()
                time.sleep(self._backoff(attempt))
                continue
            break
        if resp.status_code >= 400:
            raw = resp.text[:300]
            resp.close()
            if resp.status_code == 401:
                raise GlmAuthError(f"HTTP 401: {raw}")
            if resp.status_code == 429:
                raise GlmRateLimitError(f"被限流: {raw}")
            try:
                if json.loads(raw).get("status") == C.STATUS_TRANSIENT:
                    raise GlmTransientRejection(
                        resp.status_code,
                        f"对话接口被风控瞬时拒绝（已退避重试 {retries} 次）：请稍后重试")
            except (ValueError, AttributeError):
                pass
            raise GlmUpstreamError(resp.status_code, "对话接口返回错误", raw)
        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" not in ctype:
            # 上游可能以 HTTP 200 返回 JSON 错误信封（非 SSE）
            raw = resp.text[:500]
            resp.close()
            try:
                env = json.loads(raw)
                if env.get("status") in AUTH_STATUS:
                    raise GlmAuthError(f"上游返回登录失效: {raw}")
                raise GlmUpstreamError(resp.status_code, f"预期 SSE，实际 Content-Type={ctype!r}",
                                       raw)
            except json.JSONDecodeError:
                raise GlmUpstreamError(resp.status_code, f"预期 SSE，实际 Content-Type={ctype!r}",
                                       raw) from None
        return _ClosingResponse(resp)


class _ClosingResponse:
    """保证退出时关闭上游连接的小上下文包装。"""

    def __init__(self, resp: requests.Response):
        self._resp = resp

    def __enter__(self) -> requests.Response:
        return self._resp

    def __exit__(self, *exc) -> None:
        self._resp.close()
