"""chatglm2api —— 智谱清言（chatglm.cn）私有接口的 Python 客户端（IDE 通道）。

定位：**Go 版兄弟仓库 chatglm-cli 的子集**，库形态（程序内嵌调用）；接口契约见
``tests/fixtures/contract.json``（与 Go 仓逐字节一致的跨语言契约）。
纯 API 层：不涉及登录流程逆向，凭据由用户从浏览器手工提供。
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
ASSISTANT_DRAWING = constants.ASSISTANT_DRAWING

__all__ = [
    "GlmClient", "Credential", "load_credential", "make_credential", "save_credential",
    "GlmError", "GlmAuthError", "GlmRateLimitError", "GlmUpstreamError", "GlmStreamTruncated",
    "GlmTransientRejection",
    "ChatAggregate", "BASE", "DEFAULT_ASSISTANT", "DEFAULT_MODEL", "ASSISTANT_DRAWING", "constants",
    "__version__",
]
