try:
    __import__('pysqlite3')
    import sys as _sys
    _sys.modules['sqlite3'] = _sys.modules.pop('pysqlite3')
except ModuleNotFoundError:
    pass

import os, json, time, re, sqlite3
from datetime import datetime
import requests
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from openai import OpenAI
from dotenv import load_dotenv
from rag import retrieve
from content_ingest import ingest_url, detect_url_type
from feishu_docs import create_doc
import persona

load_dotenv()

APP_ID     = os.environ["SELF_FEISHU_APP_ID"]
APP_SECRET = os.environ["SELF_FEISHU_APP_SECRET"]
MODEL      = "MiniMax-M2.7"
PENDING_TOPICS_PATH = os.path.join(os.environ["DATA_PATH"], "pending_topics.json")
PENDING_TTL = 24 * 3600  # 24小时内的选题缓存视为有效

LONG_DRAFT_PROMPT = """你是一个正在写作的知识工作者，根据下面的个人背景和素材，写一篇深度文章。

## 我的个人背景
{memories}

## 本篇素材（来自我的真实阅读和思考）
{materials}

## 写作要求
- 主题聚焦：围绕「{theme}」展开，角度：{angle}
- 字数：800-1200字
- 风格：有观点，有例子，有判断，不说废话
- 结构：开头一句话抓住核心观点，正文展开，结尾一句话收尾
- 语气：第一人称，像在和朋友分享，不用"首先/其次/最后"等套路词
- 不要写"作为一个XX"这类开头
- 【脱敏要求】不得出现任何真实公司名、产品名、客户名，用通用描述替代
- 【语言要求】必须全程中文，不得出现英文
- 【格式要求】直接输出最终内容，不要输出任何分析、思考过程或写作计划
- 第一行是标题（不加"标题："前缀）

现在开始写："""

SHORT_DRAFT_PROMPT = """根据下面的个人背景和素材，写一条适合即刻/小红书的短内容。

## 我的个人背景
{memories}

## 素材
{materials}

## 写作要求
- 主题：「{theme}」，角度：{angle}
- 字数：150-300字
- 风格：口语化，有个人观点，像朋友圈分享
- 开头一句话要有冲击力
- 可以有1-3个要点，不用数字编号
- 结尾可以有一个反问或开放性问题
- 不加标签（#）
- 【脱敏要求】不得出现任何真实公司名、产品名、客户名
- 【语言要求】必须全程中文，不得出现英文
- 【格式要求】直接输出最终内容，不要输出任何分析、思考过程

现在写："""

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

SYSTEM = persona.build_system("private")


def _remember(content: str) -> None:
    global SYSTEM
    persona.append_memory(content)
    SYSTEM = persona.reload()


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
            _remember(content)
            _send_text(chat_id, f"已记住：{content}")
        return

    # 选题回复分支：检测是否为"数字"或"数字，方向"格式
    topic_match = re.match(r'^\s*(\d+)\s*(?:[，,]\s*(.+))?\s*$', text)
    if topic_match and _has_valid_pending_topics():
        index = int(topic_match.group(1))
        direction = (topic_match.group(2) or "").strip()
        _send_text(chat_id, "正在生成草稿...")
        try:
            url = _generate_and_save_draft(index, direction)
            _send_text(chat_id, f"✅ 草稿已生成：{url}")
        except Exception as e:
            _send_text(chat_id, f"❌ 草稿生成失败：{type(e).__name__}: {str(e)[:200]}")
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


# ── 选题草稿辅助 ──────────────────────────────────────────────────────────────

def _has_valid_pending_topics() -> bool:
    """判断是否存在有效的（24小时内）待确认选题"""
    if not os.path.exists(PENDING_TOPICS_PATH):
        return False
    try:
        with open(PENDING_TOPICS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return time.time() - data.get("generated_at", 0) < PENDING_TTL
    except Exception:
        return False


def _generate_and_save_draft(index: int, direction: str) -> str:
    """
    根据选题序号和可选方向生成草稿，创建飞书文档，返回文档 URL。
    """
    with open(PENDING_TOPICS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    topics = {t["index"]: t for t in data["topics"]}

    if index not in topics:
        raise ValueError(f"选题序号 {index} 不存在（共 {len(topics)} 条）")

    topic = topics[index]
    docs_list = data.get("docs_by_index", {}).get(str(index), [])
    materials = "\n\n---\n\n".join(
        d["text"][:500] if isinstance(d, dict) else str(d)
        for d in docs_list[:5]
    )

    theme = topic["title"]
    angle = direction if direction else topic.get("angle", "")
    memories = persona.load_context()

    # 判断长文/短文（direction 含"短文"则短文，否则默认长文）
    is_short = "短文" in direction

    if is_short:
        prompt = SHORT_DRAFT_PROMPT.format(
            memories=memories[:800],
            materials=materials[:1500],
            theme=theme,
            angle=angle,
        )
        max_tokens = 800
    else:
        prompt = LONG_DRAFT_PROMPT.format(
            memories=memories[:1500],
            materials=materials[:3000],
            theme=theme,
            angle=angle,
        )
        max_tokens = 2000

    resp = ai.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.8,
    )
    content = resp.choices[0].message.content
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

    # 提取标题（长文第一行，短文用 theme）
    if not is_short:
        lines = content.strip().split('\n')
        doc_title = lines[0].lstrip('#').strip() or theme
    else:
        doc_title = theme

    date_str = datetime.now().strftime("%Y-%m-%d")
    doc_full_title = f"{date_str}_{doc_title}"

    doc_url = create_doc(doc_full_title, content)
    return doc_url


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
