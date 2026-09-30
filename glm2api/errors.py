"""异常类型。

分三类，调用方可据此决定是否重试 / 是否 refresh：
  GlmAuthError      凭据失效（401 / 40102），需要 refresh 或重新取 Cookie
  GlmRateLimitError 限流（HTTP 429 / 业务限流码）
  GlmUpstreamError  其它上游错误（含业务 status != 0、HTTP >= 400、WAF 拦截页）

流被截断单独用 GlmStreamTruncated 表示——**不得**把它伪装成正常收尾（AGENTS.md R36）。
"""


class GlmError(Exception):
    """所有清言客户端异常的基类。"""


class GlmAuthError(GlmError):
    """登录态失效：HTTP 401 或业务码 40102 / 40103。"""


class GlmRateLimitError(GlmError):
    """被限流：HTTP 429。"""


class GlmUpstreamError(GlmError):
    """上游返回业务错误或非法响应。"""

    def __init__(self, status: int, message: str, body: str = ""):
        self.status = status        # HTTP 状态码；业务错误时为响应 HTTP 码
        self.message = message      # 信封 message 或解析出的错误文案
        self.body = body            # 原始响应体（截断），便于排查
        super().__init__(f"上游错误 HTTP {status}: {message}")


class GlmTransientRejection(GlmUpstreamError):
    """风控瞬时拒绝（HTTP 400 + status=40012）且已耗尽重试。

    它**不是**永久失败：实测退避重试通常能成功（见 constants.STATUS_TRANSIENT）。
    抛出这个类型意味着调用方可以过后重试，或降低请求频率。
    """


class GlmStreamTruncated(GlmError):
    """SSE 流在传输层被截断（读错误 / 半帧），而非上游正常收尾。

    上游正常收尾的标志是顶层 ``status:"finish"``（清言**不发** ``data: [DONE]``）。
    抛此异常即表示「回答不完整」，调用方必须把它当错误处理，不得补结束帧。
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"SSE 流被截断（未收到 finish 帧）: {reason}")
