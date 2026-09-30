# chatglm2api

智谱清言（[chatglm.cn](https://chatglm.cn)）**网页版私有接口**的独立 Python 客户端（纯 API 层）。

用户从浏览器取出一次凭据后，即可编程调用清言的用户信息 / 模型列表 / 会话管理与多轮对话
（流式原样透传 + 非流式聚合）。

> 本项目的协议依据全部来自一次完整抓包逆向，结论固化在 [`docs/PROTOCOL.md`](docs/PROTOCOL.md)
> （每条都标了 `[实证]` / `[推断]` / `[未知]`）。改动代码前请先读它。

---

## 1. 快速开始

### 1.1 取凭据（唯一的入参入口）

清言**没有**可编程登录接口，凭据只能由用户从浏览器手工取出（本项目的明确边界：不做登录逆向）。

1. 浏览器登录 <https://chatglm.cn>，按 `F12` 打开开发者工具；
2. `Application`（应用）→ `Storage`（存储）→ `Cookies` → `https://chatglm.cn`；
3. 复制 `chatglm_refresh_token` 的值 —— **它才是根凭据**，access token 会自动换取；
4. （可选）同时看一眼 `chatglm_user_id` 与 JWT 里的 `device_id`，写进凭据文件便于日志对齐。

### 1.2 提供凭据（三种方式任选）

**方式 A：环境变量（最快）**

```bash
export GLM_REFRESH_TOKEN='eyJhbGciOi...'      # 必需
export GLM_DEVICE_ID='d4f911af...'            # 可选，建议与 JWT.device_id 一致
```

**方式 B：整条 Cookie 粘贴**

```bash
export GLM_COOKIE='chatglm_refresh_token=eyJ...; chatglm_user_id=6982...'
```

**方式 C：凭据文件（推荐，能让 token 轮换自动落盘）**

```bash
cp glm_credential.example.json glm_credential.json   # 然后填入 refresh_token
export GLM_CREDENTIAL_FILE=D:/path/to/glm_credential.json
```

> ⚠️ **refresh token 每次刷新都会轮换**。只有用「方式 C / 明确的文件」时，
> 本项目才能把轮换后的新 token 写回文件；用环境变量时无法落盘，下次启动必须重新取 Cookie。

### 1.3 安装与自检

```bash
# 依赖只有 requests（Python 3.12 已把它移出标准库）
D:/dev/uv/ve/sys312/Scripts/python.exe -m pip install -r requirements.txt

# 无副作用自检：import 不会发任何请求
D:/dev/uv/ve/sys312/Scripts/python.exe -c "import glm2api; print(glm2api.__version__)"

# 离线回归测试（用抓包帧跑，不发网络请求）
D:/dev/uv/ve/sys312/Scripts/python.exe -m pytest tests -q
```

### 1.4 最小示例

```python
from glm2api import GlmClient, load_credential

with GlmClient(load_credential("glm_credential.json")) as client:
    print(client.user_info()["nickname"], client.left_score())   # 积分单位「分」

    # 非流式：内部用缓存池聚合，返回 dict
    res = client.chat(glm2api.DEFAULT_ASSISTANT, "用一句话介绍你自己")
    print(res["text"], res["conversation_id"])

    # 多轮：只传新消息，上下文由服务端按 conversation_id 维护
    print(client.chat(glm2api.DEFAULT_ASSISTANT, "我刚才问了什么？",
                      conversation_id=res["conversation_id"])["text"])

    # 流式：原样透传上游 SSE（不解析、不改写、不补 [DONE]）
    for chunk in client.chat(glm2api.DEFAULT_ASSISTANT, "从 1 数到 5", stream=True):
        print(chunk.decode("utf-8", "replace"), end="")
```

更多见 [`examples/`](examples/)。命令行用法：

```bash
python -m glm2api user-info
python -m glm2api models
python -m glm2api conversations --all
python -m glm2api chat "你好"                    # 非流式聚合
python -m glm2api chat "你好" --stream           # 原样透传 SSE
python -m glm2api history <conversation_id>
python -m glm2api rename <conversation_id> "新标题"
python -m glm2api delete <conversation_id>       # 实际删除，谨慎
python -m glm2api --proxy http://127.0.0.1:10809 user-info
```

---

## 2. 能力边界

| 做 | 不做（明确排除） |
|---|---|
| 凭据解析 + 签名（`X-Sign` / `X-Nonce` / `X-Timestamp`） | ❌ 登录流程逆向（微信扫码 / CDP 抓 Cookie）——**用户手工提供凭据** |
| 用户信息 / 模型列表 / 会话列表 / 会话生命周期 / 多轮对话 | ❌ 工具调用（tool_calls / XML 协议 / agent loop） |
| 流式 SSE **原样透传** | ❌ `think` 与 `text` 的语义化分流（透传时不需要；聚合结果里两者分开返回） |
| 非流式：**缓存池**聚合 | ❌ 签到 / 每日积分 / 费率（**费率上游根本不存在**，见 PROTOCOL §3.2） |
| 单账号 | ❌ 多账号池 / 粘性路由 / 冷却惩罚 |

### API 面

```python
client = GlmClient(credential, base=..., proxies=...)

client.user_info()                         # dict，含 member_info.left_score（单位「分」）
client.left_score() -> float               # 余额便捷读法（访客无 member_info → 0.0）
client.models()                            # available_models 原样

client.list_conversations(page=1, page_size=20)
client.iter_conversations()                # 自动翻页直到 has_more=False

client.chat(assistant_id, message, conversation_id=None, stream=False, **opts)
client.create_conversation(message)        # ⚠️ 见下方「易错点 2」
client.history(conversation_id, assistant_id, page_size=30, **extra)
client.rename_conversation(conversation_id, title)
client.conversation_title(conversation_id)
client.delete_conversations(ids, assistant_id)      # 走 bulk_delete
client.stop_stream(history_id) / client.update_status(history_id)

client.ensure_token()                      # 过期则 refresh，并持久化轮换后的 refresh_token
client.refresh()                           # 强制刷新
```

`chat()` 的 `**opts`：`model` / `chat_mode` / `reasoning_effort` / `is_networking` / `extra_meta`。

---

## 3. 三条硬规则（都有实证依据，勿改）

1. **refresh token 会轮换 → 换完必须落盘。**
   上游 `user/refresh` 的响应里带新的 `refresh_token`，必须覆盖写回文件；
   否则下次启动用旧值，而旧的已被上游作废，**账号直接失效**。
   本实现：`GlmClient.refresh()` 成功后立即 `save_credential()`；落盘失败显式报错。
   （对应 wild-work `AGENTS.md` 不变量 19/20）

2. **流式请求必须用不设 `timeout` 的连接。**
   `requests` 的 `timeout` 覆盖整个请求生命周期（含读 body），对 SSE 意味着「长回答必被掐断」。
   本实现：`GlmClient._stream_response()` 显式 `timeout=None`；非流式请求仍保留超时。
   （对应 `AGENTS.md` R35：主仓 Go 侧曾实测被掐断 5 次且**无终止帧**）

3. **流被截断不得伪装成正常收尾。**
   上游正常结束的标志是**顶层 `status:"finish"`**，而且清言**本来就不发** `data: [DONE]`。
   读到传输错误 / 半帧时不得补结束帧，必须抛可区分的 `GlmStreamTruncated`。
   本实现：`sse.ensure_finished()` 只认 `finish` 或顶层 `error`，其余一律抛异常。
   （对应 `AGENTS.md` R36）

---

## 4. 三个最容易做错的地方（照抄主仓 Go 实现会踩坑）

1. **删除端点是 `POST /chatglm/mainchat-api/conversation/bulk_delete`**
   （body `{"conversation_ids":[...],"assistant_id":"..."}`）。
   主仓 `internal/glm/constants.go` 写的 `/chatglm/backend-api/assistant/conversation/delete`
   **与抓包实测不符**。本项目采用实测值。删单个也走 bulk（无单删端点）。

2. **不存在「创建对话」端点。**
   点「新对话」不发任何 create 请求；会话由**首次发消息时服务端懒创建**，真实 ID 从流首帧回传。
   因此 `create_conversation()` 的实现是「发一条消息并返回首帧的 `conversation_id`」，
   而不是编一个假端点。

3. **`messages` 只带当前这一条。**
   多轮上下文全靠 `conversation_id` 由服务端维护（对比抓包 sid 356/480，两者 `messages` 长度都是 1）。
   自己拼历史会与上游语义冲突，也浪费 token。本实现的 `build_chat_body()` 会**拒绝**多条消息。

---

## 5. 已知缺口（如实标注，未做美化）

**`GET /chatglm/agent-api/conversation/page_messages_v2` 的 `list` 元素结构与翻页参数名未实证。**

- 抓包中该接口被调用 5 次，**全部返回空数组** `{"list":[],"little_more":false,"greate_more":false}`。
  原因：被点开的会话虽 `history_total:1`，但其 `meta_data` 带 `chat_mode:"chat_agent"` +
  `subscribe_id:"deep_research"` = 沉思模式失败任务，消息不在普通消息表里 `[实证]`。
- 因此本项目的 `history()` **只动态透传 `result`，不写死 `list` 元素字段**；
  未知的翻页参数名可经 `**extra` 原样拼进 query，无需改函数签名。
- 补齐方式：在真实账号上「打开一条**有真实消息**的会话并向下翻页」再抓一次包。
  旁证：消息主键叫 `history_id`，取值与 SSE 帧 `parts[].id` 相同 `[实证]`，故 `list` 元素大概率与
  SSE 的 assistant 消息同构 `[推断]` —— 但**这是推断，不是事实**。

---

## 6. 与主仓 wild-work 的已知差异

| 项 | 主仓 Go 实现 | 本项目（以抓包为准） |
|---|---|---|
| 删除会话端点 | `backend-api/.../conversation/delete` | `mainchat-api/conversation/bulk_delete` ✅ |
| `X-Device-Id` | 每请求随机 | 与 `JWT.device_id` 一致（`[实证]` SAZ 110 处一致） |
| 时间戳校验位 | 实现了一个「倒数第二位替换」算法 | 直接用原始毫秒值（该校验位对签名结果无影响） |
| 积分来源 | `member-api/member/member_info` | 直接读 `user/info` 的 `member_info`（少一个依赖） |

主仓的端点是主仓的 bug，属 wild-work 主仓范围，本项目不代为修改。

---

## 7. 安全与脱敏

- **仓库内不得出现任何真实 token / cookie 明文。** 凭据文件 `glm_credential.json`
  已在 `.gitignore` 中；请只通过环境变量或该文件在本地提供。
- `docs/PROTOCOL.md` 中的凭据均已截断为占位。
- 提交前可自查：`rg -i "eyJhbGciOi" --glob '!docs/**' .`（应无命中）。

## 8. 目录结构

```
glm2api/           核心包
  constants.py     端点与常量（全部有抓包依据）
  sign.py          签名三件套 + 请求头
  credential.py    凭据模型 / 加载 / 落盘（含 WAF cookie 剔除）
  client.py        GlmClient：请求、refresh、6 组端点、流式/聚合
  sse.py           SSE 解析与缓存池聚合（可离线用抓包文件回放）
  errors.py        异常分类
  __main__.py      命令行入口
docs/
  PROTOCOL.md      逆向协议文档（权威依据）
  HANDOFF.md       交接说明（目标、边界、验收标准）
examples/          最小示例
tests/             离线回归测试（用真实抓包帧）
```

## 9. License

MIT，见 [LICENSE](LICENSE)。
