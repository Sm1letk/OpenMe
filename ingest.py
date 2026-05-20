__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
import os, json, re, hashlib
from bs4 import BeautifulSoup
import chromadb
from dotenv import load_dotenv
from embedding import embed

load_dotenv()

CHROMA_PATH = os.environ["CHROMA_PATH"]
DATA_PATH   = os.environ["DATA_PATH"]

chroma = chromadb.PersistentClient(path=CHROMA_PATH)
col    = chroma.get_or_create_collection(
    "memories",
    metadata={"hnsw:space": "cosine"},
)


def chunk_id(source: str, idx: int) -> str:
    return hashlib.md5(f"{source}:{idx}".encode()).hexdigest()


def add_chunk(source: str, idx: int, text: str):
    text = text.strip()
    if not text or len(text) < 10:
        return
    cid = chunk_id(source, idx)
    # 跳过已存在的 chunk（断点续传）
    existing = col.get(ids=[cid])
    if existing["ids"]:
        return
    vec = embed(text)
    col.add(
        ids=[cid],
        embeddings=[vec],
        documents=[text],
        metadatas=[{"source": source, "private": True}],
    )
    print(f"  [{source}] #{idx} added ({len(text)} chars)")


# ── 1. Flomo 笔记 ──────────────────────────────────────────────────────
def ingest_flomo():
    path = os.path.join(DATA_PATH, "正觉的笔记.html")
    if not os.path.exists(path):
        print(f"Flomo: file not found at {path}, skipping")
        return
    with open(path, encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    memos = soup.select("li") or soup.select(".memo") or soup.find_all("div", class_=re.compile("memo|note"))
    if not memos:
        # fallback：提取所有 <p> 文本
        memos = soup.find_all("p")

    print(f"Flomo: found {len(memos)} items")
    for i, m in enumerate(memos):
        text = m.get_text(separator=" ", strip=True)
        add_chunk("flomo", i, text)


# ── 2. Claude 对话 ─────────────────────────────────────────────────────
def ingest_claude_conversations():
    path = os.path.join(DATA_PATH, "conversations.json")
    if not os.path.exists(path):
        print(f"Claude: file not found at {path}, skipping")
        return
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Claude 导出格式：列表，每项有 chat_messages
    idx = 0
    for conv in data:
        messages = conv.get("chat_messages") or conv.get("conversation") or []
        pairs = []
        for msg in messages:
            role = msg.get("sender") or msg.get("role", "")
            text = msg.get("text") or msg.get("content", "")
            if isinstance(text, list):  # content 可能是列表
                text = " ".join(t.get("text", "") for t in text if isinstance(t, dict))
            if role in ("human", "user") and text.strip():
                pairs.append(("Q", text.strip()))
            elif role in ("assistant",) and text.strip():
                pairs.append(("A", text.strip()))

        # 每两条 Q+A 合并为一个 chunk
        for j in range(0, len(pairs), 2):
            chunk = f"{pairs[j][1]}\n\n{pairs[j+1][1]}" if j+1 < len(pairs) else pairs[j][1]
            add_chunk("claude", idx, chunk)
            idx += 1

    print(f"Claude: ingested {idx} Q&A pairs")


# ── 3. Gemini 对话 ─────────────────────────────────────────────────────
def ingest_gemini():
    path = os.path.join(DATA_PATH, "chat-memo_49.txt")
    if not os.path.exists(path):
        print(f"Gemini: file not found at {path}, skipping")
        return
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    # 按空行分段，每段一个 chunk
    segments = [s.strip() for s in re.split(r"\n{2,}", raw) if s.strip()]
    print(f"Gemini: found {len(segments)} segments")
    for i, seg in enumerate(segments):
        add_chunk("gemini", i, seg)


# ── 主入口 ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== 开始数据入库 ===")
    ingest_flomo()
    ingest_claude_conversations()
    ingest_gemini()
    total = col.count()
    print(f"\n=== 完成！ChromaDB 共 {total} 条记录 ===")
