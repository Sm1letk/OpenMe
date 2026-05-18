# /home/bots/fetch_builders.py
"""
每日定时拉取 follow-builders GitHub JSON，去重后存入 ChromaDB。
feed URL: https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json
"""
try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ModuleNotFoundError:
    pass

import os, time, hashlib
import requests
import chromadb
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

FEED_URL    = "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json"
CHROMA_PATH = os.environ["CHROMA_PATH"]

_qw = OpenAI(
    api_key=os.environ["QIANWEN_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
_chroma = chromadb.PersistentClient(path=CHROMA_PATH)
_col = _chroma.get_or_create_collection("memories", metadata={"hnsw:space": "cosine"})


def _embed(text: str) -> list[float]:
    for attempt in range(3):
        try:
            resp = _qw.embeddings.create(model="text-embedding-v3", input=text[:2000])
            return resp.data[0].embedding
        except Exception as e:
            if attempt == 2:
                raise
            print(f"[WARN] embed failed (attempt {attempt+1}): {e}")
            time.sleep(2)


def _store_tweet(tweet: dict, author_name: str, author_handle: str) -> bool:
    """存储单条推文，返回是否为新增"""
    tweet_id = tweet.get("id", "")
    text = tweet.get("text", "").strip()
    if not text or not tweet_id:
        return False

    doc_id = hashlib.md5(f"follow_builders:{tweet_id}".encode()).hexdigest()
    if _col.get(ids=[doc_id])["ids"]:
        return False  # 已存在

    tweet_url = tweet.get("url", f"https://x.com/{author_handle}/status/{tweet_id}")
    full_text = f"[{author_name}] {text}"

    vec = _embed(full_text)
    _col.add(
        ids=[doc_id],
        embeddings=[vec],
        documents=[full_text],
        metadatas={
            "source": "follow_builders",
            "url": tweet_url,
            "title": f"{author_name} 的推文",
            "author": author_name,
            "handle": author_handle,
            "timestamp": int(time.time()),
        },
    )
    return True


def main():
    print("=== fetch_builders 开始 ===")
    try:
        r = requests.get(FEED_URL, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[ERROR] 拉取 feed 失败: {e}")
        return

    data = r.json()
    print(f"Feed 生成时间: {data.get('generatedAt', '未知')}")

    added = 0
    skipped = 0
    for builder in data.get("x", []):
        name = builder.get("name", "")
        handle = builder.get("handle", "")
        for tweet in builder.get("tweets", []):
            if _store_tweet(tweet, name, handle):
                added += 1
            else:
                skipped += 1

    print(f"完成！新增 {added} 条，跳过（已存在）{skipped} 条")
    print(f"ChromaDB 当前共 {_col.count()} 条")


if __name__ == "__main__":
    main()
