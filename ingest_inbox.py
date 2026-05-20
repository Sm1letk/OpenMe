"""
批量入库脚本：读取 data/inbox/ 里的 .md 文件，向量化后存入 ChromaDB。
用法：python3.11 ingest_inbox.py
成功入库的文件移到 data/inbox/done/
"""
try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ModuleNotFoundError:
    pass

import os, time, hashlib
from pathlib import Path
from dotenv import load_dotenv
import chromadb
from openai import OpenAI

load_dotenv()

INBOX_DIR = Path(os.environ["DATA_PATH"]) / "inbox"
DONE_DIR  = INBOX_DIR / "done"
DONE_DIR.mkdir(exist_ok=True)

_qw = OpenAI(
    api_key=os.environ["QIANWEN_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
_chroma = chromadb.PersistentClient(path=os.environ["CHROMA_PATH"])
_col = _chroma.get_or_create_collection("memories", metadata={"hnsw:space": "cosine"})


def _embed(text: str) -> list[float]:
    for attempt in range(3):
        try:
            resp = _qw.embeddings.create(model="text-embedding-v3", input=text[:2000])
            return resp.data[0].embedding
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2)


def ingest_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return False

    # 从 frontmatter 提取 url 和 title
    url = ""
    title = path.stem
    lines = text.splitlines()
    if lines[0] == "---":
        for line in lines[1:]:
            if line == "---":
                break
            if line.startswith("url:"):
                url = line[4:].strip()
            if line.startswith("title:"):
                title = line[6:].strip().strip('"')

    doc_id = hashlib.md5((url or text[:100]).encode()).hexdigest()
    if _col.get(ids=[doc_id])["ids"]:
        print(f"  跳过（已存在）：{path.name}")
        return True

    vec = _embed(text[:3000])
    _col.add(
        ids=[doc_id],
        embeddings=[vec],
        documents=[text[:3000]],
        metadatas={
            "source": "inbox",
            "url": url,
            "title": title,
            "timestamp": int(time.time()),
        },
    )
    print(f"  ✅ 入库：{title}")
    return True


def main():
    files = list(INBOX_DIR.glob("*.md"))
    if not files:
        print("inbox 为空，没有需要入库的文件")
        return

    print(f"=== 开始批量入库，共 {len(files)} 个文件 ===")
    ok = 0
    for f in files:
        try:
            if ingest_file(f):
                f.rename(DONE_DIR / f.name)
                ok += 1
        except Exception as e:
            print(f"  ❌ 失败：{f.name} — {e}")

    print(f"\n完成！成功 {ok}/{len(files)} 个，ChromaDB 共 {_col.count()} 条")


if __name__ == "__main__":
    main()
