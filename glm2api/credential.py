"""凭据模型：解析 / 加载 / 落盘。

清言凭据本体是 ``chatglm_refresh_token``（唯一能换 access token 的根凭据，PROTOCOL §1.1）。
access token 可选，缺失时由 client.ensure_token 首次请求自动补齐（对齐主仓 R26）。

输入三种方式（优先级从高到低，见 load_credential）：
  1. 整条 Cookie 字符串（含 chatglm_refresh_token=...）—— 用户直接从浏览器复制
  2. 只给 refresh token 值（JWT 串），其它从 JWT 里解析
  3. JSON 文件 {refresh_token, access_token?, device_id?, user_id?, cookie?}

环境变量：GLM_REFRESH_TOKEN / GLM_ACCESS_TOKEN / GLM_DEVICE_ID / GLM_USER_ID / GLM_COOKIE
"""

import base64
import json
import os
import time
from dataclasses import dataclass, field

from .errors import GlmUpstreamError

# JWT 里 uid 与 Cookie chatglm_user_id / refresh 响应 user_id 三者同源（PROTOCOL §1.2）
COOKIE_KEYS = ("chatglm_token", "chatglm_refresh_token", "chatglm_user_id")

# 非凭据的 CDN/WAF 浏览器指纹 cookie：SAZ 中 0 次命中，值随浏览器动态生成，
# 复刻它们只会让项目变脆（PROTOCOL §1.4）。从额外 cookie 里剔除。
WAF_COOKIE_KEYS = ("acw_tc", "cdn_sec_tc", "chatglm_token_expires",
                   "_c_WBKFRo", "_nb_ioWEgULi")
WAF_COOKIE_PREFIXES = ("ssxmod", "_c_", "_nb_")


def decode_jwt_payload(token: str) -> dict:
    """仅 base64 解 JWT payload，**不验签、不校验**。

    失败返回空 dict（调用方可用它判断「这不是 JWT」）。
    """
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)  # 补齐 padding
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def parse_cookie(cookie: str) -> dict[str, str]:
    """把整条 Cookie 拆成 {name: value}，忽略 WAF/CDN 标记。"""
    out: dict[str, str] = {}
    for seg in (cookie or "").split(";"):
        if "=" not in seg:
            continue
        k, _, v = seg.partition("=")
        out[k.strip()] = v.strip()
    return out


@dataclass
class Credential:
    """一份账号凭据。字段与磁盘 JSON 一一对应。"""

    refresh_token: str = ""          # 根凭据，必需
    access_token: str = ""           # 可缺省，由 ensure_token 补
    device_id: str = ""              # 建议与 JWT.device_id 一致（PROTOCOL §1.2）
    user_id: str = ""                # 与 JWT.uid 同源
    # 浏览器粘贴的额外 cookie 键值（如 ssxmod_itna），非必需；WAF 标记不要往里塞
    extra_cookies: dict[str, str] = field(default_factory=dict)
    refer_991: str = ""            # 浏览器 JS 动态生成的 WAF 参数，从页面 URL query 复制
    source: str = ""                 # 加载来源（文件路径或 "env"），仅用于落盘与提示

    # ── 派生字段 ──────────────────────────────────────────────
    @property
    def access_exp(self) -> int:
        """access token 的 JWT exp（Unix 秒），非 JWT 或缺失返回 0。"""
        return int(decode_jwt_payload(self.access_token).get("exp") or 0)

    @property
    def refresh_exp(self) -> int:
        return int(decode_jwt_payload(self.refresh_token).get("exp") or 0)

    def access_expired(self, skew: int = 300) -> bool:
        """access 是否过期（默认提前 5 分钟视为过期，避免边界）。"""
        exp = self.access_exp
        return (not self.access_token) or exp <= int(time.time()) + skew

    def as_cookie(self) -> str:
        """拼出 Cookie 头：chatglm_token / chatglm_refresh_token / chatglm_user_id + 额外项。"""
        pairs = []
        if self.access_token:
            pairs.append(("chatglm_token", self.access_token))
        if self.refresh_token:
            pairs.append(("chatglm_refresh_token", self.refresh_token))
        if self.user_id:
            pairs.append(("chatglm_user_id", self.user_id))
        for k, v in self.extra_cookies.items():
            if k not in COOKIE_KEYS:
                pairs.append((k, v))
        return "; ".join(f"{k}={v}" for k, v in pairs)

    def absorb_jwt(self) -> None:
        """用 refresh（优先）或 access 的 JWT payload 补齐 uid / device_id。"""
        for tok in (self.refresh_token, self.access_token):
            p = decode_jwt_payload(tok)
            if p:
                self.user_id = self.user_id or str(p.get("uid") or "")
                self.device_id = self.device_id or str(p.get("device_id") or "")
                return

    def to_dict(self) -> dict:
        return {
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "device_id": self.device_id,
            "user_id": self.user_id,
            "extra_cookies": self.extra_cookies,
        }


def make_credential(*, refresh_token: str = "", access_token: str = "", device_id: str = "",
                    user_id: str = "", cookie: str = "", refer_991: str = "") -> Credential:
    """从「整条 cookie + 零散字段」构造凭据对象。"""
    ck = parse_cookie(cookie) if cookie else {}
    cred = Credential(
        refresh_token=(refresh_token or ck.get("chatglm_refresh_token", "")).strip(),
        access_token=(access_token or ck.get("chatglm_token", "")).strip(),
        device_id=device_id.strip(),
        user_id=(user_id or ck.get("chatglm_user_id", "")).strip(),
        # 只保留业务相关的额外 cookie，WAF 标记（ssxmod_itna / acw_tc / cdn_sec_tc 等）丢掉
        extra_cookies={k: v for k, v in ck.items()
                       if k not in COOKIE_KEYS + WAF_COOKIE_KEYS
                       and not k.startswith(WAF_COOKIE_PREFIXES)},
        refer_991=os.environ.get("GLM_REFER_991", ""),
    )
    cred.absorb_jwt()
    cred.refer_991 = refer_991 or os.environ.get("GLM_REFER_991", "")
    return cred


def load_credential(path: str | None = None, *, env: dict | None = None) -> Credential:
    """加载凭据。path 为空时尝试环境变量与默认路径。

    默认路径顺序：$GLM_CREDENTIAL_FILE → ./glm_credential.json
    """
    env = env if env is not None else os.environ
    path = path or env.get("GLM_CREDENTIAL_FILE") or ""

    if not path and os.path.exists("glm_credential.json"):
        path = "glm_credential.json"

    if path:
        cred = _load_file(path)
        cred.source = path
        # 文件缺字段时用 env 兜底
        cred.refresh_token = cred.refresh_token or env.get("GLM_REFRESH_TOKEN", "")
        cred.access_token = cred.access_token or env.get("GLM_ACCESS_TOKEN", "")
        cred.device_id = cred.device_id or env.get("GLM_DEVICE_ID", "")
        cred.user_id = cred.user_id or env.get("GLM_USER_ID", "")
    else:
        cred = make_credential(
            refresh_token=env.get("GLM_REFRESH_TOKEN", ""),
            access_token=env.get("GLM_ACCESS_TOKEN", ""),
            device_id=env.get("GLM_DEVICE_ID", ""),
            user_id=env.get("GLM_USER_ID", ""),
            cookie=env.get("GLM_COOKIE", ""),
        )
        cred.source = "env"

    cred.absorb_jwt()
    if not cred.refresh_token:
        raise GlmUpstreamError(0, "缺少 refresh_token：请设置 GLM_REFRESH_TOKEN 或提供凭据文件")
    if not cred.device_id:
        # device_id 非必需（服务端不校验），但缺省时给个稳定值便于日志对齐
        cred.device_id = ""
    return cred


def _load_file(path: str) -> Credential:
    with open(path, encoding="utf-8") as f:
        raw = f.read().strip()
    if not raw:
        raise GlmUpstreamError(0, f"凭据文件为空: {path}")
    if raw.startswith("{"):
        d = json.loads(raw)
        return make_credential(
            refresh_token=d.get("refresh_token", ""),
            access_token=d.get("access_token", ""),
            device_id=d.get("device_id", ""),
            user_id=d.get("user_id", ""),
            cookie=d.get("cookie", ""),
            refer_991=d.get("refer_991", ""),
        )
    # 纯文本：整条 cookie 或裸 refresh token，两种都支持
    return make_credential(cookie=raw, refresh_token="" if "=" in raw else raw)


def save_credential(cred: Credential, path: str | None = None) -> str:
    """把凭据写回文件（原子替换）。

    ⚠️ **refresh token 每次刷新都会轮换，必须落盘**（PROTOCOL §1.3 铁律 1 / AGENTS.md 不变量 19-20）。
    不落盘 = 下次启动拿旧 refresh token，账号直接失效。
    """
    path = path or cred.source
    if not path or path == "env":
        raise GlmUpstreamError(0, "凭据来源不是文件，无法落盘：请通过 GLM_CREDENTIAL_FILE 指定路径")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cred.to_dict(), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子替换，避免写一半被杀导致凭据损坏
    return path
