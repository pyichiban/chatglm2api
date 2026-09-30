"""签名与请求头构造。

清言所有私有接口都要求签名三件套，缺一即 400：

    X-Timestamp  13 位毫秒时间戳（原样，**不需要**主仓 Go 侧那个校验位，见 PROTOCOL §2）
    X-Nonce      32 位小写 hex 随机串
    X-Sign       md5("<ts>-<nonce>-<SIGN_SECRET>") 小写 hex

复算证据（verify_sign.py，6/6 命中）：
    md5("1790732179905-75988225830f4d93b6702dcd0a2e9baf-8a1317a7468aa3ad86e997d08f3f31cb")
    = a32ee7a2876ad44e6658475f0a7c69eb

⚠️ **请求头必须完整对齐浏览器指纹**（2026-09-30 实测，见 README §4.4）：
阿里云 WAF 校验的是「头组合指纹」而非单个头。实测对照：
  - 浏览器完整头集 + 我们自己的 token/cookie/签名 → **稳定放行**（0.3~0.5s）
  - 少了 sec-ch-ua / Sec-Fetch-* / X-Exp-Groups / Accept-Encoding 任意一类
    → WAF 挂起连接（8s 读超时）或返回 40012
所以这里把抓包 sid 040 的全部请求头原样固化，缺一不可。
"""

import hashlib
import secrets
import time

from .constants import (APP_FR, APP_NAME, APP_PLATFORM, APP_VERSION, EXP_GROUPS,
                        SEC_CH_UA, SIGN_SECRET, USER_AGENT)

def sign_now(now_ms: int | None = None) -> tuple[str, str, str]:
    """生成 (timestamp, nonce, sign) 三件套。now_ms 可注入以便测试。"""
    ts = str(now_ms if now_ms is not None else int(time.time() * 1000))
    nonce = secrets.token_hex(16)  # 16 字节 → 32 位小写 hex
    sign = hashlib.md5(f"{ts}-{nonce}-{SIGN_SECRET}".encode()).hexdigest()
    return ts, nonce, sign


def build_headers(token: str = "", accept: str = "application/json, text/plain, */*",
                  device_id: str = "") -> dict[str, str]:
    """组装一次请求的完整头（**不含 Cookie**）。

    ⚠️ Cookie 必须交给 requests 的 cookie jar，**不要**在这里设显式 Cookie 头。
    显式 Cookie 头会覆盖 jar，使服务端下发/轮转的 WAF cookie（acw_tc）无法带回。

    ⚠️ 头集合必须与浏览器完全一致（WAF 组合指纹校验），不要随意删减。
    """
    ts, nonce, sign = sign_now()
    ua = USER_AGENT.replace("{device_id}", device_id) if device_id else         USER_AGENT.replace("DeviceId/{device_id}", "DeviceId/")
    h = {
        # 基础（IDE Electron 客户端实测值）
        "Accept": accept,
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN",
        "App-Name": APP_NAME,
        "Origin": "https://chatglm.cn",
        "User-Agent": ua,
        # 业务伪装头（IDE：win / 2.0.6）
        "X-App-Platform": APP_PLATFORM,
        "X-App-Version": APP_VERSION,
        "X-App-fr": APP_FR,
        "X-Lang": "zh",
        "X-Device-Model": "",
        "X-Device-Brand": "",
        "X-Exp-Groups": EXP_GROUPS,
        # 浏览器客户端提示（IDE 是 Electron/Chromium 150）
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        # 签名三件套
        "X-Timestamp": ts,
        "X-Nonce": nonce,
        "X-Sign": sign,
    }
    if token:
        h["Authorization"] = "Bearer " + token
    return h
