"""命令行入口：python -m glm2api <子命令>。

凭据来源（按优先级）：
  --cred PATH     显式指定凭据 JSON 文件
  $GLM_CREDENTIAL_FILE
  ./glm_credential.json（存在则用）
  环境变量 GLM_REFRESH_TOKEN / GLM_DEVICE_ID / GLM_COOKIE（无文件时）

⚠️ 涉及写操作（rename / delete）会真实改动账号数据，请确认后再执行。
"""

import argparse
import json
import sys

from . import constants as C
from .client import GlmClient
from .credential import load_credential
from .errors import GlmError


def _emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_user_info(client: GlmClient, args) -> None:
    info = client.user_info()
    # 积分余额单位是「分」（100 分 = 1 积分），单列出来便于核对
    _emit({"uid": info.get("_id") or client.cred.user_id,
           "nickname": info.get("nickname"),
           "is_guest": info.get("is_guest"),
           "left_score": (info.get("member_info") or {}).get("left_score"),
           "member_info": info.get("member_info")})


def cmd_models(client: GlmClient, args) -> None:
    res = client.models()
    for m in res.get("models") or []:
        print(f"{m.get('selected_model'):<20} {m.get('display_name')}  tags={m.get('display_tag')}")
    print("\ndefault:", json.dumps(res.get("default"), ensure_ascii=False))


def cmd_conversations(client: GlmClient, args) -> None:
    if args.all:
        for i, conv in enumerate(client.iter_conversations(), 1):
            print(f"{i:>3} {conv.get('conversation_id')} {conv.get('update_time')} "
                  f"{conv.get('title')}")
        return
    res = client.list_conversations(page=args.page, page_size=args.page_size)
    _emit(res)


def cmd_chat(client: GlmClient, args) -> None:
    prompt = args.prompt or sys.stdin.read()
    if args.stream:
        # 原样透传：逐块写 stdout.buffer，不解析、不补 [DONE]
        for chunk in client.chat(args.assistant, prompt, conversation_id=args.conversation,
                                 stream=True, model=args.model, chat_mode=args.chat_mode,
                                 reasoning_effort=args.effort,
                                 is_networking=not args.no_networking):
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        return
    res = client.chat(args.assistant, prompt, conversation_id=args.conversation,
                      stream=False, model=args.model, chat_mode=args.chat_mode,
                      reasoning_effort=args.effort, is_networking=not args.no_networking)
    _emit({k: v for k, v in res.items() if k != "parts"})
    if res.get("thinking"):
        print("\n--- thinking ---\n" + res["thinking"], file=sys.stderr)


def cmd_history(client: GlmClient, args) -> None:
    _emit(client.history(args.conversation, assistant_id=args.assistant, page_size=args.page_size))


def cmd_rename(client: GlmClient, args) -> None:
    client.rename_conversation(args.conversation, args.title)
    print("ok")


def cmd_delete(client: GlmClient, args) -> None:
    client.delete_conversations(args.ids, assistant_id=args.assistant)
    print(f"已删除 {len(args.ids)} 个会话")


def cmd_refresh(client: GlmClient, args) -> None:
    client.ensure_token(force=True)
    print(f"刷新成功  uid={client.cred.user_id}  access_exp={client.cred.access_exp}")
    if client.cred.source and client.cred.source != "env":
        print(f"新 refresh_token 已落盘: {client.cred.source}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("glm2api", description="智谱清言网页版私有接口客户端")
    p.add_argument("--cred", help="凭据文件路径（默认 $GLM_CREDENTIAL_FILE 或 ./glm_credential.json）")
    p.add_argument("--base", default=C.BASE, help="上游基址（默认 %(default)s）")
    p.add_argument("--proxy", help="HTTP 代理，如 http://127.0.0.1:10809")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("user-info", help="用户信息（含积分余额）").set_defaults(fn=cmd_user_info)

    sub.add_parser("models", help="可用模型列表").set_defaults(fn=cmd_models)

    pc = sub.add_parser("conversations", help="会话列表")
    pc.add_argument("-p", "--page", type=int, default=1)
    pc.add_argument("-s", "--page-size", type=int, default=C.DEFAULT_PAGE_SIZE)
    pc.add_argument("-a", "--all", action="store_true", help="自动翻页输出全部")
    pc.set_defaults(fn=cmd_conversations)

    pch = sub.add_parser("chat", help="发一条消息")
    pch.add_argument("prompt", nargs="?", help="提问内容（缺省从 stdin 读）")
    pch.add_argument("-c", "--conversation", default=None, help="会话 ID；省略=新会话")
    pch.add_argument("-a", "--assistant", default=C.DEFAULT_ASSISTANT)
    pch.add_argument("-m", "--model", default=C.DEFAULT_MODEL)
    pch.add_argument("--chat-mode", default="", help="thinking / deep_research / 空")
    pch.add_argument("--effort", default=None, help="thinking=深度 / deep_thinking=极致")
    pch.add_argument("--no-networking", action="store_true", help="关闭联网检索")
    pch.add_argument("--stream", action="store_true", help="原样透传上游 SSE")
    pch.set_defaults(fn=cmd_chat)

    ph = sub.add_parser("history", help="会话历史消息（list 结构未实证，原样透传）")
    ph.add_argument("conversation")
    ph.add_argument("-a", "--assistant", default=C.DEFAULT_ASSISTANT)
    ph.add_argument("-s", "--page-size", type=int, default=C.DEFAULT_HISTORY_PAGE_SIZE)
    ph.set_defaults(fn=cmd_history)

    pr = sub.add_parser("rename", help="重命名会话")
    pr.add_argument("conversation")
    pr.add_argument("title")
    pr.set_defaults(fn=cmd_rename)

    pd = sub.add_parser("delete", help="删除会话（走 bulk_delete）")
    pd.add_argument("ids", nargs="+")
    pd.add_argument("-a", "--assistant", default=C.DEFAULT_ASSISTANT)
    pd.set_defaults(fn=cmd_delete)

    sub.add_parser("refresh", help="强制刷新 access token 并落盘").set_defaults(fn=cmd_refresh)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
    try:
        cred = load_credential(args.cred)
        with GlmClient(cred, base=args.base, proxies=proxies) as client:
            args.fn(client, args)
    except GlmError as e:
        print(f"[glm2api] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
