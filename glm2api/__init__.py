"""chatglm2api —— 智谱清言（chatglm.cn）网页版私有接口的独立 Python 客户端。

纯 API 层实现：不涉及登录流程逆向，凭据由用户从浏览器手工提供。
能力范围见 README.md（含明确的「不做」清单与已知缺口）。
"""

from .client import GlmClient
from .credential import Credential, load_credential, make_credential, save_credential
from .errors import (GlmAuthError, GlmError, GlmRateLimitError, GlmStreamTruncated,
                     GlmTransientRejection, GlmUpstreamError)
from .sse import ChatAggregate

__version__ = "0.1.0"

# 常量便捷导出
from . import constants

BASE = constants.BASE
DEFAULT_ASSISTANT = constants.DEFAULT_ASSISTANT
DEFAULT_MODEL = constants.DEFAULT_MODEL

__all__ = [
    "GlmClient", "Credential", "load_credential", "make_credential", "save_credential",
    "GlmError", "GlmAuthError", "GlmRateLimitError", "GlmUpstreamError", "GlmStreamTruncated",
    "GlmTransientRejection",
    "ChatAggregate", "BASE", "DEFAULT_ASSISTANT", "DEFAULT_MODEL", "constants",
    "__version__",
]
