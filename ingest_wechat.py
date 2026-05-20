__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

import os, json, re, hashlib
from pathlib import Path
import chromadb
from dotenv import load_dotenv
from embedding import embed

load_dotenv()

CHROMA_PATH = os.environ["CHROMA_PATH"]
DATA_PATH   = os.environ["DATA_PATH"]
TEXTS_DIR   = os.path.join(DATA_PATH, "texts")

chroma = chromadb.PersistentClient(path=CHROMA_PATH)
col    = chroma.get_or_create_collection("memories", metadata={"hnsw:space": "cosine"})

# ── 关系映射（文件名 → 匿名标签） ──────────────────────────────────────
# 格式："私聊_微信昵称": "关系标签"
# 标签建议：自己 / 朋友A / 前同事A / 老板 / 恋爱对象 / 家人 等
# 文件名对应 WeFlow 导出的 JSONL 文件名（不含 .jsonl 后缀）
RELATIONSHIP_MAP = {
    "私聊_你的微信昵称":   "自己",
    "私聊_联系人昵称1":    "朋友A",
    "私聊_联系人昵称2":    "前同事A",
    # 继续添加...
}

# 用户自己的微信名（用于内容脱敏，替换为"我"）
USER_NAMES = ["你的微信名", "英文名"]

# 敏感信息正则
SENSITIVE_PATTERNS = [
    re.compile(r'1[3-9]\d{9}'),           # 手机号
    re.compile(r'\d{17}[\dX]'),           # 身份证
    re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}'),  # 邮箱
]

WINDOW_SECONDS  = 30 * 60   # 30 分钟一个对话段
MIN_USER_CHARS  = 50        # 用户发言总字数低于此阈值，丢弃该段
MIN_USER_MSGS   = 2         # 用户发言条数低于此阈值，丢弃该段


def chunk_id(source: str, idx: int) -> str:
    return hashlib.md5(f"{source}:{idx}".encode()).hexdigest()


def add_chunk(source: str, idx: int, text: str, relation: str):
    text = text.strip()
    if not text or len(text) < 20:
        return
    cid = chunk_id(source, idx)
    if col.get(ids=[cid])["ids"]:
        return
    vec = embed(text)
    col.add(
        ids=[cid],
        embeddings=[vec],
        documents=[text],
        metadatas=[{"source": source, "relation": relation, "private": True}],
    )


def clean_content(text: str, real_name: str, label: str) -> str:
    """脱敏：替换真实名字、去除敏感信息、清理引用格式。"""
    # 去除 [引用 xxx：yyy] 引用标记（保留回复内容本身）
    text = re.sub(r'\[引用[^\]]*\]', '', text)
    # 用户名 → 我
    for name in USER_NAMES:
        text = text.replace(name, "我")
    # 对方名字 → 关系标签
    if real_name:
        text = text.replace(real_name, label)
    # 敏感信息 → [已隐藏]
    for pat in SENSITIVE_PATTERNS:
        text = pat.sub('[已隐藏]', text)
    return text.strip()


def parse_file(path: Path):
    """解析单个 JSONL 文件，返回 (user_id, other_name, messages)。"""
    header, members, messages = None, [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            t = obj.get("_type")
            if t == "header":
                header = obj
            elif t == "member":
                members.append(obj)
            elif t == "message":
                messages.append(obj)

    # 识别用户：accountName 匹配 USER_NAMES 的就是自己
    user_id, other_id, other_name = None, None, None
    for m in members:
        if m.get("accountName") in USER_NAMES:
            user_id = m["platformId"]
        else:
            other_id = m["platformId"]
            other_name = m.get("accountName", "")

    # 如果没找到（比如自己和自己的对话），退化处理
    if user_id is None and members:
        user_id = members[0]["platformId"]

    return user_id, other_name, messages


def chunk_messages(messages: list, user_id: str, other_name: str, label: str):
    """按 30 分钟时间窗口分段，返回格式化 chunk 列表。"""
    # 只保留文字消息（type 0 和 25）
    text_msgs = [
        m for m in messages
        if m.get("type") in (0, 25) and m.get("content")
    ]

    if not text_msgs:
        return []

    chunks = []
    window = [text_msgs[0]]

    for msg in text_msgs[1:]:
        if msg["timestamp"] - window[-1]["timestamp"] > WINDOW_SECONDS:
            chunks.append(window)
            window = [msg]
        else:
            window.append(msg)
    if window:
        chunks.append(window)

    result = []
    for w in chunks:
        user_msgs   = [m for m in w if m.get("sender") == user_id]
        user_text   = "".join(m.get("content", "") for m in user_msgs)
        user_chars  = len(re.sub(r'\s', '', user_text))

        # 过滤零碎内容
        if user_chars < MIN_USER_CHARS or len(user_msgs) < MIN_USER_MSGS:
            continue

        lines = []
        for m in w:
            content = m.get("content", "").strip()
            if not content:
                continue
            content = clean_content(content, other_name, label)
            if not content:
                continue
            speaker = "我" if m.get("sender") == user_id else label
            lines.append(f"{speaker}：{content}")

        if lines:
            header_line = f"[与{label}的对话]"
            result.append(header_line + "\n" + "\n".join(lines))

    return result


def ingest_file(path: Path, label: str):
    source = f"wechat_{label}"
    user_id, other_name, messages = parse_file(path)
    chunks = chunk_messages(messages, user_id, other_name, label)

    added = 0
    for i, chunk in enumerate(chunks):
        add_chunk(source, i, chunk, label)
        added += 1
        if added % 20 == 0:
            print(f"  [{label}] {added}/{len(chunks)} chunks added...")

    print(f"  [{label}] 完成，共 {added} 个有效对话段（原始 {len(chunks)} 段）")
    return added


if __name__ == "__main__":
    print("=== 微信聊天记录入库 ===")
    if not os.path.isdir(TEXTS_DIR):
        print(f"ERROR: 目录不存在 {TEXTS_DIR}")
        print("请先在服务器上创建 /home/bots/data/texts/ 并上传 jsonl 文件")
        sys.exit(1)

    total = 0
    for fname, label in RELATIONSHIP_MAP.items():
        fpath = Path(TEXTS_DIR) / (fname + ".jsonl")
        if not fpath.exists():
            print(f"  [跳过] {fname}.jsonl 不存在")
            continue
        print(f"\n处理 {fname} → {label}")
        total += ingest_file(fpath, label)

    print(f"\n=== 完成！本次新增 {total} 条，ChromaDB 共 {col.count()} 条 ===")
