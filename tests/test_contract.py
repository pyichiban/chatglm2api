"""跨语言契约测试：断言 Python 实现与 contract.json（同一份文件也被 Go 实现断言）一致。

设计要点：
  * contract.json 的 fixtures 期望值由 **另一套实现（Go）+ 真实抓包帧** 反推，不是自我复制；
  * known_divergences 显式记录两边当前不一致之处，各自断言自己那一项 ⇒
    谁改动行为就会在这里失败，逼迫同步更新契约，而不是悄悄漂移；
  * 全程不发网络请求：HTTP 层用假 session 替身，SSE 用仓库内 fixture 回放。
"""

import base64
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glm2api import constants as C                                         # noqa: E402
from glm2api import client as client_mod                                   # noqa: E402
from glm2api.client import GlmClient, build_chat_body, unwrap              # noqa: E402
from glm2api.credential import (COOKIE_KEYS, WAF_COOKIE_KEYS,               # noqa: E402
                                WAF_COOKIE_PREFIXES, load_credential,
                                make_credential, save_credential)
from glm2api.errors import (GlmAuthError, GlmRateLimitError,                # noqa: E402
                            GlmStreamTruncated, GlmTransientRejection,
                            GlmUpstreamError)
from glm2api.sign import build_headers, sign_now                           # noqa: E402
from glm2api.sse import (ChatAggregate, ensure_finished, iter_sse_data,     # noqa: E402
                         parse_frame, render_part)

FIXDIR = Path(__file__).parent / "fixtures"
CONTRACT = json.loads((FIXDIR / "contract.json").read_text(encoding="utf-8"))
MD_IMG = re.compile(r"\s*!\[\]\(https://[^)]+\)")


@pytest.fixture(scope="module")
def ct():
    return CONTRACT


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16] if s else ""


def _jwt(payload: dict) -> str:
    b = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{b}.sig"


# ── fixture 完整性：两个仓库必须回放**逐字节相同**的真实帧 ──────────
@pytest.mark.parametrize("name,sha256", sorted(CONTRACT["fixtures"]["sha256"].items()))
def test_fixture_bytes_are_the_shared_ones(name, sha256):
    got = hashlib.sha256((FIXDIR / name).read_bytes()).hexdigest()
    assert got == sha256, f"{name} 与契约里的 sha256 不一致 ⇒ 两边回放的已经不是同一份流量"


# ── 常量 / 端点 / 码值 ──────────────────────────────────────
def test_endpoints_match_contract(ct):
    got = {"refresh": C.EP_REFRESH, "user_info": C.EP_USER_INFO, "models": C.EP_MODELS,
           "conv_list": C.EP_CONV_LIST, "chat": C.EP_CHAT, "history": C.EP_CONV_MESSAGES,
           "history_v2_dead": C.EP_HISTORY, "bulk_delete": C.EP_BULK_DELETE,
           "modify_title": C.EP_MODIFY_TITLE, "conv_title": C.EP_CONV_TITLE,
           "update_status": C.EP_UPDATE_STATUS, "stop_stream": C.EP_STOP_STREAM,
           "image_reference": C.EP_IMAGE_REFERENCE}
    assert got == ct["endpoints"]


def test_defaults_codes_timeouts(ct):
    assert C.BASE == ct["defaults"]["base"]
    assert C.DEFAULT_MODEL == ct["defaults"]["model"]
    assert C.CHAT_TYPE == ct["defaults"]["chat_type"]
    assert C.DEFAULT_PAGE_SIZE == ct["defaults"]["page_size"]
    assert C.DEFAULT_HISTORY_PAGE_SIZE == ct["defaults"]["history_page_size_v2"]
    assert C.STATUS_TRANSIENT == ct["codes"]["transient"]
    assert list(client_mod.AUTH_STATUS) == ct["codes"]["auth"]
    assert (C.CONNECT_TIMEOUT, C.READ_TIMEOUT) == (ct["timeouts"]["connect_sec"], ct["timeouts"]["read_sec"])
    assert C.SIGN_SECRET == ct["sign"]["secret"]
    assert C.DEFAULT_ASSISTANT == ct["agents"]["main"]
    assert C.ASSISTANT_DRAWING == ct["agents"]["drawing"]


def test_cookie_and_env_names(ct):
    assert list(COOKIE_KEYS) == ct["cookies"]["business"]
    assert sorted(WAF_COOKIE_KEYS) == sorted(ct["cookies"]["waf_drop_keys"])
    assert list(WAF_COOKIE_PREFIXES) == ct["cookies"]["waf_drop_prefix"]
    got = {"refresh_token": C.ENV_REFRESH_TOKEN, "access_token": C.ENV_ACCESS_TOKEN,
           "device_id": C.ENV_DEVICE_ID, "user_id": C.ENV_USER_ID, "cookie": C.ENV_COOKIE,
           "credential_file": C.ENV_CRED_FILE}
    assert got == ct["env"]


# ── 签名与 IDE 指纹 ─────────────────────────────────────────
def test_sign_vector(ct):
    v = ct["sign"]["vector"]
    assert sign_now(now_ms=int(v["ts"]))[0] == v["ts"]
    assert hashlib.md5(f"{v['ts']}-{v['nonce']}-{C.SIGN_SECRET}".encode()).hexdigest() == v["sign"]


def test_ide_fingerprint_headers(ct):
    h = build_headers(token="T", accept="text/event-stream", device_id="dev1")
    assert sorted(h) == sorted(ct["channel"]["header_names"]), "请求头集合与契约不一致"
    assert h["App-Name"] == ct["channel"]["app_name"]
    assert h["X-App-Platform"] == ct["channel"]["app_platform"]        # IDE 通道 = win
    assert h["X-App-Version"] == ct["channel"]["app_version"]
    assert h["X-App-fr"] == ct["channel"]["app_fr"]
    assert h["sec-ch-ua"] == ct["channel"]["sec_ch_ua"]
    for m in ct["channel"]["ua_markers"]:
        assert m in h["User-Agent"], m
    for k in ct["channel"]["never_headers"]:                            # Cookie 必须交给 jar
        assert k not in h
    assert len(h["X-Nonce"]) == ct["sign"]["nonce_len"]
    assert len(h["X-Timestamp"]) == ct["sign"]["ts_len"]
    assert len(h["X-Sign"]) == 32


def test_per_request_extra_headers(ct):
    cred = make_credential(refresh_token=_jwt({"uid": "u" * 24, "device_id": "dev9"}))
    cred.access_token = _jwt({"exp": 4102444800, "device_id": "dev9"})
    cl = GlmClient(cred)
    hh = cl._headers("application/json")
    for k in ct["channel"]["per_request_extra_headers"]:
        assert k in hh, k
    assert hh["X-Device-Id"] == "dev9"                                  # 与 JWT.device_id 同源
    assert len(hh["X-Request-Id"]) == 32
    assert "Cookie" not in hh
    cl.close()


def test_divergence_v3_accept_encoding(ct):
    """V3：Python 发 br/zstd，Go 只发 gzip/deflate。改任一边都要先改契约。"""
    assert build_headers()["Accept-Encoding"] == "gzip, deflate, br, zstd"
    d = next(x for x in ct["known_divergences"] if x["id"] == "V3")
    assert "br" in d["python"] and "br" not in d["go"]


# ── HTTP 替身 ───────────────────────────────────────────────
class FakeResp:
    def __init__(self, status=200, payload=None, raw=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._raw = raw if raw is not None else json.dumps(payload if payload is not None
                                                            else {"status": 0, "result": {}})
        self.text = self._raw

    @property
    def content(self):
        return self._raw.encode("utf-8")

    def json(self):
        return json.loads(self._raw)

    def iter_lines(self):
        return iter(self._raw.splitlines())

    def iter_content(self, chunk_size=None):
        yield self.content

    def close(self):
        pass


class FakeJar:
    def __init__(self):
        self.store = {}

    def pop(self, k, default=None):
        return self.store.pop(k, default)

    def set(self, k, v, domain=None, path=None):
        self.store[(k, domain, path)] = v

    def __iter__(self):
        return iter(self.store)


class FakeSession:
    """记录请求，并按队列返回预设响应（dict 自动包成 FakeResp）。"""

    def __init__(self, resps=()):
        self.resps = [r if isinstance(r, FakeResp) else FakeResp(200, r) for r in resps]
        self.calls = []
        self.cookies = FakeJar()
        self.proxies = {}
        self.verify = True

    def _next(self):
        assert self.resps, "测试预设响应已用完"
        return self.resps.pop(0)

    def request(self, method, url, headers=None, data=None, params=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params or {},
                           "body": json.loads(data) if data else None, "headers": headers or {},
                           "timeout": timeout})
        return self._next()

    def post(self, url, headers=None, data=None, params=None, timeout=None, stream=None):
        self.calls.append({"method": "POST", "url": url, "params": params or {},
                           "body": json.loads(data) if data else None, "headers": headers or {},
                           "timeout": timeout, "stream": stream})
        return self._next()

    def close(self):
        pass


def _client(resps=None, **kw):
    cred = make_credential(refresh_token=_jwt({"uid": "u" * 24, "device_id": "d" * 32}))
    cred.access_token = _jwt({"exp": 4102444800, "uid": "u" * 24, "device_id": "d" * 32})
    cl = GlmClient(cred, **kw)
    cl.session = FakeSession(resps if resps is not None else [{"status": 0, "result": {}}])
    cl.retry_base_delay = 0.0001                     # 命中退避用例时不真等
    return cl


# ── 请求体形状 ─────────────────────────────────────────────
def test_chat_body_first_turn_and_multiturn(ct):
    b = build_chat_body(C.DEFAULT_ASSISTANT, "你好", "")
    assert sorted(b) == sorted(ct["chat_body"]["top_keys"])
    assert b["conversation_id"] == ct["chat_body"]["first_turn_conversation_id"]
    assert b["project_id"] == "" and b["chat_type"] == C.CHAT_TYPE
    assert len(b["messages"]) == ct["chat_body"]["messages_len"]
    assert b["messages"][0] == {"role": "user", "content": [{"type": "text", "text": "你好"}]}
    assert sorted(b["meta_data"]) == sorted(ct["chat_body"]["meta_keys"])
    assert b["meta_data"]["platform"] == ct["defaults"]["meta_platform"]  # 头 win / body pc：服务端不校验
    assert sorted(b["meta_data"]["cogview"]) == sorted(ct["chat_body"]["cogview_base_keys"])
    assert "reasoning_effort" not in b["meta_data"]                        # 可选：不传就不发

    b2 = build_chat_body(C.DEFAULT_ASSISTANT, "第二问", "a" * 24, reasoning_effort="high")
    assert b2["conversation_id"] == "a" * 24 and len(b2["messages"]) == 1
    assert b2["meta_data"]["reasoning_effort"] == "high"

    with pytest.raises(GlmUpstreamError):                                   # 客户端拼历史必须被拒
        build_chat_body(C.DEFAULT_ASSISTANT, [{"content": "a"}, {"content": "b"}])


def test_generate_image_body(ct):
    """生图 = 对话通道换 agent + cogview（V7：Python 总带 style/scene）。"""
    cl = _client()
    sent = {}
    cl.chat = lambda aid, msg, **kw: (sent.update(aid=aid, kw=kw), {"text": ""})[1]
    cl.generate_image("画只猫", aspect_ratio="3:4")
    assert sent["aid"] == ct["agents"]["drawing"]
    cw = sent["kw"]["extra_meta"]["cogview"]
    assert sorted(cw) == sorted(ct["chat_body"]["cogview_base_keys"] + ct["chat_body"]["cogview_drawing_keys"])
    assert cw["aspect_ratio"] == "3:4" and cw["style"] == "none" and cw["scene"] == "none"
    cl.close()


def test_request_shapes(ct):
    """逐个端点断言 method / path / query / body 键集合。"""
    shapes = ct["request_shapes"]
    cases = [
        ("history", lambda c: c.history("c" * 24, assistant_id="a" * 24), shapes["history"]),
        ("history_v2", lambda c: c.history_v2("c" * 24), shapes["history_v2"]),
        ("conv_list", lambda c: c.list_conversations(), shapes["conv_list"]),
        ("rename", lambda c: c.rename_conversation("c" * 24, "T"), shapes["rename"]),
        ("conv_title", lambda c: c.conversation_title("c" * 24), shapes["conv_title"]),
        ("bulk_delete", lambda c: c.delete_conversations(["c" * 24]), shapes["bulk_delete"]),
        ("stop_stream", lambda c: c.stop_stream("h" * 24), shapes["stop_stream"]),
        ("update_status", lambda c: c.update_status("h" * 24), shapes["update_status"]),
    ]
    for name, call, want in cases:
        cl = _client()
        call(cl)
        c0 = cl.session.calls[-1]
        ep = CONTRACT["endpoints"][want["endpoint"]]
        assert c0["method"] == want["method"], name
        assert c0["url"].endswith(ep), (name, c0["url"])
        if "query" in want:
            assert sorted(c0["params"]) == sorted(want["query"]), (name, c0["params"])
        if "body_keys" in want:
            assert sorted(c0["body"]) == sorted(want["body_keys"]), (name, c0["body"])
        if want.get("pagination_params") == []:
            assert "page" not in c0["params"] and "page_size" not in c0["params"], name
        cl.close()


def test_stream_request_has_no_timeout(ct):
    """契约铁律：流式请求不带任何超时（带长回答会被掐断且没有终止帧）。"""
    cl = _client([FakeResp(200, raw="data: {}\n\n", headers={"Content-Type": "text/event-stream"})])
    with cl._stream_response({"assistant_id": C.DEFAULT_ASSISTANT}):
        pass
    c0 = cl.session.calls[-1]
    assert c0["timeout"] == ct["timeouts"]["stream_total_timeout"]      # None
    assert c0["stream"] is True
    assert c0["headers"]["Accept"] == "text/event-stream"
    assert c0["url"].endswith(CONTRACT["endpoints"]["chat"])
    cl.close()


def test_envelope_error_mapping(ct):
    # 登录态失效
    cl = _client([{"status": 40103, "message": "user needs login"}])
    with pytest.raises(GlmAuthError):
        cl.user_info()
    cl.close()
    # 429 限流
    cl = _client([FakeResp(429, {"status": 429, "message": "too many"})])
    with pytest.raises(GlmRateLimitError):
        cl.user_info()
    cl.close()
    # 非 JSON（WAF 拦截页）
    cl = _client([FakeResp(200, raw="<html>waf blocked</html>")])
    with pytest.raises(GlmUpstreamError):
        cl.user_info()
    cl.close()
    # 其它业务错误
    with pytest.raises(GlmUpstreamError):
        unwrap({"status": 1, "message": "invalid"})
    assert unwrap({"status": 0, "result": {"a": 1}}) == {"a": 1}


def test_transient_rejection_retries(ct):
    """V1：Python 命中 40012 会指数退避重试 MAX_RETRIES 次；Go 不重试直接报错。"""
    cl = _client([FakeResp(400, {"status": 40012, "message": "bad request"})] * 9)
    with pytest.raises(GlmTransientRejection):
        cl.user_info()
    assert len(cl.session.calls) == C.MAX_RETRIES + 1
    d = next(x for x in ct["known_divergences"] if x["id"] == "V1")
    assert str(C.MAX_RETRIES) in d["python"] and "不重试" in d["go"]
    cl.close()


def test_401_self_heals_exactly_once():
    """HTTP 401 ⇒ 强制刷新后只重一次（这是凭据自愈，不是退避重试）。"""
    cl = _client([FakeResp(401, {"status": 401, "message": "no"}),
                  {"status": 0, "result": {"ok": True}}])
    n = {"refresh": 0}

    def fake_refresh():
        n["refresh"] += 1
        cl.cred.access_token = _jwt({"exp": 4102444800})

    cl.refresh = fake_refresh
    assert cl.user_info() == {"ok": True}
    assert n["refresh"] == 1
    assert len([c for c in cl.session.calls if c["url"].endswith(C.EP_USER_INFO)]) == 2
    cl.close()


# ── refresh 与凭据持久化 ───────────────────────────────────
def test_refresh_shape_and_persist(ct, tmp_path):
    """refresh 用 refresh_token 当 Authorization、body={}、轮换后的新 refresh 必须落盘。"""
    credfile = tmp_path / "cred.json"
    credfile.write_text(json.dumps({"refresh_token": "old-root-token",
                                    "refer_991": "python-drops-this"}), encoding="utf-8")
    cred = load_credential(str(credfile))
    cl = GlmClient(cred)
    new_access = _jwt({"exp": 4102444800, "uid": "u" * 24})
    new_root = _jwt({"exp": 4102444800, "uid": "u" * 24})

    fake = FakeSession([{"status": 0, "result": {"access_token": new_access,
                                                 "refresh_token": new_root, "user_id": "u" * 24}}])
    real_session = client_mod.requests.Session
    client_mod.requests.Session = lambda *a, **k: fake            # refresh 内部自建 Session
    try:
        cl.refresh()
    finally:
        client_mod.requests.Session = real_session

    c0 = fake.calls[-1]
    path, _, query = c0["url"].partition("?")
    assert path.endswith(CONTRACT["endpoints"][ct["request_shapes"]["refresh"]["endpoint"]])
    # V2 差异：Python 仍支持 refer__991（凭据里有就拼进 query），Go 已彻底移除
    assert "refer__991=" in query
    d2 = next(x for x in ct["known_divergences"] if x["id"] == "V2")
    assert "refer_991" in d2["python"] and "移除" in d2["go"]
    assert c0["headers"]["Authorization"] == "Bearer old-root-token"   # 根凭据放 Authorization
    assert c0["body"] == ct["request_shapes"]["refresh"]["body"]
    assert cred.refresh_token == new_root
    on_disk = json.loads(credfile.read_text(encoding="utf-8"))
    assert on_disk["refresh_token"] == new_root                  # 落了盘（铁律 1）
    assert sorted(on_disk) == sorted(ct["credential_file"]["keys"])
    cl.close()


def test_save_requires_file_source(ct):
    """来源是 env（无文件）时必须显式报错，而不是静默丢弃轮换后的根凭据。"""
    cred = make_credential(refresh_token="x")
    assert cred.source in ("", "env")
    with pytest.raises(GlmUpstreamError):
        save_credential(cred)


# ── SSE 语义（Go 断言同一批数字）───────────────────────────
def _feed(name):
    lines = (FIXDIR / name).read_text(encoding="utf-8").splitlines()
    agg = ChatAggregate()
    for payload in iter_sse_data(lines):
        if (obj := parse_frame(payload)):
            agg.feed(obj)
    return agg, agg.as_result()


@pytest.mark.parametrize("name", ["stream_first.txt", "stream_error.txt", "stream_drawing.txt"])
def test_sse_contract(name, ct):
    want = ct["fixtures"][name]
    agg, r = _feed(name)
    assert r["conversation_id"] == want["conversation_id"]
    assert r["history_id"] == want["history_id"]
    assert r["frames"] == want["frames"]
    assert r["finished"] is want["finished"]
    assert (r["error"] or {}).get("error_code") == want["error_code"]
    text = MD_IMG.sub("", r["text"])                    # V6：Python 把图链拼进 text ⇒ 归一后再比
    assert (len(text), _sha(text)) == (want["text_len"], want["text_sha"])
    assert text[:len(want["text_head"])] == want["text_head"]
    assert (len(r["thinking"]), _sha(r["thinking"])) == (want["reasoning_len"], want["reasoning_sha"])
    images = re.findall(r"!\[\]\((https://[^)]+)\)", r["text"])
    assert (len(images), _sha("|".join(images))) == (want["image_count"], want["image_sha"])
    assert (images[0][:48] if images else "") == want["image_url_head"]


def test_finish_only_short_answer_is_not_lost(ct):
    """契约里 stream_first 的 point：短回答只有 finish 帧，聚合必须拿到全文。"""
    assert "finish" in ct["fixtures"]["stream_first.txt"]["point"]
    _, r = _feed("stream_first.txt")
    assert r["text"] and "你好" in r["text"] and r["thinking"]
    assert len(r["parts"]) == 3                        # think + tool_calls + text
    assert len([p for p in r["parts"] if p["text"] or p["thinking"]]) == 2


def test_bare_envelope_line_is_ignored(ct):
    """生图流末尾的裸 JSON 信封不带 data: 前缀 ⇒ 不该进聚合（frames 恰为 6 已隐含验证）。"""
    lines = (FIXDIR / "stream_drawing.txt").read_text(encoding="utf-8").splitlines()
    payloads = list(iter_sse_data(lines))
    assert len(payloads) == ct["fixtures"]["stream_drawing.txt"]["frames"]
    assert not any(p.lstrip().startswith('{"status":0,"message":"success"') for p in payloads)
    assert all(parse_frame(p) and parse_frame(p).get("conversation_id") for p in payloads)


def test_truncated_stream_raises(ct):
    assert ct["sse_rules"]["truncation"].startswith("未见 finish/error")
    agg = ChatAggregate()
    agg.feed(json.loads('{"status":"init","parts":[{"logic_id":"L",'
                        '"content":[{"type":"text","text":"半句"}]}]}'))
    with pytest.raises(GlmStreamTruncated):
        ensure_finished(agg)


def test_no_done_sentinel(ct):
    """上游不发 [DONE]；即使出现也要被当成噪声丢掉。"""
    assert ct["sse_rules"]["done_sentinel"] is False and ct["sse_rules"]["usage_fields"] is None
    assert [p for p in iter_sse_data(["data: [DONE]", ""])] == ["[DONE]"]
    assert parse_frame("[DONE]") is None                              # 解帧阶段被丢弃
    assert parse_frame('{"status":0,"message":"success","result":null,"rid":"x"}') is not None


def test_render_part_type_buckets(ct):
    rules = ct["sse_rules"]
    for t in rules["content_text_types"]:
        txt, think, _ = render_part({"status": "init", "content": [{"type": t, "text": "X", "code": "Y"}]})
        assert txt == "X" and think == ""
    for t in rules["content_think_types"]:
        txt, think, _ = render_part({"status": "init", "content": [{"type": t, "think": "T", "content": "C"}]})
        assert think == "T" and txt == ""
    txt, _, _ = render_part({"status": "init", "content": [{"type": "image", "image": [{}]}]})
    assert txt == ""                                   # one_to_more_finish 的空对象被跳过


def test_part_key_precedence(ct):
    assert ct["sse_rules"]["part_key_precedence"] == ["logic_id", "id", "#index"]
    from glm2api.sse import _part_key
    assert _part_key({"logic_id": "L", "id": "I"}, 0) == "L"
    assert _part_key({"id": "I"}, 3) == "I"
    assert _part_key({}, 3) == "#3"


# ── 历史端点形状 ────────────────────────────────────────────
def test_history_fixture_matches_contract(ct):
    want = ct["fixtures"]["history.json"]
    d = json.loads((FIXDIR / "history.json").read_text(encoding="utf-8"))
    assert d["conversation_id"] == want["conversation_id"]
    assert [m["id"] for m in d["messages"]] == want["message_ids"]
    assert len(d["messages"]) == want["turns"]
    assert d["messages"][0]["create_time"] == want["created_at_head"]
    pairs = []
    for m in d["messages"]:
        user = "".join(c.get("text", "") for c in m["input"]["content"] if c.get("type") == "text")
        bot = "".join(c.get("text", "") for p in m["output"]["parts"]
                      for c in p.get("content", []) if c.get("type") == "text")
        pairs.append([user[:20], bot[:20]])
    assert pairs == want["pairs"]


def test_python_result_keys(ct):
    """Python 聚合结果的键集合（Go 侧是 JSONL 事件，见 cli_event_contract）。"""
    _, r = _feed("stream_first.txt")
    assert sorted(r) == sorted(ct["python_result_keys"])


def test_cli_event_contract_shape(ct):
    """CLI 事件契约由 Go 侧执行；Python 至少校验契约自身的完整性。"""
    ec = ct["cli_event_contract"]
    assert ec["version"] == 1 and ec["first_event"] == "meta"
    assert set(ec["types"]) == set(ec["fields"]) | {"item", "result", "warn"}
    for t, keys in ec["fields"].items():
        assert keys[0] == "v" and keys[1] == "type", t
        assert "request_id" in keys, t


def test_known_divergences_are_complete(ct):
    """每条差异都要有 id/topic/python/go 四项，且 id 唯一（对齐后应删除该条并更新契约）。"""
    for d in ct["known_divergences"]:
        assert {"id", "topic", "python", "go"} <= set(d), d
        assert d["python"].strip() and d["go"].strip()
    ids = [d["id"] for d in ct["known_divergences"]]
    assert len(ids) == len(set(ids)), "差异条目 id 重复"
    nums = [int(i[1:]) for i in ids]
    assert nums == sorted(nums), "差异条目编号必须有序"
