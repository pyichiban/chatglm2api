# chatglm.cn 网页版私有 API 逆向结果（独立 Python 项目用）

> 来源：`ref/chatglm_web_20260929.saz`（511 会话，Fiddler 抓包，2026-09-29）
> 解压目录：`ref/chatglm_web_20260929_extracted/`（`raw/NN_*` 原始报文、`bodies/NN.body` 解压后的响应体、`timeline.txt` 埋点时间线）
> 分析脚本（可重跑）：`outline` 见 `ref/chatglm_web_outline.tsv`；`parse_telemetry.py`、`verify_sign.py`、`check_creds.py`、`decode_headers.py` 均在解压目录下
>
> **标注约定**：`[实证]` = 抓包/复算直接证据；`[推断]` = 由相邻证据推出，未经直接验证；`[未知]` = 无证据。

---

## 1. 凭据模型

### 1.1 三个关键凭据

| 凭据 | 位置 | 作用 | 实测 |
|---|---|---|---|
| `chatglm_refresh_token` | Cookie | **根凭据**，唯一能换 access token | `[实证]` JWT `type:"refresh"`，`exp-iat = 180 天` |
| `chatglm_token`（access） | Cookie，与 `Authorization: Bearer` **同值** | 日常调用 | `[实证]` JWT `type:"access"`，`exp-iat = 24h`；SAZ 中 94 个请求二者逐字节相同 |
| `X-Sign` / `X-Nonce` / `X-Timestamp` | 请求头 | 私有接口签名门禁 | `[实证]` 见 §2 |

### 1.2 JWT 载荷（access）

```json
{"alg":"HS256","typ":"JWT"}
{"sub":"用户_xxxxxx","exp":...,"nbf":...,"iat":...,
 "jti":"...","uid":"6982e0ad...","device_id":"d4f911af...","type":"access"}
```

- `uid` = 账号 ID，与 Cookie `chatglm_user_id`、`user/refresh` 响应的 `user_id` 三者同源。
- `device_id` = 服务端写入的设备号。**请求头 `X-Device-Id` 与它一致**（`[实证]` SAZ 110 处一致）。
- `sub` 是昵称，非稳定标识，**不要用它当账号 ID**。

### 1.3 刷新（唯一的 token 交换点）

```
POST /chatglm/user-api/user/refresh
Authorization: Bearer <chatglm_refresh_token>     ← 注意：refresh 放在 Authorization
Content-Type: application/json;charset=UTF-8
+ 全部签名头
body: {}
```

响应 `[实证]`：
```json
{"status":0,"message":"success","result":{
  "user_id":"...","access_token":"<JWT>","refresh_token":"<JWT>","is_guest":false,"mode":1}}
```

**两条铁律**：
1. **refresh token 会轮换** —— 响应里的新 `refresh_token` 必须落盘覆盖，否则下次用旧值必失败。
2. 全 SAZ 只有这 **1 个**请求用 refresh 当 Authorization，其余 94 个业务请求全用 access。`[实证]`

### 1.4 明确**不是**凭据的东西（不要复刻）

以下在用户提供的浏览器请求头里出现过，但均在 SAZ 中 **0 次命中**，且与本项目实现无关，属阿里云 WAF/风控 SDK 的浏览器指纹回传：

- URL 上的 `refer__991=<长混淆串>`
- Cookie `_c_WBKFRo`、`_nb_ioWEgULi`
- Cookie `ssxmod_itna` / `ssxmod_itna2` / `cdn_sec_tc` / `acw_tc`（CDN/WAF 标记）

**新增依赖这些会让项目变脆，且无法复现（值随浏览器环境动态生成）。**

---

## 2. 签名算法 `[实证]`

```
X-Sign = md5( X-Timestamp + "-" + X-Nonce + "-" + SIGN_SECRET )      小写 hex
```

```python
SIGN_SECRET = "8a1317a7468aa3ad86e997d08f3f31cb"
X_Timestamp = str(int(time.time() * 1000))     # 13 位毫秒
X_Nonce     = secrets.token_hex(16)            # 32 位小写 hex
X_Sign      = hashlib.md5(f"{X_Timestamp}-{X_Nonce}-{SIGN_SECRET}".encode()).hexdigest()
```

**复算验证**（`verify_sign.py`，纯本地无请求）：SAZ 中 5 个不同会话 + 用户提供的浏览器请求头，**6/6 命中**，含：
```
md5("1790732179905-75988225830f4d93b6702dcd0a2e9baf-8a1317a7468aa3ad86e997d08f3f31cb")
= a32ee7a2876ad44e6658475f0a7c69eb   ✅
```

> ⚠️ 本仓 `internal/glm/sign.go` 注释里的「X-Timestamp 倒数第二位替换成校验位」**对签名结果无影响**（原始值与替换值恰好相同），新项目**直接用原始毫秒值即可**，不必实现该校验位。

### 请求头清单（`[实证]`，按 SAZ 原文）

必带（鉴权相关）：
```
X-Sign, X-Nonce, X-Timestamp          # 签名三件套
Authorization: Bearer <access_token>  # 换 token 时为 refresh
Cookie: chatglm_token=<access>; chatglm_refresh_token=<refresh>; chatglm_user_id=<uid>
X-Device-Id                           # 建议与 JWT.device_id 一致
```
伪装头（业务头，非鉴权）：
```
App-Name: chatglm          X-App-Platform: pc        X-App-Version: 0.0.1
X-App-fr: default          X-Lang: zh                X-Request-Id: <32hex>
X-Device-Model: (空)       X-Device-Brand: (空)
Origin: https://chatglm.cn Referer: https://chatglm.cn/main/alltoolsdetail
Content-Type: application/json;charset=UTF-8         Accept: application/json, text/plain, */*
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Edg/151.0.0.0
X-Exp-Groups: <20 项实验分组串，见 SAZ；可省略，服务端不校验>
```

> `[实证]` `X-Exp-Groups` 可由服务端接口 `GET /chatglm/operation-api/experimental/groups` 动态获取，但**不发送它也能正常调用**（本仓未发送且工作正常）。

### 响应信封

```json
{"status":0, "message":"success", "result": {...}, "rid":"<= X-Request-Id>"}
```
- `status != 0` 为业务错误；`status=40103 / message="user needs login"` = 未登录 `[实证]` sid 092/148。
- 另有 HTTP 401 直接返回（sid 092 为 401 + 上述 body）。

---

## 3. 端点清单

统一前缀 `https://chatglm.cn`。**全部需要 §2 的签名头。**

### 3.1 用户信息 `[实证]` sid 064 / 212（登录态）、081（访客态）

```
GET /chatglm/user-api/user/info
```
```json
{"status":0,"result":{
  "_id":"6982e0ad0efeac7998789a48","username":"...","nickname":"Smurf7788",
  "avatar":"https://...","phone":"186****1404","ip":"...","app_channel":"default",
  "sl":0,"idcard_verified":false,"sub_account":{"mode":0,"is_teen":false},
  "created_at":1790572780,"is_guest":false,
  "profile":{"nickname":{...},"avatar":{...},"phone":{...},"gender":0,"age_group":"","job":""},
  "member_info":{"member_status":1,"non_member_number":4,"member_expiration_time":-1,
    "level":"","rights":[],"token_usage_percent":"18%","token_left_percent":"82%",
    "left_score":"2000.00","score_rule":"免费用户，登录赠送200积分/天"},
  "block_info":{"blocked":false,"sensitive_level":0,...},
  "setting":{"sync_storage":false,"stat_disallowed":true},
  "verification":{"idcard":{"verified":false}}}}
```

**积分余额 = `result.member_info.left_score`，单位「分」（100 分 = 1 积分，`"2000.00"` = 20 积分）。** `[实证]`

- 访客（`is_guest:true`）**没有 `member_info` 字段**，取余额前必须判空 `[实证]` sid 081。
- 本 SAZ 中**未出现** `GET /chatglm/member-api/member/member_info`（本仓 constants.go 注释称其为唯一余额来源）→ **新项目直接用 `user/info` 即可**，少一个依赖。`[实证]`

### 3.2 模型信息 `[实证]` sid 026 / 048（每次进入对话页都拉）

```
GET /chatglm/agent-api/operation/detail?tag=available_models
```
```json
{"status":0,"result":{
  "default":{"agent":"glm-5.3-flash","am":"glm-5.3-flash","guest":"glm-5.3-flash",
             "non_vip":"glm-5.3-flash","svip":"glm-5.3-flash","vip":"glm-5.3-flash"},
  "models":[
    {"display_name":"GLM-5.3","selected_model":"glm-5.3","display_tag":[],
     "description":"","am_description":"","am_switch":true,"chat_switch":true,
     "reasoning_efforts":[
       {"display_name":"快速","effort":"","am_effort":"low","description":""},
       {"display_name":"深度","effort":"thinking","am_effort":"high","description":"处理复杂任务"},
       {"display_name":"极致","effort":"deep_thinking","am_effort":"max","description":"全力推理，耗时更长"}]},
    {"display_name":"GLM-Flash","selected_model":"glm-5.3-flash","display_tag":["new"],
     "description":"5.3-Flash，回复速度快","am_description":"5.3-Flash，积分消耗大幅降低", ...}]}}
```

**能力矩阵**：仅 `am_switch` / `chat_switch` / `display_tag`。`[实证]` **没有** per-model 的上下文长度 / token 上限 / 多模态能力字段。

**费率：未找到。** `[实证]` 全量 grep 所有响应体，无 `price`/`fee`/`rate`/`cost`/`unit_price` 字段。`payment-api/customer/type` 只返回 `{type,title,content,subject_num,tip_status}`；`payment-api/v1/cs/me` 只返回客服工单地址。→ **新项目不要实现费率接口**。

`effort` 取值映射 `[实证：深度；推断：快速/极致]`：
| UI | `reasoning_effort` 请求值 |
|---|---|
| 快速 | `""` |
| 深度 | `"thinking"` ✅ 已实证（sid 356/480） |
| 极致 | `"deep_thinking"` |

### 3.3 对话列表 + 翻页 `[实证]` sid 125 / 231 / 271 / 274 / 276 / 290 / 339

```
POST /chatglm/mainchat-api/conversation/recent_list
body: {"page":1,"page_size":20}
```
```json
{"status":0,"result":{"has_more":true,"conversation_list":[
  {"conversation_id":"6abb0f35a0610b1da40542f2","assistant_id":"65940acff94777010aa6b796",
   "title":"数学问题求解","history_total":1,
   "meta_data":{"if_plus_model":false,"is_networking":true,"chat_mode":"chat_agent",
                "subscribe_id":"deep_research","subscribe_is_read":false,"title_modified":true},
   "status":0,"update_time":1790644023,"project_id":""}]}}
```

- **翻页是纯页码**：`page` 1→2→3→4 实测递增，`has_more` 决定是否继续。`[实证]`
- `history_total == 0` 且 title 以 `【任务】` 开头 = **未发消息的空壳会话**。`[实证]`
- `update_time` 是 **Unix 秒**。
- `title_modified:true` = 用户手动改过标题。`[实证]`

### 3.4 创建对话 —— **不存在创建端点** `[实证]`

点「新对话」（埋点 `1790684978733 CLICK DIV: 新对话`）后**只发出** `available_models` / `payment-api/customer/type` / `customize_subscribe/detail/info` 三个请求，**无任何 create 调用**。

真正的创建发生在首次发消息：`conversation_id:""` → 响应首帧回传真实 `conversation_id`（sid 356）。`[实证]`

> **新项目 API 设计**：`create_conversation()` 不要造一个假端点，应该实现为「发一条消息并返回首帧的 conversation_id」，或只提供 `chat(conversation_id=None)`。已验证脚本 `test_runner.py` 的做法可参考。

### 3.5 对话历史消息 `[实证接口 / 未知结构]`

```
GET /chatglm/agent-api/conversation/page_messages_v2
      ?assistant_id=<24 位 hex>&conversation_id=<24 位 hex>&page_size=30
```
外层信封 `[实证]`（5 次调用全部相同）：
```json
{"status":0,"result":{"list":[],"little_more":false,"greate_more":false}}
```

| 项 | 状态 |
|---|---|
| 端点、方法、三个参数 | `[实证]` |
| `page_size=30` 为网页版固定值 | `[实证]` |
| `little_more` = 更早还有（向上翻）、`greate_more` = 更新还有（上游拼写错误） | `[推断]` |
| 触发时机：点开历史会话即调一次 | `[实证]`（timeline.txt 5 处） |
| **`list` 元素结构** | `[未知]` —— 5 次全返回空数组 |
| **翻页参数名**（`page` / `cursor` / `last_id`） | `[未知]` —— 无内容可翻，抓包无证据 |

为什么全空：被点开的会话在 `recent_list` 里虽 `history_total:1`，但 `meta_data` 带 `chat_mode:"chat_agent"` + `subscribe_id:"deep_research"` = **沉思模式失败任务**，消息不在普通消息表里。`[实证]`

**旁证（同结构，来自关联接口）**：
- 消息主键叫 **`history_id`** —— `stream/update_status` 与 `stream/stop_stream` 用它，取值与 SSE 帧 `parts[].id` 完全相同。`[实证]`
- SSE 里 assistant 消息结构已知（§3.6），`list` 元素 `[推断]` 同构。

> **新项目要求**：实现该接口，但把 `list` 元素**动态透传（不写死字段）**，并在 README 标注「结构未实证」。若需补实证，应在真实账号上「打开一条有真实消息的会话并向下翻页」再抓一次。

### 3.6 对话（首次 / 多轮）`[实证]` sid 356（首轮）、480（多轮）

```
POST /chatglm/backend-api/assistant/stream
Accept: text/event-stream
Content-Type: application/json
```

请求体（**首轮与多轮结构完全一致，唯一差别是 `conversation_id`**）：
```json
{"assistant_id":"65940acff94777010aa6b796",
 "conversation_id":"",              // 多轮填真实 ID；首轮空串
 "project_id":"","chat_type":"user_chat",
 "meta_data":{
   "cogview":{"rm_label_watermark":false},
   "is_test":false,"input_question_type":"xxxx","channel":"","draft_id":"",
   "chat_mode":"thinking","reasoning_effort":"high",
   "selected_model":"glm-5.3-flash","is_networking":true,
   "quote_log_id":"","platform":"pc"},
 "messages":[{"role":"user","content":[{"type":"text","text":"你好"}]}]}
```

> ⚠️ **`messages` 只带当前这一条，不带历史**。多轮上下文由服务端按 `conversation_id` 维护。`[实证]`（对比 356 与 480，两者 messages 长度都是 1）

#### SSE 帧语义 `[实证]`

- 每帧 `data: {json}`，**结尾没有 `data: [DONE]`**（`grep -c DONE` = 0）。`[实证]`
- `parts[].status == "init"` → **增量 delta**；`== "finish"` → 该段**完整全文**。`[实证]`（356 三帧：think/text 各一次 finish 全文）
- `parts[].content[].type` ∈ `text` / `think` / `tool_calls` / `tool_result`。`[实证]`
- 顶层 `status` ∈ `init` / `finish` / `error`。**正常收尾 = 顶层 `status:"finish"`，不是 [DONE]**。`[实证]`
- 错误收尾：`"status":"error"` + `"error":{"error_code":10024,"err_msg":"generator exit"}` + `last_error`。`[实证]`（480 被用户 abort）
- **整条流无任何 usage / token / 计费字段**。`[实证]`
- 其它字段：`parts[].model`（`moe_53f` / `moe_zero`）、`parts[].logic_id`（UUID）、`meta_data.increase_content`（与上帧重叠的后缀）、`meta_data.total_time`、`parts[].id` == `conversation` 的消息 `history_id`。`[实证]`

首帧回传新建对话的 ID：
```json
{"id":"6abbaf3a430b0bf06c7b6b1b","conversation_id":"6abbaf3a430b0bf06c7b6b1a", ...}
```

#### 配套控制接口 `[实证]` sid 402 / 497

```
POST /chatglm/mainchat-api/stream/update_status   body: {"history_id":"<assistant 消息 id>"}
POST /chatglm/mainchat-api/stream/stop_stream     body: {"history_id":"<assistant 消息 id>"}
```

### 3.7 删除对话 `[实证]` sid 289 / 323

```
POST /chatglm/mainchat-api/conversation/bulk_delete
body: {"conversation_ids":["6abb0f33aed53d5a7c5efdbc"],"assistant_id":"65940acff94777010aa6b796"}
→ {"status":0,"result":null}
```

- **删单个也走 bulk_delete**（数组），无单删端点。
- ⚠️ **本仓 `internal/glm/constants.go` 的 `EpDeleteConv = "/chatglm/backend-api/assistant/conversation/delete"` 与实测不符，新项目请用 `bulk_delete`。**

### 3.8 对话改名 `[实证]` sid 314 / 361

```
POST /chatglm/mainchat-api/conversation/modify_title
body: {"conversation_id":"6abb0722892f90cd87451de2","title":"很高兴见到你"}
→ {"status":0,"result":null}

GET /chatglm/mainchat-api/conversation/title?conversation_id=<id>
→ {"status":0,"result":{"title":"你好"}}     # 新建后前端立刻取一次自动生成的标题
```

### 3.9 其他已知端点（本项目**不需要**，仅备查）

| 端点 | 用途 |
|---|---|
| `GET /chatglm/operation-api/config/cur_ts` | 服务端时间（可作时钟校准） |
| `POST /chatglm/user-api/user/logout` | 登出 |
| `POST /chatglm/user-api/guest/access` | 取访客 token |
| `POST /chatglm/user-api/user/wechat_qr_login` | 微信扫码登录（body `{"login_code":"..."}`）**本次不实现** |
| `GET /chatglm/operation-api/config/operation_data?tag=...` | 运营配置（starter 提示词 / clientConfig） |
| `GET /chatglm/operation-api/experimental/groups` | 实验分组串（喂给 X-Exp-Groups） |
| `POST /chatglm/member-api/member/daily_login_score` | 每日登录积分（本仓已实现，本次不在范围） |
| `GET /chatglm/mainchat-api/claw_agent/list` | 「伙伴」列表（恒空） |
| `GET /chatglm/feed-api/assistant_top/v4/recent_list` | 公开智能体列表 |
| `POST /chatglm/backend-api/v1/conversation/recommendation/list` | 回答后推荐追问 |

---

## 4. assistant_id 语义 `[实证]`

**它是「智能体（bot）的唯一 ID」，不是模型 ID。** 模型选择是另一层（`meta_data.selected_model`）。

取值来源（实证）：`GET /chatglm/operation-api/config/operation_data?tag=fixed_assistant`（sid 124）

| assistant_id | 名称 | 入口别名 |
|---|---|---|
| `65940acff94777010aa6b796` | ChatGLM（主对话） | `main` |
| `68f0b8c110eea3e78b0e0e5e` | 学习搭子 | |
| `65a232c082ff90a2ad2f15e2` | AI画图 | |
| `658a7988b8a9a98d38725745` | AI阅读 | |
| `668d03b2e99d661ed3c32516` | 视频助手 | |

同一 ID 也是 `operation-api/assistant/welcome_info/<id>` 的路径参数，响应含 `name`/`description`/`avatar`/`pv`/`hit`/`bot_for_key`。`[实证]` sid 052

**「一个会话」= (`assistant_id`, `conversation_id`) 二元组** —— `recent_list` 每条都同时带这两个字段。`[实证]`

---

## 5. 本项目已有的可复用资产（Go 侧，勿逐行照抄，看语义即可）

路径 `internal/glm/`（**注意：这是 Go 实现，且含本次明确排除的工具调用逻辑，只借语义**）：
- `sign.go` —— 签名与请求头构造（含该校验位实现，新项目可简化）
- `sse.go` —— SSE 解析（`init` delta / `finish` 全文的补差额逻辑）
- `client.go` —— token 刷新 + 落盘（不变式：调 `RefreshToken` 后必须 `SaveAtomic`）
- `constants.go` —— 端点常量（**注意 §3.7 的端点错误**）

另有已验证的独立 Python 脚本（**同一目录，输出格式可直接参考**）：
- `test_runner.py` —— 签名 + 请求头 + SSE 消费的可用实现
- `prototype.py` / `agent_loop.py` —— **工具调用探索，本次明确排除，不要读进设计**

---

## 6. 待补齐（新项目必须自己验证的两点）

1. **`page_messages_v2` 的 `list` 元素结构** —— 需要一次「打开有真实消息的历史会话」的抓包。
2. **`page_messages_v2` 的翻页参数名** —— 需要一次「向下翻页」的抓包。

> 这两点无法从现有 SAZ 得出。新项目应把它们**隔离在单个函数内 + 标注未实证**，而不是猜测后写进 README 当成事实。
