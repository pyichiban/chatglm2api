"""签名与请求头构造。

清言所有私有接口都要求签名三件套，缺一即 400：

    X-Timestamp  13 位毫秒时间戳（原样，**不需要**主仓 Go 侧那个校验位，见 PROTOCOL §2）
    X-Nonce      32 位小写 hex 随机串
    X-Sign       md5("<ts>-<nonce>-<SIGN_SECRET>") 小写 hex

复算证据（verify_sign.py，6/6 命中）：
    md5("1790732179905-75988225830f4d93b6702dcd0a2e9baf-8a1317a7468aa3ad86e997d08f3f31cb")
    = a32ee7a2876ad44e6658475f0a7c69eb
"""

import hashlib
import secrets
import time

from .constants import APP_FR, APP_NAME, APP_PLATFORM, APP_VERSION, SIGN_SECRET, USER_AGENT


def sign_now(now_ms: int | None = None) -> tuple[str, str, str]:
    """生成 (timestamp, nonce, sign) 三件套。now_ms 可注入以便测试。"""
    ts = str(now_ms if now_ms is not None else int(time.time() * 1000))
    nonce = secrets.token_hex(16)  # 16 字节 → 32 位小写 hex
    sign = hashlib.md5(f"{ts}-{nonce}-{SIGN_SECRET}".encode()).hexdigest()
    return ts, nonce, sign


def build_headers(token: str = "", cookie: str = "", accept: str = "application/json, text/plain, */*") -> dict[str, str]:
    """组装一次请求的完整头。

    token 为空时不带 Authorization（一般只有 refresh 之外的调用才会出现）。
    cookie 由调用方按当前凭据拼好（见 credential.py 的 as_cookie）。
    """
    ts, nonce, sign = sign_now()
    h = {
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": accept,
        "App-Name": APP_NAME,
        "Origin": "https://chatglm.cn",
        "Referer": "https://chatglm.cn/main/alltoolsdetail",
        "User-Agent": USER_AGENT,
        "X-App-Platform": APP_PLATFORM,
        "X-App-Version": APP_VERSION,
        "X-App-fr": APP_FR,
        "X-Lang": "zh",
        "X-Device-Model": "",
        "X-Device-Brand": "",
        "X-Timestamp": ts,
        "X-Nonce": nonce,
        "X-Sign": sign,
    }
    if token:
        h["Authorization"] = "Bearer " + token
    if cookie:
        h["Cookie"] = cookie
    return h
