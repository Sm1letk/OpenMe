# /home/bots/suggest_topics.py
try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ModuleNotFoundError:
    pass

import os, json, time, re
import requests
import chromadb
from openai import OpenAI
from dotenv import load_dotenv
import persona

load_dotenv()

CHROMA_PATH    = os.environ["CHROMA_PATH"]
DATA_PATH      = os.environ["DATA_PATH"]
TARGET_CHAT_ID = os.environ["TARGET_CHAT_ID"]
APP_ID         = os.environ["SELF_FEISHU_APP_ID"]
APP_SECRET     = os.environ["SELF_FEISHU_APP_SECRET"]
FEISHU_API     = "https://open.feishu.cn/open-apis"
PENDING_PATH   = os.path.join(DATA_PATH, "pending_topics.json")
MODEL          = "MiniMax-M2.7"

_minimax = OpenAI(
    api_key=os.environ["MINIMAX_API_KEY"],
    base_url="https://api.minimaxi.com/v1",
)
_chroma = chromadb.PersistentClient(path=CHROMA_PATH)
_col = _chroma.get_or_create_collection("memories", metadata={"hnsw:space": "cosine"})

_token_cache = {"token": "", "expires_at": 0}


def _get_token() -> str:
    if time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    resp = requests.post(
        f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=10,
    ).json()
    if "tenant_access_token" not in resp:
        raise RuntimeError(f"获取 token 失败: {resp}")
    _token_cache["token"] = resp["tenant_access_token"]
    _token_cache["expires_at"] = time.time() + resp.get("expire", 7200)
    return _token_cache["token"]


def _send_text(text: str):
    """向 TARGET_CHAT_ID 发送文本消息"""
    requests.post(
        f"{FEISHU_API}/im/v1/messages?receive_id_type=chat_id",
        headers={"Authorization": f"Bearer {_get_token()}", "Content-Type": "application/json"},
        json={
            "receive_id": TARGET_CHAT_ID,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        },
        timeout=10,
    )


def _get_recent_docs(days: int = 3) -> list[dict]:
    """从 ChromaDB 取最近 N 天有 timestamp 元数据的内容"""
    cutoff = int(time.time()) - days * 24 * 3600
    try:
        result = _col.get(
            where={"timestamp": {"$gte": cutoff}},
            limit=100,
        )
        docs = []
        for doc, meta in zip(result["documents"], result["metadatas"]):
            docs.append({
                "text": doc,
                "url": meta.get("url", ""),
                "title": meta.get("title", ""),
                "source": meta.get("source", ""),
            })
        return docs
    except Exception as e:
        print(f"[WARN] ChromaDB 查询失败: {e}")
        return []


TOPIC_PROMPT = """你是「野生西兰花」（AI产品经理，非科班野蛮生长）的内容助手。
根据以下近3天的阅读素材，为她生成10条内容选题建议。

## 用户身份
{memories}

## 近3天阅读素材
{content}

## 输出要求
以JSON数组格式输出，每条包含以下字段：
- index: 序号（1-10）
- title: 选题标题（15字以内，有冲击力）
- angle: 创作切入点（25字以内）
- source_summary: 来源内容摘要（30字以内）
- source_url: 原文链接（无则填空字符串）

只输出JSON数组，不要输出任何其他内容。"""


def _generate_topics(docs: list[dict], memories: str) -> list[dict]:
    """调用 MiniMax 生成10条选题，返回结构化列表"""
    content_parts = []
    for d in docs[:20]:
        url_hint = f"（{d['url']}）" if d['url'] else ""
        content_parts.append(f"- {d['text'][:200]}{url_hint}")
    content = "\n".join(content_parts)

    prompt = TOPIC_PROMPT.format(
        memories=memories[:1000],
        content=content[:4000],
    )
    resp = _minimax.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=3000,
        temperature=0.8,
    )
    raw = resp.choices[0].message.content
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
    # 去掉 markdown 代码块包裹
    raw = re.sub(r'^```[a-z]*\n?', '', raw).rstrip('`').strip()

    # 提取 JSON 数组
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if not m:
        raise ValueError(f"MiniMax 未返回 JSON 数组: {raw[:200]}")
    try:
        topics = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e} | 原文: {raw[:200]}")
    if topics:
        print(f"[DEBUG] 首条选题字段: {list(topics[0].keys())}")
    return topics


def _format_push_message(topics: list[dict]) -> str:
    """格式化推送消息"""
    lines = ["📋 今日选题建议（回复序号选择，如「3」或「3，聚焦在XX角度」）\n"]
    for t in topics:
        url = t.get('source_url') or t.get('url') or ''
        summary = t.get('source_summary') or t.get('summary') or t.get('source') or ''
        lines.append(
            f"{t.get('index', '')}. {t.get('title', '')}\n"
            f"   角度：{t.get('angle', '')}\n"
            f"   来源：{summary}"
            + (f"（{url}）" if url else "")
        )
    return "\n\n".join(lines)


def main():
    print("=== 选题推送开始 ===")
    docs = _get_recent_docs(days=3)
    if not docs:
        print("近3天无新入库内容，跳过推送")
        return

    print(f"找到近3天内容：{len(docs)} 条")
    memories = persona.load_context()
    topics = _generate_topics(docs, memories)
    print(f"生成选题：{len(topics)} 条")

    # 缓存选题（含原始 docs 供草稿生成使用）
    pending = {
        "generated_at": int(time.time()),
        "topics": topics,
        "docs_by_index": {
            str(t["index"]): (
                [d for d in docs if t.get("source_url") and t["source_url"] in d.get("url", "")]
                or docs[:3]
            )
            for t in topics
        }
    }
    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)

    msg = _format_push_message(topics)
    _send_text(msg)
    print("推送完成")


if __name__ == "__main__":
    main()
