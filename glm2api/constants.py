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
# AI 画图智能体（IDE 抓包 sid 781：生图对话用它，body 差异只在 meta_data.cogview）
ASSISTANT_DRAWING = "65a232c082ff90a2ad2f15e2"
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

# ── 伪装头常量（IDE / Electron 客户端指纹）────────────────────
APP_NAME = "chatglm"
# ⚠️ 2026-09-30 实测（temp/saz_ide/raw/281_c.txt）：改用 IDE 指纹。
# 原因：WEB 浏览器通道被阿里云 WAF 新增 refer__991 动态参数校验（页面 JS 生成，
# 无法离线复刻），而 IDE 原生请求**不带该参数**、抓包 21 次 recent_list 零风控全过。
# IDE 与 WEB 共享同一后端与凭据体系（user/info、mainchat-api 全同名同格式）。
APP_PLATFORM = "win"          # IDE 为 win（WEB 浏览器为 pc）
APP_VERSION = "2.0.6"         # IDE 客户端版本（WEB 为 0.0.1）
APP_FR = "default"
# IDE 的 UA 是 Electron，中文品牌段被客户端自身写坏为 ????（抓包原样保留）。
# DeviceId 段在 build_headers 中用实际 device_id 填充。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) ????/2.0.6 Chrome/150.0.7871.250 Electron/43.6.0 "
    "Safari/537.36 Platform/ChatGLM_Win; AppVersion/2.0.6;"
    "DeviceId/{device_id};RefreshId/1790641614338"
)
# IDE 的 sec-ch-ua 是纯 Chromium v150（无 Edge 品牌）
SEC_CH_UA = '"Not;A=Brand";v="8", "Chromium";v="150"'

# 非流式请求的超时（连接 + 读取）。流式请求**刻意不设 timeout**（AGENTS.md R35）。
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 60

# ── 风控瞬时拒绝 → 退避重试 ─────────────────────────────────
# 清言走阿里云 WAF，POST 类接口会被瞬时拒绝（HTTP 400 + status=40012）。
# 实测原因（2026-09-30 新抓包）：所有请求的 URL query 必须带 refer__991=<JS生成的混淆串>。
# 缺此参数时 WAF 返回 40012；带上后秒通。该值由浏览器页面 JS 动态生成，保质期未知。
# 退避重试作为兜底：即使 refer__991 过期/缺失，合理间隔后也能通过。
STATUS_TRANSIENT = 40012
MAX_RETRIES = 4          # 重试次数上限（含首次共 5 次请求）
RETRY_BASE_DELAY = 1.0   # 退避基数（秒）：delay = base * 2**attempt * jitter

# ── 环境变量 ─────────────────────────────────────────────────
ENV_REFRESH_TOKEN = "GLM_REFRESH_TOKEN"
ENV_ACCESS_TOKEN = "GLM_ACCESS_TOKEN"
ENV_DEVICE_ID = "GLM_DEVICE_ID"
ENV_USER_ID = "GLM_USER_ID"
ENV_COOKIE = "GLM_COOKIE"
ENV_CRED_FILE = "GLM_CREDENTIAL_FILE"
ENV_REFER_991 = "GLM_REFER_991"  # 浏览器生成的 WAF 参数，从 URL query 复制（见 README §4.4）

# IDE 客户端实测的实验分组串（sid 281）。服务端不校验取值，但必须存在（WAF 指纹）。
EXP_GROUPS = (
    "na_android_config:exp:NA,na_4o_config:exp:4o_A,tts_config:exp:tts_config_a,"
    "na_glm4plus_config:exp:open,mainchat_server_app:exp:A,mobile_history_daycheck:exp:a,"
    "desktop_toolbar:exp:A,chat_drawing_server:exp:A,drawing_server_cogview:exp:cogview4,"
    "app_welcome_v2:exp:A,chat_drawing_streamv2:exp:A,mainchat_rm_fc:exp:add,"
    "mainchat_dr:exp:open,chat_auto_entrance:exp:A,drawing_server_hi_dream:control:A,"
    "homepage_square:exp:close,assistant_recommend_prompt:exp:3,app_home_regular_user:exp:A,"
    "mainchat_moe:exp:300,assistant_greet_user:exp:greet_user,"
    "app_welcome_personalize:exp:A,assistant_model_exp_group:exp:glm4.5,ai_wallet:exp:ai_wallet_enable"
)
