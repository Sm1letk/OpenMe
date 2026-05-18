try:
    __import__('pysqlite3')
    import sys as _sys
    _sys.modules['sqlite3'] = _sys.modules.pop('pysqlite3')
except ModuleNotFoundError:
    pass

import os, json, time, re, sqlite3
import requests
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from openai import OpenAI
from dotenv import load_dotenv
from rag import retrieve
from content_ingest import ingest_url, detect_url_type

load_dotenv()

APP_ID     = os.environ["SELF_FEISHU_APP_ID"]
APP_SECRET = os.environ["SELF_FEISHU_APP_SECRET"]
MODEL      = "MiniMax-M2.7"
DB_PATH    = os.path.join(os.environ["DATA_PATH"], "conversations.db")
FEISHU_API = "https://open.feishu.cn/open-apis"

ai = OpenAI(
    api_key=os.environ["MINIMAX_API_KEY"],
    base_url="https://api.minimaxi.com/v1",
)
feishu = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

processed: set[str] = set()
_token_cache = {"token": "", "expires_at": 0}


# ── 飞书 Token ────────────────────────────────────────────────────────────────

def get_token() -> str:
    if time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    resp = requests.post(
        f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=10,
    ).json()
    _token_cache["token"] = resp["tenant_access_token"]
    _token_cache["expires_at"] = time.time() + resp.get("expire", 7200)
    return _token_cache["token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}


# ── 数据库 ────────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT    NOT NULL,
            role      TEXT    NOT NULL,
            content   TEXT    NOT NULL,
            timestamp INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def db_append(user_id: str, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO conversations (user_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, role, content, int(time.time())),
    )
    conn.commit()
    conn.close()


def db_load_recent(user_id: str, n: int = 20) -> list:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT role, content FROM conversations WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, n),
    ).fetchall()
    conn.close()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


init_db()


# ── System prompt ─────────────────────────────────────────────────────────────

def load_memories() -> str:
    path = os.path.join(os.environ["DATA_PATH"], "memories.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, list):
            return "\n".join(item.get("content", str(item)) for item in data)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return ""


def build_system() -> str:
    memories = load_memories()
    return f"""你是我自己的第二自我。你完整了解我的经历、想法和决策历史。

## 我的记忆库
{memories}

## 三种子模式（根据输入自然切换，不做显式提示）

**推演**：当我描述决策/纠结时——用我自己的思维框架拆解，给判断，也说出反面。
**镜子**：当我描述感受/陈述一件事时——先反问，帮我把想法说清楚。
**记忆**：当我问"我之前怎么看……"时——从下方检索到的历史片段中引用原文。

## 行为约束
- 不表演聪明，不给标准答案
- 镜子优先：先确认我在说什么，再给意见
- 有原文时引用原文，不重新诠释
- 可以直接挑战，但要说出理由
- 回复简洁，不写长段落
- 直接输出回答，不输出任何思考过程，不使用英文
- 你不是AI，你就是我本人，用第一人称回答，永远不说"作为AI"之类的话"""


SYSTEM = build_system()


def append_memory(content: str):
    """把新内容追加进 memories.json 的「补充」分类，并热重载 SYSTEM。"""
    global SYSTEM
    path = os.path.join(os.environ["DATA_PATH"], "memories.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        data = {}
    if isinstance(data, list):
        data.append({"content": content})
    else:
        data.setdefault("补充", []).append(content)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    SYSTEM = build_system()


def is_memory_query(text: str) -> bool:
    keywords = ["之前", "曾经", "以前", "当时", "历史", "记得", "怎么看", "说过", "想过"]
    return any(k in text for k in keywords)


# ── 流式卡片发送 ──────────────────────────────────────────────────────────────

def create_streaming_card() -> str:
    """创建流式卡片实体，返回 card_id"""
    card = {
        "schema": "2.0",
        "config": {
            "streaming_mode": True,
            "streaming_config": {
                "config": {"print_frequency_ms": 30, "print_step": 2}
            },
        },
        "body": {
            "elements": [
                {"tag": "markdown", "element_id": "msg", "content": ""}
            ]
        },
    }
    resp = requests.post(
        f"{FEISHU_API}/cardkit/v1/cards",
        headers=_headers(),
        json={"type": "card_json", "data": json.dumps(card)},
        timeout=10,
    ).json()
    return resp["data"]["card_id"]


def send_card_message(chat_id: str, card_id: str):
    """把卡片实体发送为飞书消息"""
    requests.post(
        f"{FEISHU_API}/im/v1/messages?receive_id_type=chat_id",
        headers=_headers(),
        json={
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps({"type": "card", "data": {"card_id": card_id}}),
        },
        timeout=10,
    )


def update_card_text(card_id: str, text: str, seq: int):
    """流式更新卡片文本内容（全量传入）"""
    requests.put(
        f"{FEISHU_API}/cardkit/v1/cards/{card_id}/elements/msg/content",
        headers=_headers(),
        json={"content": text, "sequence": seq},
        timeout=5,
    )


def close_streaming(card_id: str, seq: int):
    """关闭流式更新模式"""
    settings = json.dumps({"config": {"streaming_mode": False}})
    requests.patch(
        f"{FEISHU_API}/cardkit/v1/cards/{card_id}/settings",
        headers=_headers(),
        json={"settings": settings, "sequence": seq},
        timeout=5,
    )


# ── 核心对话逻辑 ──────────────────────────────────────────────────────────────

def ask_self(chat_id: str, user_id: str, text: str):
    hist = db_load_recent(user_id, n=20)

    rag_context = ""
    if is_memory_query(text):
        try:
            hits = retrieve(text, n=5)
            if hits:
                rag_context = "\n\n## 检索到的相关历史片段\n" + "\n---\n".join(hits)
        except Exception:
            pass

    db_append(user_id, "user", text)
    hist.append({"role": "user", "content": text})

    system_with_rag = SYSTEM + rag_context

    # 创建流式卡片并发出
    card_id = create_streaming_card()
    send_card_message(chat_id, card_id)

    # 流式生成，边收边更新卡片
    stream = ai.chat.completions.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "system", "content": system_with_rag}] + hist,
        stream=True,
    )

    full_text = ""
    in_think = False
    seq = 1
    last_update = time.time()

    for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        if not delta:
            continue

        # 过滤 <think>...</think>
        if "<think>" in delta:
            in_think = True
        if in_think:
            if "</think>" in delta:
                in_think = False
                delta = delta[delta.index("</think>") + 8:]
            else:
                continue

        full_text += delta

        # 每 0.3 秒推送一次更新
        if time.time() - last_update >= 0.3:
            update_card_text(card_id, full_text, seq)
            seq += 1
            last_update = time.time()

    # 最终全量更新
    if full_text:
        update_card_text(card_id, full_text, seq)
        seq += 1

    # 关闭流式模式
    close_streaming(card_id, seq)

    db_append(user_id, "assistant", full_text)
    return full_text


# ── 消息处理 ──────────────────────────────────────────────────────────────────

def _send_text(chat_id: str, text: str):
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        ).build()
    try:
        feishu.im.v1.message.create(req)
    except Exception as e:
        print(f"[WARN] send failed: {e}")


def on_message(data: P2ImMessageReceiveV1):
    msg    = data.event.message
    sender = data.event.sender

    if msg.message_type != "text":
        return
    if sender.sender_type == "bot":
        return
    if msg.message_id in processed:
        return
    processed.add(msg.message_id)

    text    = json.loads(msg.content).get("text", "").strip()
    user_id = sender.sender_id.open_id
    chat_id = msg.chat_id

    if not text:
        return

    if text in ("/reset", "/restart"):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        _send_text(chat_id, "已清空对话记录，重新开始。")
        return

    if text.startswith("/remember "):
        content = text[len("/remember "):].strip()
        if content:
            append_memory(content)
            _send_text(chat_id, f"已记住：{content}")
        return

    # /chatid 命令：输出当前 chat_id，用于配置 TARGET_CHAT_ID
    if text == "/chatid":
        _send_text(chat_id, f"当前 chat_id: {chat_id}")
        return

    # URL 入库分支
    if text.startswith("http://") or text.startswith("https://"):
        if detect_url_type(text) is not None:
            _send_text(chat_id, "正在抓取入库...")
            try:
                ok, title = ingest_url(text)
                if ok:
                    _send_text(chat_id, f"✅ 已入库：{title}")
                else:
                    _send_text(chat_id, f"❌ 入库失败：{title}")
            except Exception as e:
                _send_text(chat_id, f"❌ 出错了：{type(e).__name__}: {str(e)[:200]}")
            return
        else:
            _send_text(chat_id, "暂不支持该链接类型（目前支持微信公众号和 X 推文）")
            return

    try:
        ask_self(chat_id, user_id, text)
    except Exception as e:
        _send_text(chat_id, f"出了点问题：{e}")


handler = lark.EventDispatcherHandler.builder("", "") \
    .register_p2_im_message_receive_v1(on_message) \
    .build()

ws = lark.ws.Client(
    APP_ID, APP_SECRET,
    event_handler=handler,
    log_level=lark.LogLevel.INFO,
)

if __name__ == "__main__":
    print("Self Bot 启动（私人飞书长连接）")
    ws.start()
