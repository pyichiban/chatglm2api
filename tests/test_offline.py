"""离线回归测试：不发起任何网络请求。

数据来源是 2026-09-29 的 Fiddler 抓包（PROTOCOL.md 的证据源）。
抓包目录默认取 dev/ref/chatglm_web_20260929_extracted，缺失时相关用例自动跳过。

运行：D:/dev/uv/ve/sys312/Scripts/python.exe -m pytest tests -q
"""

import hashlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glm2api import constants as C                                  # noqa: E402
from glm2api.client import GlmClient, build_chat_body               # noqa: E402
from glm2api.credential import load_credential, make_credential     # noqa: E402
from glm2api.errors import GlmStreamTruncated, GlmUpstreamError     # noqa: E402
from glm2api.sign import build_headers, sign_now                    # noqa: E402
from glm2api.sse import ChatAggregate, ensure_finished, iter_sse_data, parse_frame  # noqa: E402

# 抓包目录（可被环境变量覆盖）
CAPTURE = Path(os.environ.get(
    "GLM_CAPTURE_DIR",
    "D:/work/wild-work/dev/ref/chatglm_web_20260929_extracted",
))
BODY356 = CAPTURE / "bodies" / "356.body"   # 首轮对话：think + tool_calls + text，4 帧
BODY480 = CAPTURE / "bodies" / "480.body"   # 多轮被用户 abort：末帧 status=error

needs_capture = pytest.mark.skipif(not BODY356.exists(), reason=f"抓包目录不存在: {CAPTURE}")


# ── 签名 ─────────────────────────────────────────────────────
def test_sign_matches_capture_evidence():
    """PROTOCOL §2 的复算证据：6/6 命中的其中一组。"""
    ts, nonce = "1790732179905", "75988225830f4d93b6702dcd0a2e9baf"
    expect = "a32ee7a2876ad44e6658475f0a7c69eb"
    assert hashlib.md5(f"{ts}-{nonce}-{C.SIGN_SECRET}".encode()).hexdigest() == expect
    # sign_now 必须用同一公式（固定 nonce 后逐字节相同）
    with mock.patch("glm2api.sign.secrets.token_hex", return_value=nonce):
        assert sign_now(now_ms=int(ts)) == (ts, nonce, expect)


def test_build_headers_shape():
    h = build_headers(token="T", accept="text/event-stream")
    assert h["Authorization"] == "Bearer T"
    assert h["Accept"] == "text/event-stream"
    assert len(h["X-Nonce"]) == 32 and len(h["X-Timestamp"]) == 13
    assert len(h["X-Sign"]) == 32
    # 无 token 时不带 Authorization
    assert "Authorization" not in build_headers()
    # ⚠️ 关键：build_headers **不得**设 Cookie 头（会覆盖 cookie jar，导致 40012）
    assert "Cookie" not in h


# ── 凭据 ─────────────────────────────────────────────────────
FAKE_JWT_PAYLOAD = {"uid": "6982e0ad0efeac7998789a48", "device_id": "c37ee0587ac14b9483f419380d8d23ca",
                    "type": "refresh", "exp": 4102444800}


def _fake_jwt(payload: dict) -> str:
    import base64
    b = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{b}.sig"


def test_make_credential_from_cookie():
    """整条 cookie 粘贴 → 解析出根凭据；WAF 标记必须被丢弃（PROTOCOL §1.4）。"""
    rt = _fake_jwt(FAKE_JWT_PAYLOAD)
    cookie = f"ssxmod_itna=xxx; acw_tc=yyy; chatglm_refresh_token={rt}; chatglm_user_id=u1"
    cred = make_credential(cookie=cookie)
    assert cred.refresh_token == rt
    assert cred.user_id == "u1"
    assert cred.device_id == FAKE_JWT_PAYLOAD["device_id"]     # 从 JWT 自动补齐
    assert "ssxmod_itna" not in cred.as_cookie() and "acw_tc" not in cred.as_cookie()
    assert "chatglm_refresh_token=" + rt in cred.as_cookie()


def test_credential_status_and_save(tmp_path):
    rt = _fake_jwt(FAKE_JWT_PAYLOAD)
    cred = make_credential(refresh_token=rt)
    assert cred.access_expired()          # 没有 access → 需要 refresh
    assert cred.refresh_exp > 0
    cred.access_token = _fake_jwt({"exp": 4102444800})
    assert not cred.access_expired()

    # 落盘 / 回读（refresh 轮换后必须能正确读回）
    f = tmp_path / "cred.json"
    f.write_text("{}", encoding="utf-8")
    cred.source = str(f)
    from glm2api.credential import save_credential
    save_credential(cred)
    again = load_credential(str(f))
    assert again.refresh_token == rt and again.access_token == cred.access_token


def test_load_credential_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GLM_REFRESH_TOKEN", _fake_jwt(FAKE_JWT_PAYLOAD))
    monkeypatch.delenv("GLM_CREDENTIAL_FILE", raising=False)
    monkeypatch.chdir(tmp_path)   # 避开仓库里的 glm_credential.json
    cred = load_credential()
    assert cred.source == "env" and cred.user_id == FAKE_JWT_PAYLOAD["uid"]


# ── 请求体 ───────────────────────────────────────────────────
def test_build_chat_body_matches_capture_shape():
    """对齐抓包 sid 356 的字段集合与取值（首轮 conversation_id 为空串）。"""
    body = build_chat_body(C.DEFAULT_ASSISTANT, "你好", "", model="glm-5.3-flash",
                           chat_mode="thinking", reasoning_effort="high", is_networking=True)
    assert body["conversation_id"] == "" and body["chat_type"] == "user_chat"
    assert body["project_id"] == "" and body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "你好"}]}]
    assert set(body["meta_data"]) >= set(["cogview", "is_test", "input_question_type", "channel",
                                          "draft_id", "chat_mode", "selected_model", "is_networking",
                                          "quote_log_id", "platform", "reasoning_effort"])


def test_build_chat_body_rejects_history():
    """messages 只带当前这一条：客户端拼历史会与上游语义冲突（PROTOCOL §3.6）。"""
    with pytest.raises(GlmUpstreamError):
        build_chat_body(C.DEFAULT_ASSISTANT, [{"role": "user", "content": "a"},
                                              {"role": "assistant", "content": "b"}])


def test_multiturn_uses_conversation_id():
    body = build_chat_body(C.DEFAULT_ASSISTANT, "第二问", "abc123")
    assert body["conversation_id"] == "abc123" and len(body["messages"]) == 1


# ── 请求头（不发请求）────────────────────────────────────────
def test_client_headers_include_signed_fields():
    cred = make_credential(refresh_token=_fake_jwt(FAKE_JWT_PAYLOAD))
    cred.access_token = _fake_jwt({"exp": 4102444800})
    client = GlmClient(cred)
    h = client._headers("application/json, text/plain, */*")
    for k in ("X-Sign", "X-Nonce", "X-Timestamp", "X-Device-Id", "X-Request-Id", "Authorization",
              "App-Name", "Origin"):
        assert k in h, k
    assert h["X-Device-Id"] == FAKE_JWT_PAYLOAD["device_id"]   # 与 JWT 一致（PROTOCOL §1.2）
    assert len(h["X-Request-Id"]) == 32
    assert "Cookie" not in h                                    # cookie 一律走 jar
    client.close()


def test_client_cookies_go_into_jar():
    """凭据必须进 cookie jar（而非显式 Cookie 头），否则 WAF cookie 无法链路化。"""
    cred = make_credential(refresh_token=_fake_jwt(FAKE_JWT_PAYLOAD))
    cred.access_token = _fake_jwt({"exp": 4102444800})
    client = GlmClient(cred)
    jar = client.session.cookies.get_dict(domain="chatglm.cn", path="/")
    assert jar["chatglm_refresh_token"] == cred.refresh_token
    assert jar["chatglm_token"] == cred.access_token
    assert jar["chatglm_user_id"] == cred.user_id
    # WAF cookie 不在 jar 里也不应被误删，且绝不能来自「凭据额外 cookie」
    assert not any(k.startswith(("ssxmod", "_c_", "_nb_")) for k in jar)
    client.close()


# ── SSE：用真实抓包帧回归 ────────────────────────────────────
@needs_capture
def test_parse_capture_frames():
    lines = BODY356.read_text(encoding="utf-8", errors="replace").splitlines()
    frames = [f for f in (parse_frame(p) for p in iter_sse_data(lines)) if f]
    assert [f["status"] for f in frames] == ["init", "init", "init", "finish"]
    assert frames[0]["conversation_id"] == "6abbaf3a430b0bf06c7b6b1a"
    # 段落状态：三段都是 finish（该段完整全文），顶层才是增量语义的 init
    assert [f["parts"][0]["status"] for f in frames] == ["finish", "finish", "finish", "finish"]


@needs_capture
def test_aggregate_first_round():
    """首轮：think + tool_calls + text 三段；末帧 finish ⇒ 完整。"""
    agg = ChatAggregate()
    for payload in iter_sse_data(BODY356.read_text(encoding="utf-8", errors="replace").splitlines()):
        if obj := parse_frame(payload):
            agg.feed(obj)
    ensure_finished(agg)
    res = agg.as_result()
    assert res["finished"] and res["error"] is None
    assert res["conversation_id"] == "6abbaf3a430b0bf06c7b6b1a"
    assert res["history_id"].startswith("6abbaf3a")
    assert res["text"].startswith("你好")         # finish 帧覆盖，不得重复两遍
    assert res["text"].count("你好呀") == 1
    assert "用户发来了一个简单的问候" in res["thinking"]   # think 段归入 thinking
    assert res["frames"] == 4


@needs_capture
def test_aggregate_error_frame_is_not_finish():
    """被 abort 的流：末帧 status=error，error 必须被记录且不视为正常完成。"""
    agg = ChatAggregate()
    for payload in iter_sse_data(BODY480.read_text(encoding="utf-8", errors="replace").splitlines()):
        if obj := parse_frame(payload):
            agg.feed(obj)
    ensure_finished(agg)               # error 也算「有结论」，不该抛截断
    res = agg.as_result()
    assert not res["finished"]
    assert res["error"] and res["error"].get("error_code") == 10024
    assert res["text"]                 # abort 前已产生的正文仍然保留


@needs_capture
def test_truncation_raises_instead_of_faking_done():
    """R36：流被截断必须抛错，不得伪装成正常收尾。"""
    lines = BODY356.read_text(encoding="utf-8", errors="replace").splitlines()
    cut = lines[:lines.index("")]      # 只保留第一帧（丢掉 finish 帧）
    agg = ChatAggregate()
    for payload in iter_sse_data(cut):
        if obj := parse_frame(payload):
            agg.feed(obj)
    with pytest.raises(GlmStreamTruncated):
        ensure_finished(agg)


def test_iter_sse_data_handles_multiline_and_done():
    payloads = list(iter_sse_data(["event: message", "data: {\"a\":1}", "",
                                   "data: [DONE]", "", "data: {\"b\":", "data: 2}", ""]))
    assert payloads == ['{"a":1}', '[DONE]', '{"b":\n2}']
    assert parse_frame("[DONE]") is None and parse_frame("{bad") is None


# ── 风控瞬时拒绝（40012）退避重试 ─────────────────────────────
class _FakeResp:
    """模拟 requests.Response 的最小面。"""

    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    @property
    def content(self) -> bytes:
        return self.text.encode()

    def close(self) -> None:
        pass


def _client():
    cred = make_credential(refresh_token=_fake_jwt(FAKE_JWT_PAYLOAD))
    cred.access_token = _fake_jwt({"exp": 4102444800})
    return GlmClient(cred)


def test_transient_40012_is_retried_then_succeeds(monkeypatch):
    """40012 是瞬时拒绝：退避重试后必须拿到结果，而不是直接报错。"""
    client = _client()
    calls = []

    def fake_request(method, url, **kw):
        calls.append(1)
        # 前两次瞬时拒绝，第三次成功（复现实测：退避到第 4 次成功）
        if len(calls) < 3:
            return _FakeResp(400, {"status": C.STATUS_TRANSIENT, "message": "bad request(40012)"})
        return _FakeResp(200, {"status": 0, "result": {"ok": True}})

    monkeypatch.setattr(client.session, "request", fake_request)
    monkeypatch.setattr("glm2api.client.time.sleep", lambda *_: None)  # 不等真时间
    assert client._json("POST", C.EP_CONV_LIST, body={"page": 1}) == {"ok": True}
    assert len(calls) == 3
    client.close()


def test_transient_40012_exhausted_raises_distinct_error(monkeypatch):
    """重试耗尽后必须抛可区分的 GlmTransientRejection（提示可稍后重试）。"""
    from glm2api.errors import GlmTransientRejection
    client = _client()
    monkeypatch.setattr(client.session, "request",
                        lambda *a, **k: _FakeResp(400, {"status": C.STATUS_TRANSIENT,
                                                        "message": "bad request(40012)"}))
    monkeypatch.setattr("glm2api.client.time.sleep", lambda *_: None)
    with pytest.raises(GlmTransientRejection):
        client._json("POST", C.EP_CONV_LIST, body={"page": 1}, retries=2)
    client.close()


def test_non_transient_error_not_retried(monkeypatch):
    """普通业务错误不得触发重试（避免把「确定性失败」放大成 5 倍请求）。"""
    client = _client()
    calls = []
    monkeypatch.setattr(client.session, "request",
                        lambda *a, **k: (calls.append(1),
                                         _FakeResp(200, {"status": 50001, "message": "param error"}))[1])
    with pytest.raises(GlmUpstreamError):
        client._json("POST", C.EP_CONV_LIST, body={"page": 1})
    assert len(calls) == 1
    client.close()
