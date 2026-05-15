__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
import os
import time
from openai import OpenAI
import chromadb
from dotenv import load_dotenv

load_dotenv()

_qw = OpenAI(
    api_key=os.environ["QIANWEN_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
_chroma = chromadb.PersistentClient(path=os.environ["CHROMA_PATH"])
_col    = _chroma.get_or_create_collection(
    "memories",
    metadata={"hnsw:space": "cosine"},
)


def _embed(text: str) -> list[float]:
    for attempt in range(3):
        try:
            resp = _qw.embeddings.create(
                model="text-embedding-v3",
                input=text[:2000],
            )
            return resp.data[0].embedding
        except Exception as e:
            if attempt == 2:
                raise
            print(f"[WARN] embed failed (attempt {attempt+1}): {e}, retrying...")
            time.sleep(2)


def retrieve(query: str, n: int = 5) -> list[str]:
    """检索与 query 最相关的历史片段，返回文本列表。仅私人 bot 调用。"""
    vec = _embed(query)
    results = _col.query(
        query_embeddings=[vec],
        n_results=n,
    )
    docs = results["documents"][0] if results["documents"] else []
    return docs
