__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
import os
import chromadb
from dotenv import load_dotenv
from embedding import embed

load_dotenv()

_chroma = chromadb.PersistentClient(path=os.environ["CHROMA_PATH"])
_col    = _chroma.get_or_create_collection(
    "memories",
    metadata={"hnsw:space": "cosine"},
)


def retrieve(query: str, n: int = 5, where: dict | None = None) -> list[str]:
    """检索与 query 最相关的历史片段，返回文本列表。
    where: ChromaDB 元数据过滤条件，如 {"source": "wechat_article"}
    """
    vec = embed(query)
    kwargs = {"query_embeddings": [vec], "n_results": n}
    if where:
        kwargs["where"] = where
    results = _col.query(**kwargs)
    docs = results["documents"][0] if results["documents"] else []
    return docs
