"""chatglm.cn 网页版私有接口常量。

来源：ref/glm-tool-proto/PROTOCOL.md（2026-09-29 Fiddler 抓包逆向结论）。
注意：本文件所有值均有抓包证据，改动前请回查协议文档。
"""

# 上游站点根
BASE = "https://chatglm.cn"

# ── 签名 ─────────────────────────────────────────────────────
# 清言网页版客户端内硬编码的签名密钥（[实证]，见 PROTOCOL §2）
SIGN_SECRET = "8a1317a7468aa3ad86e997d08f3f31cb"

# ── 默认业务参数 ──────────────────────────────────────────────
# 主对话（ChatGLM 智能体）的 assistant_id，注意它是「智能体 ID」而非模型 ID（PROTOCOL §4）
DEFAULT_ASSISTANT = "65940acff94777010aa6b796"
DEFAULT_MODEL = "glm-5.3-flash"
DEFAULT_PAGE_SIZE = 20          # recent_list 网页版固定值
DEFAULT_HISTORY_PAGE_SIZE = 30  # page_messages_v2 网页版固定值
CHAT_TYPE = "user_chat"

# ── 端点（统一前缀 BASE，全部需要签名头）──────────────────────
EP_REFRESH = "/chatglm/user-api/user/refresh"                      # POST，用 refresh 换 access，会轮换 refresh
EP_USER_INFO = "/chatglm/user-api/user/info"                       # GET
EP_MODELS = "/chatglm/agent-api/operation/detail"                  # GET ?tag=available_models
EP_CONV_LIST = "/chatglm/mainchat-api/conversation/recent_list"    # POST {page,page_size}
EP_CHAT = "/chatglm/backend-api/assistant/stream"                  # POST SSE
EP_HISTORY = "/chatglm/agent-api/conversation/page_messages_v2"    # GET ?assistant_id&conversation_id&page_size
# ⚠️ 删除会话的正确端点是 mainchat-api/conversation/bulk_delete（[实证] sid 289/323）。
#    主仓 internal/glm/constants.go 的 backend-api/.../conversation/delete 与实测不符，勿照抄。
EP_BULK_DELETE = "/chatglm/mainchat-api/conversation/bulk_delete"  # POST {conversation_ids,assistant_id}
EP_MODIFY_TITLE = "/chatglm/mainchat-api/conversation/modify_title"  # POST {conversation_id,title}
EP_CONV_TITLE = "/chatglm/mainchat-api/conversation/title"         # GET ?conversation_id
EP_UPDATE_STATUS = "/chatglm/mainchat-api/stream/update_status"    # POST {history_id}
EP_STOP_STREAM = "/chatglm/mainchat-api/stream/stop_stream"        # POST {history_id}

# ── 伪装头常量 ────────────────────────────────────────────────
APP_NAME = "chatglm"
APP_PLATFORM = "pc"
APP_VERSION = "0.0.1"
APP_FR = "default"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0"
)

# 非流式请求的超时（连接 + 读取）。流式请求**刻意不设 timeout**（AGENTS.md R35）。
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 60
