# IDE 渠道生图（AI 画图）协议分析

> 来源：`ref/chatglm_ide_20260930.saz`（2026-09-30 16:32–16:37，IDE 普通聊天模式）
> 关键会话：sid 781（生图对话）、760（参考图改图，失败）、470/471（画图配置）、791-794（成品图下载）、800（update_status）
> 结论：**生图走的是普通对话通道** `backend-api/assistant/stream`，仅换 `assistant_id` 与 `meta_data.cogview`，无独立生图端点。

---

## 1. 一句话结论

AI 画图 = `POST /chatglm/backend-api/assistant/stream` + `assistant_id=65a232c082ff90a2ad2f15e2`（AI画图智能体）
+ `meta_data.cogview={aspect_ratio,style,scene,...}`。SSE 流里以 `content[].type=="image"`
逐张推送成品图 URL，末帧 `type=="one_to_more_finish"` 收尾。**没有任何独立的"创建生图任务"接口**。

---

## 2. 请求（sid 781 实证）

```
POST /chatglm/backend-api/assistant/stream
Accept: text/event-stream
（其余签名/伪装头与普通对话完全一致：IDE 指纹，无 refer__991）
```

请求体（与普通对话的差异只有两处，已标注）：

```json
{
  "assistant_id": "65a232c082ff90a2ad2f15e2",   // ← 差异1：AI画图智能体（非 65940acf…主对话）
  "conversation_id": "",
  "project_id": "",
  "chat_type": "user_chat",
  "meta_data": {
    "cogview": {                                  // ← 差异2：生图参数
      "aspect_ratio": "3:4",                      //   宽高比（WEB 端可选 1:1 / 3:4 / 4:3 / 9:16 / 16:9）
      "style": "none",                            //   风格（none=默认）
      "scene": "none",                            //   场景（none=默认）
      "chat_model": "",
      "rm_label_watermark": false                 //   是否去水印
    },
    "is_test": false,
    "input_question_type": "xxxx",
    "channel": "",
    "draft_id": "",
    "chat_mode": "",
    "reasoning_effort": "low",
    "selected_model": "glm-5.3-flash",
    "is_networking": false,
    "quote_log_id": "",
    "platform": "win"
  },
  "messages": [{"role": "user", "content": [{"type": "text", "text": "<画图提示词>"}]}]
}
```

> 对比主对话（sid 281）：`meta_data.cogview` 在普通对话里只有 `{"rm_label_watermark":false}`，
> 生图时多出 `aspect_ratio / style / scene / chat_model` 四个键。

---

## 3. SSE 响应帧（6 帧，16.1s 完成一次 4 图生成）

```
帧0  top=init   part=init    content=[{type:"text", text:""}]                 ← 空文本占位
帧1  top=init   part=init    content=[{type:"image", image:[{image_url:..._3_0.jpg}]}]   ← 第1张
帧2  top=init   part=init    content=[{type:"image", image:[{image_url:..._0_0.jpg}]}]   ← 第2张
帧3  top=init   part=init    content=[{type:"image", image:[{image_url:..._1_0.jpg}]}]   ← 第3张
帧4  top=init   part=init    content=[{type:"image", image:[{image_url:..._2_0.jpg}]}]   ← 第4张
帧5  top=finish part=finish  content=[{type:"one_to_more_finish", image:[{}]}]           ← 收尾
```

要点：

| 项 | 值 | 说明 |
|---|---|---|
| `content[].type` | `image` | 生图内容项，与 text/think 平级 |
| `image[].image_url` | `https://sfile.chatglm.cn/testpath/<history_id>_<n>_0.jpg` | 成品图直链（`<n>` 为序号 0-3） |
| `content[].code` | 改写后的画图提示词 | 服务端把用户 prompt 润色后的实际生图 prompt（`intent_original_output` 为原文） |
| 末帧 type | `one_to_more_finish` | "一次生成多图"的收尾标记，`status:"finish"` |
| 顶层 `meta_data.cogview.generate_image_type` | 1 | 生图类型标记 |
| 顶层 `meta_data.answer_type` | `multi` | 多图回答 |
| `total_time` | 16.1s | 单次 4 图耗时 |

**聚合规则**：沿用现有 `sse.ChatAggregate` —— `image` 类型项在 `TEXT_TYPES` 之外，
需要在 `render_part()` 中新增处理：把 `image[].image_url` 转成 Markdown `![](url)`。
末帧 `one_to_more_finish` 无正文，聚合时忽略即可（顶层 `status=="finish"` 已满足收尾判定）。

---

## 4. 配套请求（同一生图会话的完整生命周期）

| sid | 请求 | 作用 | 结果 |
|---|---|---|---|
| 781 | `POST assistant/stream` | 生图（4 张） | ✅ 6 帧 SSE |
| 785 | `GET conversation/title?conversation_id=…` | 生图会话的自动标题 | ✅ 标题=用户 prompt 摘要 |
| 800 | `POST stream/update_status` | body `{"history_id":"6abcc9f0cf54e013be725886"}` | ✅ status=0 |
| 791-794 | `GET sfile.chatglm.cn/testpath/<id>_<n>_0.jpg` | 下载 4 张成品图（无需签名，公开 CDN） | ✅ |

> 图片直链 `sfile.chatglm.cn/testpath/...` **不需要签名头**，可直接 GET 下载（浏览器就是这么拉的）。
> URL 带 `?image_process=format,webp` 是 CDN 的格式转换参数，去掉即原图 jpg。

---

## 5. 参考图改图（sid 760，本次失败）

```
POST /chatglm/drawing-api/v1/image/reference
{"image_url":"https://t1.chatglm.cn/file/6abcc9abc03cf57c41399cb1.png?...",
 "file_id":"6abcc9abc03cf57c41399cb1",
 "type":"Intelligent Editing",       // 智能编辑模式
 "category":1,
 "conversation_id":"", "history_id":"",
 "assistant_id":"65a232c082ff90a2ad2f15e2",
 "prompt":"变成古风美人",
 "if_plus_model":...}
→ {"status":500,"message":"internal server error"}
```

- 这是「智能编辑/参考图改图」入口，需要先 `productivity-api/file/chat_upload` 上传文件拿 `file_id`（sid 750）。
- **本次抓包中该请求 500 失败**，属于上游异常，协议本身存在但响应结构未实证。
- 与本工具主链路无关（生图主流程走 `assistant/stream` 即可）。

---

## 6. 画图配置接口（sid 470/471，备查）

```
GET /chatglm/feed-api/drawing/config      # 运营配置：风格/场景预设（含示例 prompt）
GET /chatglm/drawing-api/v1/drawing/config # 生图能力配置：宽高比、风格列表、参考图样例
```

`drawing-api/v1/drawing/config` 返回 `styles / scenes / aspect_ratios / reference_image / title`
等字段（含「定制写真」等预设模板）。**本工具不需要调用**——`cogview` 参数直接传 `none/none` 即默认出图。

---

## 7. 对本工具的改动建议

1. **`sse.py`**：`render_part()` 新增 `image` 类型 → 产出 Markdown 图片链接；`TEXT_TYPES` 保持不变。
2. **`client.py`**：`chat()` 无需改——生图只是换 `assistant_id` + `meta_data.cogview`。
   建议加便捷封装 `generate_image(prompt, aspect_ratio="1:1", ...)`。
3. **`constants.py`**：新增 `ASSISTANT_DRAWING = "65a232c082ff90a2ad2f15e2"`。
4. 末帧 `one_to_more_finish` 的 `image:[{}]` 为空对象，聚合时跳过空 image 项。

> 已知限制：`aspect_ratio/style/scene` 的**合法取值枚举**本次抓包未完整实证
> （仅见 `3:4` / `none`），完整列表需调 `drawing-api/v1/drawing/config` 获取或再抓包。
