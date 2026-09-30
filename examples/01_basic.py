"""最小示例：用户信息 / 模型 / 会话列表。

运行前设置 GLM_REFRESH_TOKEN（或 GLM_CREDENTIAL_FILE 指向凭据文件）：
    GLM_REFRESH_TOKEN='eyJ...' python examples/01_basic.py
"""

from glm2api import DEFAULT_ASSISTANT, GlmClient, load_credential


def main() -> None:
    with GlmClient(load_credential()) as client:
        # 用户信息。积分余额单位是「分」：left_score="2000.00" 即 20 积分。
        info = client.user_info()
        print(f"昵称: {info.get('nickname')}  访客: {info.get('is_guest')}")
        print(f"积分: {client.left_score()} 分 = {client.left_score() / 100:.2f} 积分")

        # 可用模型（仅 am_switch / chat_switch / display_tag 三个能力字段，无上下文长度等）
        print("\n可用模型:")
        for m in client.models().get("models") or []:
            print(f"  {m.get('selected_model'):<20} {m.get('display_name')} {m.get('display_tag')}")

        # 会话列表：翻页是纯页码，has_more 决定是否继续
        print("\n最近会话:")
        for conv in client.iter_conversations():
            print(f"  [{conv.get('assistant_id')}] {conv.get('title')}  ({conv.get('conversation_id')})")
            if conv.get("assistant_id"):
                break  # 示例只打第一条

    print(f"\n主对话 assistant_id = {DEFAULT_ASSISTANT}")


if __name__ == "__main__":
    main()
