"""最小示例：对话（非流式聚合 + 流式原样透传 + 多轮）。

关键点（PROTOCOL §3.6）：
  * messages 只带当前这一条，多轮上下文靠 conversation_id 由服务端维护；
  * 首轮 conversation_id 传 None，真实 ID 从流首帧回传；
  * 流式透传**不补 [DONE]**——上游正常收尾的标志是顶层 status:"finish"。

    GLM_REFRESH_TOKEN='eyJ...' python examples/02_chat.py
"""

from glm2api import DEFAULT_ASSISTANT, DEFAULT_MODEL, GlmClient, load_credential


def main() -> None:
    with GlmClient(load_credential()) as client:
        # ① 非流式：内部用缓存池聚合，think 与 text 分开返回
        res = client.chat(DEFAULT_ASSISTANT, "用一句话说明什么是递归",
                          model=DEFAULT_MODEL, chat_mode="thinking", reasoning_effort="high")
        print("--- 非流式 ---")
        print(f"会话 ID: {res['conversation_id']}  (消息 ID: {res['history_id']})")
        print(f"思考: {res['thinking'][:80]}..." if res["thinking"] else "思考: (无)")
        print(f"回答: {res['text']}")

        # ② 多轮：只发新消息，带上上一轮的 conversation_id
        follow = client.chat(DEFAULT_ASSISTANT, "再举个生活中的例子",
                             conversation_id=res["conversation_id"])
        print("\n--- 追问 ---")
        print(follow["text"])

        # ③ 流式：原样透传上游 SSE 字节，逐帧打印
        print("\n--- 流式透传（原始 SSE）---")
        for chunk in client.chat(DEFAULT_ASSISTANT, "从 1 数到 5", stream=True,
                                 chat_mode="thinking", reasoning_effort="high"):
            print(chunk.decode("utf-8", "replace"), end="", flush=True)  # noqa: T201
        print()


if __name__ == "__main__":
    main()
