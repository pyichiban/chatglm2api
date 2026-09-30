"""最小示例：会话生命周期（列表 → 历史 → 改名 → 删除）。

⚠️ rename / delete 会**真实改动账号数据**，请确认会话 ID 后再运行。
delete 走 bulk_delete（删单个也用数组）。

    GLM_REFRESH_TOKEN='eyJ...' python examples/03_manage.py <conversation_id>
"""

import sys

from glm2api import DEFAULT_ASSISTANT, GlmClient, load_credential


def main(conversation_id: str | None = None) -> None:
    with GlmClient(load_credential()) as client:
        if not conversation_id:
            convs = list(client.iter_conversations())
            print(f"共 {len(convs)} 个会话，前 5 个：")
            for c in convs[:5]:
                print(f"  {c.get('conversation_id')}  {c.get('title')}")
            if not convs:
                return
            conversation_id = convs[0]["conversation_id"]

        # 历史消息：⚠️ list 元素结构未实证（见 README §5），这里只打印顶层键
        res = client.history(conversation_id, assistant_id=DEFAULT_ASSISTANT)
        print(f"\n历史信封: keys={list(res)}  list={len(res.get('list') or [])} "
              f"little_more={res.get('little_more')} greate_more={res.get('greate_more')}")

        # 当前标题（新建会话后前端会立刻取一次自动生成的标题）
        print(f"当前标题: {client.conversation_title(conversation_id)!r}")

        # 改名
        client.rename_conversation(conversation_id, "chatglm2api 示例标题")
        print(f"改后标题: {client.conversation_title(conversation_id)!r}")

        # 删除（谨慎：取消下面注释即真的删除）
        # client.delete_conversation(conversation_id)
        # print("已删除")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
