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

更多见 [`examples/`](examples/)。命令行入口 `python -m glm2api`（库自带的调试用）：

> 需要**人 & agent 都能直接调的 CLI**（输入 → JSONL 事件流）请用兄弟仓库
> **[pyichiban/chatglm-cli](https://github.com/pyichiban/chatglm-cli)**（Go，单文件零依赖）；
> 两个仓库共用同一份 `glm_credential.json` 格式。

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
client.generate_image(message, aspect_ratio="1:1", style="none", scene="none", **opts)
                                           # AI 画图（走对话通道换 agent）；返回 text 里含 Markdown 图片链接
                                           # ⚠️ style/scene 已失效；参考图改图见 docs/生图协议与实操.md
client.edit_image(prompt, image_url, **opts)
                                           # 参考图改图（智能编辑）→ {image_url, conversation_id}
                                           # image_url 必须是**上游可达的公网 URL**（不需 chat_upload）；
                                           # 输出比例强制跟随输入图（要正方形先补白）；**单图接口**，
                                           # 多角色请合并到同一张参考图（见 docs/生图协议与实操.md §3.7/§3.8）
client.create_conversation(message)        # ⚠️ 见下方「易错点 2」
client.history(conversation_id, assistant_id, **extra)
                                           # ✅ 单会话历史 [实证 2026-10-16]：GET mainchat-api/conversation/messages
                                           # 升序全量、**无分页**（page_size/page 实测被忽略）；
                                           # ⚠️ assistant_id 必带且要与会话匹配，传错直接空 messages
client.history_v2(conversation_id, ...)    # ❌ 老路子 page_messages_v2：实测始终返空 list，仅留作比对
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

### 5.1 单会话历史：`page_messages_v2` 是死路，可用端点已找到 ✅

**原缺口（抓包 5 次全返空 `list`）已于 2026-10-16 实测解决**：这个端点确实是死路，
但同一个会话的数据在另一个端点上，一次请求就能拿到全量消息：

```
GET /chatglm/mainchat-api/conversation/messages?conversation_id=<24hex>&assistant_id=<24hex>
→ {conversation_id, project_id, if_plus_model, new_ai_search_conversation,
   messages:[{id, create_time, update_time, input:{content[…], meta_data{…}},
              output:{role, status, parts[…], last_error}, score, memory_score}]}
```

- `output` 与对话 SSE 帧**同构**（`parts[].content[]` 的 type 一致）⇒ 可用同一套聚合规则解析；
- **升序全量、无分页**：`page_size=1` 实测被忽略，响应里也没有 `has_more` 类字段；
- `assistant_id` 必带：不传 ⇒ `status=400 invalid param`；传错 ⇒ `messages:[]`（**静默空**，不报错）。
  因为一个会话 = (`assistant_id`, `conversation_id`) 二元组。
- 完整字段与证据见 [`docs/PROTOCOL.md` §3.5.1](docs/PROTOCOL.md)。
- `history()` 已改指它；旧的 `page_messages_v2` 降为 `history_v2()` 仅供日后比对。

### 5.2 参考图上传接口被 WAF 拦死（可绕开），且**不支持多图**

`POST /chatglm/productivity-api/file/chat_upload` 在不带 `refer__991` 时 **100% 返回 40012**
（2026-10-15 实测：win/pc 两种指纹、233KB PNG 与 31KB JPEG、冷却 2h25m 后单发——均被秒拒）。
同刻 `recent_list`（写）与 `image/reference`（写）均正常 ⇒ 是这条 multipart 路径被单独拦，不是账号级限流。

**绕开方式**：参考图改图**不需要上传** —— `POST drawing-api/v1/image/reference` 的 `image_url`
接受**任意上游可达的公网图片 URL**（自家 CDN / 境内图床 / ghproxy 中转均可），`file_id` 可留空。

**多图不支持**（两条独立结论，详见 [`docs/生图协议与实操.md`](docs/生图协议与实操.md) §3.7）：

- `image/reference` 是单图接口：并列字段（`images`/`image_urls`/…）被静默忽略，数组直接 400；
- 对话通道的图片项（`content[].type=="image"`）**只有主对话 agent 会读**（可多图、可多轮），
  **画图 agent 完全忽略**。

⇒ 多角色同框的正确做法：**把多个角色合并到同一张参考图**（如角色设定集），用 prompt 分块锚定（§3.8 已验证）。

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
  生图协议与实操.md  生图（文生图 / 参考图改图）协议 + 线上实测实操
  HANDOFF.md       交接说明（目标、边界、验收标准）
examples/          最小示例
tests/             离线回归测试（用真实抓包帧）
```

## 9. License

MIT，见 [LICENSE](LICENSE)。
