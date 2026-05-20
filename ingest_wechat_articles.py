"""
微信公众号文章批量入库脚本
读取 wechat-article-exporter 导出的 JSON 文件，逐篇抓取正文，向量化后存入 ChromaDB。

用法：
  python3 ingest_wechat_articles.py /path/to/微信公众号文章.json

JSON 格式（wechat-article-exporter 导出）：
  [{"title": "...", "link": "https://mp.weixin.qq.com/...", "digest": "...", ...}, ...]
"""
try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ModuleNotFoundError:
    pass

import json
import sys
import time
from pathlib import Path

from content_ingest import _store, _url_doc_id, _already_ingested, embed


def ingest_articles(json_path: str):
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print("错误：JSON 文件格式不对，需要是文章列表")
        return

    total = len(data)
    ok = skipped = failed = 0

    print(f"=== 开始入库，共 {total} 篇文章 ===\n")

    for i, article in enumerate(data, 1):
        title = article.get("title", "").strip()
        link = article.get("link", "").strip()
        digest = article.get("digest", "").strip()
        account = article.get("_accountName", "")

        if not link:
            print(f"[{i}/{total}] 跳过（无链接）：{title[:40]}")
            skipped += 1
            continue

        print(f"[{i}/{total}] {title[:50]}…")

        doc_id = _url_doc_id(link)
        if _already_ingested(doc_id):
            print(f"  ↳ 跳过（已入库）")
            skipped += 1
            continue

        # 直接用 title + digest 入库（服务器 IP 被微信反爬拦截，不抓全文）
        body = f"来源：{account}\n标题：{title}\n\n{digest}" if account else f"标题：{title}\n\n{digest}"
        if not digest:
            body = f"来源：{account}\n标题：{title}" if account else title

        try:
            _store(doc_id, body, title, link, "wechat_article")
            print(f"  ↳ ✅ 入库（{account}）：{title[:50]}")
            ok += 1
        except Exception as e:
            print(f"  ↳ ❌ 入库失败: {e}")
            failed += 1

        # 避免过快请求
        time.sleep(0.5)

    print(f"\n=== 完成！成功 {ok}，跳过 {skipped}，失败 {failed}，共 {total} 篇 ===")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 ingest_wechat_articles.py /path/to/微信公众号文章.json")
        sys.exit(1)
    ingest_articles(sys.argv[1])
