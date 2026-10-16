# HANDOFF — chatglm.cn 网页版独立 Python 逆向工具项目

> **交付形态**：本仓库即交付物（`glm2api/` 库 + `docs/` 协议文档 + `examples/` + `tests/`）。
> 早期交接阶段的目标目录 `ref/glm-tool-proto/` 仅为历史来源，已不再维护。
> **日期**：2026-09-30（2026-10-15 复核更新，见文末「复核变更」）
> **上游来源**：本项目（wild-work）对 chatglm.cn 网页版的一次完整抓包逆向 + 多轮 agent 分析

---

## 1. 一句话任务

把已完成的 **chatglm.cn 网页版逆向结果**（纯 API 层，不含登录流程逆向）落地为一个**独立的 Python 项目**：用户提供浏览器凭据后，可直接调用清言网页版私有接口完成「用户信息 / 模型信息 / 对话列表管理 / 会话创建·历史·删除 / 改名 / 多轮对话（流式透传 + 非流式缓存池）」。

**已定下的边界（不要扩大）**：

| 做 | 不做 |
|---|---|
| 凭据解析 + 签名（`X-Sign` 等） | ❌ 登录流程逆向（微信扫码 / CDP 抓 Cookie）——**用户手工提供浏览器凭据** |
| 上述 6 组端点 | ❌ 工具调用（tool_calls / XML 协议 / agent loop） |
| 流式 SSE **原样透传** | ❌ `think` 与 `text` 的语义化区分（透传时不需要） |
| 非流式：**缓存池**聚合 | ❌ 签到 / 每日积分 / 费率（费率上游不存在） |
| 单账号 | ❌ 多账号池 / 粘性路由 / 冷却惩罚 |

---

## 2. 第一件事：读协议文档（不要重新抓包）

**`docs/PROTOCOL.md`** 已把本次逆向的全部结论固化，包含：凭据模型、签名算法与复算证据、
10 个端点的请求/响应样例、SSE 帧语义、`assistant_id` 语义、以及**明确标注的「未实证」项**。
生图/参考图改图另见 [`生图协议与实操.md`](生图协议与实操.md)。

抓包原始数据在 `ref/chatglm_web_20260929_extracted/`（`raw/NN_*` 报文、`bodies/NN.body` 解压响应、`timeline.txt` 埋点时间线、`chatglm_web_outline.tsv` 索引）。需要回查时按 sid 号查。

**⚠️ 验证纪律（2026-10-15 更新）**：逆向阶段确实要求「不随意发请求」，但后续已按用户要求做过**多轮线上实测**
（生图复核、参考图改图、接口定位），结论已写入 `docs/生图协议与实操.md`。
当前纪律：**能离线回放就先离线**；确需真实调用时**一次只改一个变量**、请求间隔 ≥5s、避免密集重试（会加重 WAF 限流）。

### 本目录已有文件（务必先看，防重复劳动）

| 文件 | 内容 | 本项目如何处理 |
|---|---|---|
| `PROTOCOL.md` | 本次逆向协议文档（新写） | **权威依据，逐条实现** |
| `生图协议与实操.md` | 生图（文生图 / 参考图改图）协议 + 线上实测实操 | 生图相关需求以此为准 |
| `test_runner.py` | 早期已验证可用的 Python 实现（签名、请求头、SSE 消费） | 已抽取为正式实现，硬编码凭据已清理 |
| `prototype.py` | 工具调用探索原型 | ❌ 与本次范围无关 |
| `agent_loop.py` | 工具调用/agent loop 探索 | ❌ 与本次范围无关 |

> `test_runner.py` 里硬编码了 SAZ 中提取的真实 token/cookie。**新项目必须改为从配置/env 读取**，并且**不要把任何 token 写进新仓库的任何文件**。

---

## 3. 交付物要求

### 3.1 技术约束

- Python 3.12；**依赖只允许 `requests`**（Python 3.12 已移出 stdlib）。运行环境用 `D:\dev\uv\ve\sys312`（`uv pip install requests`）。
- **不要引入 httpx / aiohttp / pydantic / tenacity 等**。SSE 用手写行解析、用 `requests` 的 `stream=True` + `iter_lines()` 即可（`test_runner.py` 已有可参考实现）。
- 源码/注释用中文；变量名可缩写。风格参考仓库根 `AGENTS.md`。
- 临时文件放 `temp/`，**不要用系统临时目录**。

### 3.2 期望的 API 面（方法名可调整，能力不要少）

```python
client = GlmClient(credential)          # credential 从 env / 文件加载

# 用户与会话
client.user_info() -> dict              # 含 member_info.left_score（单位「分」）
client.models() -> dict                 # available_models 原样
client.list_conversations(page=1, page_size=20) -> dict     # has_more + conversation_list
client.iter_conversations() -> Iterator[dict]               # 自动翻页直到 has_more=False

# 会话生命周期
client.chat(assistant_id, messages, conversation_id=None, stream=False, ...)
    # 首轮 conversation_id=None → 上游空串 → 从首帧取回真实 id
    # stream=True  → 原样透传 SSE（不解析、不改写、不补 [DONE]）
    # stream=False → 内部缓存池聚合后一次性返回（含 conversation_id）
client.history(conversation_id, assistant_id) -> dict   # ✅ mainchat-api/conversation/messages（升序全量、无分页）
client.history_v2(conversation_id, ...) -> dict         # ❌ 老端点 page_messages_v2（实测始终空 list）
client.rename_conversation(conversation_id, title) -> None
client.delete_conversations(ids: list[str], assistant_id) -> None
client.stop_stream(history_id) / client.update_status(history_id)   # 建议提供

# 凭据
client.ensure_token()                   # 过期则用 refresh 换，并**持久化轮换后的 refresh_token**
```

### 3.3 必须写进代码的 3 条硬规则（都有实证依据，见 PROTOCOL.md）

1. **refresh token 会轮换 → 换完必须落盘**。不落盘 = 下次启动用旧 refresh token，账号直接失效（本项目曾因此踩坑，见 AGENTS.md 不变量 19/20）。
2. **流式接口必须用不设 `timeout` 的连接**。`requests` 里**不要**给流式请求传 `timeout`（或传 `(connect_timeout, None)`）——否则长回答会被掐断。本项目 Go 侧实测被掐断 5 次（AGENTS.md R35）。
3. **流被截断不得伪装成正常收尾**。SSE 读到传输错误/半帧时，**不要**补 `[DONE]` 或结束标记；应抛出可区分的错误（AGENTS.md R36 的教训）。上游正常结束的标志是**顶层 `status:"finish"`**，上游本来就不发 `[DONE]`。

### 3.4 凭据格式（用户手工提供）

建议支持环境变量 + 文件两种方式，**至少**需要 refresh token（access 可自动换）：

```
GLM_REFRESH_TOKEN   # 必需，来自浏览器 cookie chatglm_refresh_token
GLM_DEVICE_ID       # 可选；建议从 JWT.device_id 解析，与凭据同源
GLM_COOKIE          # 可选，整条 cookie（若用户直接粘贴）
```

从浏览器取凭据：F12 → Application/存储 → Cookie → `chatglm.cn` → 复制 `chatglm_refresh_token`。**README 必须写清这一步**，因为这是唯一的入参入口。

---

## 4. 三个最容易做错的地方（重点核对）

1. **删除端点是 `POST /chatglm/mainchat-api/conversation/bulk_delete`**（body `{"conversation_ids":[...],"assistant_id":"..."}`）。
   本项目 Go 侧 `internal/glm/constants.go` 写的是 `/chatglm/backend-api/assistant/conversation/delete`，**与抓包实测不符**。新项目用实测值。**不要照抄 Go 的端点常量。**
2. **不存在「创建对话」端点**。点「新对话」不发任何 create 请求；会话由首次发消息时服务端懒创建。`create_conversation()` 要么不提供，要么实现为「发一条消息并返回首帧 id」。**不要凭感觉编一个端点。**
3. **`messages` 只带当前这一条**，多轮上下文全靠 `conversation_id`。不要自己拼历史（会与上游语义冲突，也会浪费 token）。

### 已知缺口（必须如实标注，不许猜完当事实）

`GET /chatglm/agent-api/conversation/page_messages_v2` 的 **`list` 元素结构**与**翻页参数名**在本次抓包中**没有证据**（5 次调用全返回空数组，因为被点开的都是沉思模式失败任务）。
**⚠️ 2026-10-16 结论：这条端点确实是死路**；真正可用的历史端点是
`GET /chatglm/mainchat-api/conversation/messages?conversation_id&assistant_id`（见 PROTOCOL §3.5.1），
`history()` 已改指它。
**2026-10-15 复核：在一条有真实消息的会话上再试，仍返回空 `list`**；同期试了
`backend-api/conversation/messages`（404）与 `backend-api/v1/conversation/messages`（`status=1 invalid_request_error`）也都不通。

→ ~~实现时：该函数**动态透传** `list` 不写死字段；README/代码注释里标注「结构未实证，待补一次真实会话抓包」。~~
   （已作废：改用上条的 `conversation/messages`，字段已实证并有回放测试）

另一个已确认的阻塞（2026-10-15）：`POST productivity-api/file/chat_upload`（上传参考图）在不带
`refer__991` 时 **100% 返回 40012**，win/pc 两种指纹、任意体积都被拒 ⇒ 参考图改图请改用
「公网图片 URL 直传 `image/reference`」路线，详见 [`生图协议与实操.md`](生图协议与实操.md) §3.2。

---

## 5. 建议的实现顺序

1. `credential.py` —— 凭据加载 + JWT 解析（取 `uid` / `device_id` / `exp`，**不解码不校验签名，只 base64 解 payload**）
2. `sign.py` —— `X-Sign/X-Nonce/X-Timestamp` + 请求头组装（照 PROTOCOL.md §2；**用原始毫秒时间戳，不必实现 Go 侧那个校验位**）
3. `client.py` —— 会话建立、统一 `_request()`、`ensure_token()` + refresh 落盘
4. 6 组端点方法（§3.2）
5. SSE：`stream=True` 透传 + `stream=False` 缓存池聚合（两个函数，共用同一份行解析）
6. `README.md`（怎么取凭据、怎么跑、能力边界、已知缺口）+ `examples/` 最小示例

---

## 6. 验收标准

- [ ] 只依赖 `requests`；`python -c "import glm_client"` 无副作用（不自动发请求）
- [ ] 用一份**真实凭据**跑通：user_info / models / list_conversations / chat(stream=True) / chat(stream=False) / history / rename / delete
- [ ] 流式输出与浏览器抓包帧**逐帧一致**（不增删改帧）
- [ ] refresh 轮换后文件里是新 refresh_token（可人为把 access 置过期后重启验证）
- [ ] 长回答不被 `timeout` 掐断（跑一次超过 60s 的回答）
- [ ] 全仓 grep 不到任何真实 token / cookie 明文
- [ ] README 明确写出 §4 的三个易错点与 §4 的已知缺口

---

## 7. 收尾项（2026-10-15 复核后）

1. ~~`page_messages_v2` 的 `list` 元素结构仍未实证~~ → **已解决（2026-10-16）**：该端点确认为死路（刚发消息的会话仍返空），
   真正可用的历史端点是 `GET /chatglm/mainchat-api/conversation/messages?conversation_id&assistant_id`
   （升序全量、无分页）。结构与证据见 [`PROTOCOL.md` §3.5.1](PROTOCOL.md)；
   `glm2api.history()`（Python）与 `glm2cli conv history`（Go）均已改指它，
   旧的 `page_messages_v2` 分别保留为 `history_v2()` / `HistoryV2()` 仅供比对。
2. GO 侧 `internal/glm/constants.go` 的 `EpDeleteConv` 端点与实际不符 —— **属于 wild-work 主仓的 bug**，不在本项目范围内。
3. ~~本项目 `X-Device-Id` 每请求随机~~ → **已修**：`client._headers()` 现用 `credential.device_id`（与 JWT 内一致）。
4. 新增已知阻塞：`chat_upload` 不带 `refer__991` 时 100% 40012（详见 `生图协议与实操.md` §3.5）；
   参考图改图改走「公网图片 URL」路线（§3.2）。

---

## 8. 复核变更（2026-10-15）

| 变更 | 说明 |
|---|---|
| 删除两份过时文档 | `IDE生图协议分析.md`、`生图OpenAI兼容接口可行性评估.md` → 合并为 [`生图协议与实操.md`](生图协议与实操.md) |
| 生图结论更新 | `aspect_ratio` 实测全集；`style`/`scene` **已失效**；非法比例静默降级 1:1 |
| 参考图改图重大更新 | **不需要 `chat_upload`**：`image/reference` 接受任意上游可达的公网 `image_url`，`file_id` 可空 |
| 编辑模式比例 | 已确认**强制保持输入图比例**（`aspect_ratio` 无效） |
| 验证纪律 | 由「禁止发请求」改为「离线优先 + 单变量 + 控频」 |

---

## 9. 建议技能（suggested skills）

| 技能 | 用途 |
|---|---|
| `fiddler-saz-analysis` | 若用户愿意补抓包，用它快速建立 outline + 定位 sid |
| `diagnose` | 遇到「流被掐断 / 401 死锁 / 帧丢失」类问题时按纪律排查，不要瞎试 |
| `write-a-skill` | 若最终想把这套协议固化成可复用的 skill（比如 `/glm-api`） |

**其他建议调用的能力**：
- 读 `D:\work\wild-work\dev\AGENTS.md` 的 **R25 / R35 / R36 / 不变量 19-20** —— 这四条正是本项目 §3.3 三条硬规则的来源，读一遍能避免重复踩坑。

---

## 10. 脱敏声明

本文件与 `PROTOCOL.md` 中的 token/cookie 值**均已截断或替换为占位**（如 `eyJhbGciOiJIUzI1NiIsInR5c...<421 chars>`），未包含任何可直接使用的凭据。

⚠️ 但 `ref/glm-tool-proto/test_runner.py` 与解压目录 `ref/chatglm_web_20260929_extracted/` **仍含真实凭据明文**（SAZ 原始数据）。这些文件在 `ref/` 忽略规则下不入库，但：
- 新项目**不得**把这些值复制进去；
- 建议确认那个 refresh token 已在网页端登出作废（它在抓包时有效期至 2027-03-27）。
