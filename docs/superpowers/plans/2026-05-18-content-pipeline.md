# 内容流水线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为飞书 bot 增加 URL 入库、每日选题推送、草稿生成并写入飞书云文档的完整内容流水线。

**Architecture:** 新增 `content_ingest.py`（URL抓取入库）、`feishu_docs.py`（飞书云文档创建）、`suggest_topics.py`（选题生成推送）、`fetch_builders.py`（follow-builders定时拉取），并修改 `self_feishu.py` 增加 URL 检测和选题回复两个处理分支。

**Tech Stack:** Python 3.11, ChromaDB (pysqlite3), Qianwen text-embedding-v3, MiniMax-M2.7, lark_oapi, requests, Feishu docx API v1

---

## 文件变更清单

| 文件 | 操作 | 职责 |
|------|------|------|
| `content_ingest.py` | 新建 | 识别 URL 类型→抓取正文→向量化→入 ChromaDB |
| `feishu_docs.py` | 新建 | 调用飞书 docx API 创建文档并写入内容，返回链接 |
| `suggest_topics.py` | 新建 | 查近3天内容→生成10条选题→推送飞书→缓存 pending_topics.json |
| `fetch_builders.py` | 新建 | 拉 follow-builders GitHub JSON→去重→入 ChromaDB |
| `self_feishu.py` | 修改 | 顶部加 pysqlite3 workaround；on_message 加 URL 分支和数字回复分支 |
| `.env` | 修改 | 新增 `TARGET_CHAT_ID` |
| `tests/test_content_ingest.py` | 新建 | 测试 URL 类型识别和 tweet_id 提取 |

---

## Task 1: content_ingest.py — URL 抓取 + 入库

**Files:**
- Create: `/home/bots/content_ingest.py`
- Test: `/home/bots/tests/test_content_ingest.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_content_ingest.py
import sys
sys.path.insert(0, '/home/bots')
from content_ingest import detect_url_type, extract_tweet_info

def test_detect_wechat():
    assert detect_url_type("https://mp.weixin.qq.com/s/KjJf8nGXRJUiuDg5E6Uh4g") == "wechat"

def test_detect_x():
    assert detect_url_type("https://x.com/btcbears/status/2055923801519542485?s=46") == "x"

def test_detect_twitter():
    assert detect_url_type("https://twitter.com/sama/status/1234567890") == "x"

def test_detect_unknown():
    assert detect_url_type("https://github.com/foo/bar") is None

def test_extract_tweet_info():
    username, tweet_id = extract_tweet_info("https://x.com/btcbears/status/2055923801519542485?s=46")
    assert username == "btcbears"
    assert tweet_id == "2055923801519542485"
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /home/bots && python -m pytest tests/test_content_ingest.py -v
```
预期：`ModuleNotFoundError: No module named 'content_ingest'`

- [ ] **Step 3: 创建 content_ingest.py**

```python
# /home/bots/content_ingest.py
__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

import os, re, time, hashlib
import requests
import chromadb
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

CHROMA_PATH = os.environ["CHROMA_PATH"]

_qw = OpenAI(
    api_key=os.environ["QIANWEN_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
_chroma = chromadb.PersistentClient(path=CHROMA_PATH)
_col = _chroma.get_or_create_collection("memories", metadata={"hnsw:space": "cosine"})

WECHAT_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.34(0x16082222) NetType/WIFI Language/zh_CN"
)


# ── URL 类型识别 ──────────────────────────────────────────────────────────────

def detect_url_type(url: str) -> str | None:
    """返回 'wechat' | 'x' | None"""
    if "mp.weixin.qq.com" in url:
        return "wechat"
    if re.search(r'(?:x|twitter)\.com/\w+/status/\d+', url):
        return "x"
    return None


def extract_tweet_info(url: str) -> tuple[str, str]:
    """从 X URL 提取 (username, tweet_id)"""
    m = re.search(r'(?:x|twitter)\.com/([^/]+)/status/(\d+)', url)
    if not m:
        raise ValueError(f"无法解析推文 URL: {url}")
    return m.group(1), m.group(2)


# ── 嵌入 + 入库 ───────────────────────────────────────────────────────────────

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


def _store(source: str, url: str, title: str, text: str) -> bool:
    """向量化并存入 ChromaDB，返回是否为新增（去重）"""
    text = text.strip()
    if len(text) < 50:
        return False
    doc_id = hashlib.md5(url.encode()).hexdigest()
    if _col.get(ids=[doc_id])["ids"]:
        return False  # 已存在
    vec = _embed(text[:3000])
    _col.add(
        ids=[doc_id],
        embeddings=[vec],
        documents=[text[:3000]],
        metadatas={
            "source": source,
            "url": url,
            "title": title,
            "timestamp": int(time.time()),
        },
    )
    return True


# ── 微信抓取 ──────────────────────────────────────────────────────────────────

def _fetch_wechat(url: str) -> dict | None:
    """UA 伪装抓取微信公众号文章，返回 {title, text} 或 None"""
    try:
        r = requests.get(url, headers={"User-Agent": WECHAT_UA}, timeout=20)
        r.encoding = "utf-8"
        html = r.text

        import re as _re
        # 标题
        title = ""
        m = _re.search(r'<h1[^>]*class="rich_media_title"[^>]*>\s*(.*?)\s*</h1>', html, _re.S)
        if m:
            title = _re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if not title:
            m = _re.search(r'<title>(.*?)</title>', html)
            if m:
                title = m.group(1).strip()

        # 正文
        body = ""
        m = _re.search(r'id="js_content"[^>]*>(.*?)</div>', html, _re.S)
        if m:
            raw = m.group(1)
            raw = _re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=_re.S)
            raw = _re.sub(r'<style[^>]*>.*?</style>', '', raw, flags=_re.S)
            raw = _re.sub(r'<br\s*/?>', '\n', raw, flags=_re.I)
            raw = _re.sub(r'</p>', '\n', raw, flags=_re.I)
            raw = _re.sub(r'<[^>]+>', '', raw)
            raw = raw.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
            lines = [l.strip() for l in raw.split('\n') if l.strip()]
            body = '\n'.join(lines)

        if not body:
            return None
        return {"title": title or "微信文章", "text": body}
    except Exception as e:
        print(f"[ERROR] 微信抓取失败: {e}")
        return None


# ── X 抓取 ────────────────────────────────────────────────────────────────────

def _fetch_x(url: str) -> dict | None:
    """通过 fxtwitter API 抓取推文，返回 {title, text} 或 None"""
    try:
        username, tweet_id = extract_tweet_info(url)
        api_url = f"https://api.fxtwitter.com/{username}/status/{tweet_id}"
        r = requests.get(api_url, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        tweet = data.get("tweet", {})
        text = tweet.get("text", "").strip()
        author = tweet.get("author", {}).get("name", username)
        if not text:
            return None
        title = f"{author} 的推文"
        return {"title": title, "text": f"[{author}] {text}"}
    except Exception as e:
        print(f"[ERROR] X 抓取失败: {e}")
        return None


# ── 主入口 ────────────────────────────────────────────────────────────────────

def ingest_url(url: str) -> tuple[bool, str]:
    """
    入库单条 URL。
    返回 (success, title)。
    """
    url_type = detect_url_type(url)
    if url_type is None:
        return False, "不支持的链接类型"

    if url_type == "wechat":
        result = _fetch_wechat(url)
        source = "wechat_manual"
    else:
        result = _fetch_x(url)
        source = "x_manual"

    if result is None:
        return False, "抓取失败"

    is_new = _store(source, url, result["title"], result["text"])
    if not is_new:
        return True, f"{result['title']}（已入库）"
    return True, result["title"]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /home/bots && python -m pytest tests/test_content_ingest.py -v
```
预期：5 个测试全部 PASS

- [ ] **Step 5: 手动集成验证**

```bash
cd /home/bots && python3 -c "
from content_ingest import ingest_url
ok, title = ingest_url('https://mp.weixin.qq.com/s/KjJf8nGXRJUiuDg5E6Uh4g')
print('微信:', ok, title)
ok, title = ingest_url('https://x.com/btcbears/status/2055923801519542485')
print('X:', ok, title)
"
```
预期：两条都返回 `True, 文章标题`

- [ ] **Step 6: 提交**

```bash
cd /home/bots && git add content_ingest.py tests/test_content_ingest.py
git commit -m "feat: add content_ingest.py for URL fetch and ChromaDB storage"
```

---

## Task 2: self_feishu.py — 新增 /chatid 命令 + URL 入库分支

**Files:**
- Modify: `/home/bots/self_feishu.py`

- [ ] **Step 1: 在文件最顶部（第一行前）加 pysqlite3 workaround**

在 `import os, json, time, re, sqlite3` 这一行**之前**插入：

```python
__import__('pysqlite3')
import sys as _sys
_sys.modules['sqlite3'] = _sys.modules.pop('pysqlite3')
```

注意：必须是文件第 1-3 行，否则 sqlite3 已被 import 会失效。

- [ ] **Step 2: 在现有 import 区加入 content_ingest**

在 `from rag import retrieve` 后面加：

```python
from content_ingest import ingest_url, detect_url_type
```

- [ ] **Step 3: 在 on_message 函数里加 /chatid 命令和 URL 分支**

找到 `on_message` 中现有的 `/remember` 命令处理块（约第 325-330 行），在它**之后**、`ask_self` 调用**之前**插入：

```python
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
                _send_text(chat_id, f"❌ 出错了：{e}")
            return
        else:
            _send_text(chat_id, "暂不支持该链接类型（目前支持微信公众号和 X 推文）")
            return
```

- [ ] **Step 4: 重启 bot 并测试**

```bash
# 服务器上
pkill -f self_feishu.py
nohup python3.11 /home/bots/self_feishu.py > /home/bots/logs/feishu.log 2>&1 &
```

在飞书发送 `/chatid`，bot 应回复当前 chat_id。
发送微信文章链接，bot 应回复「✅ 已入库：文章标题」。

- [ ] **Step 5: 把 chat_id 写入 .env**

```bash
# 把飞书回复的 chat_id 加入 .env
echo 'TARGET_CHAT_ID=oc_xxxxxxxxxxxxxxxxxxxxxxxx' >> /home/bots/.env
```

- [ ] **Step 6: 提交**

```bash
cd /home/bots && git add self_feishu.py .env
git commit -m "feat: add URL ingestion branch and /chatid command to feishu bot"
```

---

## Task 3: feishu_docs.py — 飞书云文档创建

**Files:**
- Create: `/home/bots/feishu_docs.py`

飞书文档文件夹 token：`WFqPfQ3RElxZdEdj93lceJbknBd`
飞书租户域名：`acn0f8jrf3fc.feishu.cn`

- [ ] **Step 1: 创建 feishu_docs.py**

```python
# /home/bots/feishu_docs.py
"""
飞书云文档创建工具。
调用 Feishu docx v1 API 在指定文件夹创建文档并写入内容。
"""

import os, time, json
import requests
from dotenv import load_dotenv

load_dotenv()

APP_ID     = os.environ["SELF_FEISHU_APP_ID"]
APP_SECRET = os.environ["SELF_FEISHU_APP_SECRET"]
FEISHU_API = "https://open.feishu.cn/open-apis"
FOLDER_TOKEN = "WFqPfQ3RElxZdEdj93lceJbknBd"
FEISHU_DOMAIN = "acn0f8jrf3fc.feishu.cn"

_token_cache = {"token": "", "expires_at": 0}


def _get_token() -> str:
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
    return {"Authorization": f"Bearer {_get_token()}", "Content-Type": "application/json"}


def _create_document(title: str) -> str:
    """在指定文件夹创建空白文档，返回 document_id"""
    resp = requests.post(
        f"{FEISHU_API}/docx/v1/documents",
        headers=_headers(),
        json={"folder_token": FOLDER_TOKEN, "title": title},
        timeout=10,
    ).json()
    if resp.get("code") != 0:
        raise RuntimeError(f"创建文档失败: {resp}")
    return resp["data"]["document"]["document_id"]


def _add_content_blocks(document_id: str, content: str):
    """将正文按段落拆分，批量插入为 paragraph 块"""
    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
    if not paragraphs:
        return

    children = []
    for para in paragraphs:
        children.append({
            "block_type": 2,
            "text": {
                "elements": [{"text_run": {"content": para}}],
                "style": {}
            }
        })

    resp = requests.post(
        f"{FEISHU_API}/docx/v1/documents/{document_id}/blocks/{document_id}/children",
        headers=_headers(),
        json={"children": children, "index": 0},
        timeout=15,
    ).json()
    if resp.get("code") != 0:
        raise RuntimeError(f"写入内容失败: {resp}")


def create_doc(title: str, content: str) -> str:
    """
    在草稿文件夹创建飞书文档，写入内容，返回文档 URL。
    """
    document_id = _create_document(title)
    _add_content_blocks(document_id, content)
    return f"https://{FEISHU_DOMAIN}/docx/{document_id}"
```

- [ ] **Step 2: 手动测试文档创建**

```bash
cd /home/bots && python3.11 -c "
from feishu_docs import create_doc
url = create_doc('测试文档_删除我', '这是第一段内容。\n\n这是第二段内容。')
print('文档链接:', url)
"
```

预期：打印出飞书文档链接，打开链接能看到两段内容，文档在草稿文件夹里。

- [ ] **Step 3: 提交**

```bash
cd /home/bots && git add feishu_docs.py
git commit -m "feat: add feishu_docs.py for creating cloud documents"
```

---

## Task 4: suggest_topics.py — 选题生成 + 推送

**Files:**
- Create: `/home/bots/suggest_topics.py`

- [ ] **Step 1: 创建 suggest_topics.py**

```python
# /home/bots/suggest_topics.py
__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

import os, json, time, re
import requests
import chromadb
from openai import OpenAI
from dotenv import load_dotenv

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


def _load_memories() -> str:
    path = os.path.join(DATA_PATH, "memories.json")
    try:
        data = json.loads(open(path, encoding="utf-8").read())
        if isinstance(data, list):
            return "\n".join(item.get("content", str(item)) for item in data)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return ""


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
        max_tokens=2000,
        temperature=0.8,
    )
    raw = resp.choices[0].message.content
    raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()

    # 提取 JSON 数组
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if not m:
        raise ValueError(f"MiniMax 未返回 JSON 数组: {raw[:200]}")
    topics = json.loads(m.group(0))
    return topics


def _format_push_message(topics: list[dict]) -> str:
    """格式化推送消息"""
    lines = ["📋 今日选题建议（回复序号选择，如「3」或「3，聚焦在XX角度」）\n"]
    for t in topics:
        lines.append(
            f"{t['index']}. {t['title']}\n"
            f"   角度：{t['angle']}\n"
            f"   来源：{t['source_summary']}"
            + (f"（{t['source_url']}）" if t['source_url'] else "")
        )
    return "\n\n".join(lines)


def main():
    print("=== 选题推送开始 ===")
    docs = _get_recent_docs(days=3)
    if not docs:
        print("近3天无新入库内容，跳过推送")
        return

    print(f"找到近3天内容：{len(docs)} 条")
    memories = _load_memories()
    topics = _generate_topics(docs, memories)
    print(f"生成选题：{len(topics)} 条")

    # 缓存选题（含原始 docs 供草稿生成使用）
    pending = {
        "generated_at": int(time.time()),
        "topics": topics,
        "docs_by_index": {
            str(t["index"]): [
                d for d in docs if t.get("source_url", "") in d.get("url", "")
            ] or docs[:3]
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
```

- [ ] **Step 2: 手动测试（先确保数据库有近3天内容）**

如果数据库还没有带 timestamp 的数据，先手动入库一条测试内容：
```bash
cd /home/bots && python3.11 -c "
from content_ingest import ingest_url
ok, title = ingest_url('https://mp.weixin.qq.com/s/KjJf8nGXRJUiuDg5E6Uh4g')
print(ok, title)
"
```

然后测试选题生成：
```bash
cd /home/bots && python3.11 suggest_topics.py
```
预期：飞书收到10条选题推送，`/home/bots/data/pending_topics.json` 文件被创建。

- [ ] **Step 3: 提交**

```bash
cd /home/bots && git add suggest_topics.py
git commit -m "feat: add suggest_topics.py for daily topic generation and push"
```

---

## Task 5: self_feishu.py — 选题回复 + 草稿生成

**Files:**
- Modify: `/home/bots/self_feishu.py`

- [ ] **Step 1: 在 import 区加入 feishu_docs**

在 `from content_ingest import ingest_url, detect_url_type` 后加：

```python
from feishu_docs import create_doc
```

- [ ] **Step 2: 在文件顶部常量区加入 prompt 模板和常量**

在 `MODEL = "MiniMax-M2.7"` 后加：

```python
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
```

- [ ] **Step 3: 在 on_message 函数中加选题回复分支**

在 URL 入库分支**之前**（`if text.startswith("http")`之前）加入：

```python
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
            _send_text(chat_id, f"❌ 草稿生成失败：{e}")
        return
```

- [ ] **Step 4: 在 on_message 函数外（文件末尾前）加辅助函数**

```python
def _has_valid_pending_topics() -> bool:
    """判断是否存在有效的（24小时内）待确认选题"""
    if not os.path.exists(PENDING_TOPICS_PATH):
        return False
    try:
        data = json.load(open(PENDING_TOPICS_PATH, encoding="utf-8"))
        return time.time() - data.get("generated_at", 0) < PENDING_TTL
    except Exception:
        return False


def _generate_and_save_draft(index: int, direction: str) -> str:
    """
    根据选题序号和可选方向生成草稿，创建飞书文档，返回文档 URL。
    """
    data = json.load(open(PENDING_TOPICS_PATH, encoding="utf-8"))
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
    memories = load_memories()

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

    from datetime import datetime
    date_str = datetime.now().strftime("%Y-%m-%d")
    doc_full_title = f"{date_str}_{doc_title}"

    doc_url = create_doc(doc_full_title, content)
    return doc_url
```

- [ ] **Step 5: 重启 bot 并端到端测试**

```bash
pkill -f self_feishu.py
nohup python3.11 /home/bots/self_feishu.py > /home/bots/logs/feishu.log 2>&1 &
```

1. 先确认 `pending_topics.json` 存在（上一个 task 手动生成过）
2. 在飞书发送 `1`
3. 预期：bot 回复「正在生成草稿...」然后回复飞书文档链接
4. 打开链接，确认文档内容正确，文档在草稿文件夹

- [ ] **Step 6: 提交**

```bash
cd /home/bots && git add self_feishu.py
git commit -m "feat: add topic reply handler and draft generation to feishu bot"
```

---

## Task 6: fetch_builders.py — follow-builders 定时拉取

**Files:**
- Create: `/home/bots/fetch_builders.py`

- [ ] **Step 1: 创建 fetch_builders.py**

```python
# /home/bots/fetch_builders.py
__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

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
    r = requests.get(FEED_URL, timeout=15)
    if r.status_code != 200:
        print(f"[ERROR] 拉取失败: {r.status_code}")
        return

    data = r.json()
    print(f"Feed 生成时间: {data.get('generatedAt')}")

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
```

- [ ] **Step 2: 手动运行验证**

```bash
cd /home/bots && python3.11 fetch_builders.py
```
预期输出：
```
=== fetch_builders 开始 ===
Feed 生成时间: 2026-05-18T07:45:35.433Z
完成！新增 21 条，跳过（已存在）0 条
ChromaDB 当前共 XXXXX 条
```

- [ ] **Step 3: 再次运行验证去重**

```bash
python3.11 fetch_builders.py
```
预期：新增 0 条，全部跳过（已存在）

- [ ] **Step 4: 提交**

```bash
cd /home/bots && git add fetch_builders.py
git commit -m "feat: add fetch_builders.py for daily follow-builders ingestion"
```

---

## Task 7: Cron 配置

**Files:**
- Modify: 服务器 crontab

- [ ] **Step 1: 确认 python 路径**

```bash
which python3.11
# 预期: /usr/bin/python3.11
```

- [ ] **Step 2: 确认两个脚本都能正常运行**

```bash
cd /home/bots && python3.11 fetch_builders.py
cd /home/bots && python3.11 suggest_topics.py
```
两个都无报错。

- [ ] **Step 3: 配置 crontab**

```bash
crontab -e
```

添加以下两行：
```cron
# 每日 17:00 北京时间（UTC 09:00）拉取 follow-builders
0 9 * * * cd /home/bots && /usr/bin/python3.11 fetch_builders.py >> /home/bots/logs/fetch_builders.log 2>&1

# 每日 08:00 北京时间（UTC 00:00）推送选题
0 0 * * * cd /home/bots && /usr/bin/python3.11 suggest_topics.py >> /home/bots/logs/suggest_topics.log 2>&1
```

- [ ] **Step 4: 确认 crontab 写入**

```bash
crontab -l
```
预期：能看到上面两行。

- [ ] **Step 5: 确认日志目录存在**

```bash
mkdir -p /home/bots/logs
ls /home/bots/logs/
```

- [ ] **Step 6: 提交最终状态**

```bash
cd /home/bots && git add -A
git commit -m "chore: configure cron jobs for content pipeline"
```

---

## 验收标准

1. 飞书发微信文章链接 → bot 回复「✅ 已入库：标题」
2. 飞书发 X 推文链接 → bot 回复「✅ 已入库：xxx 的推文」
3. 每天 17:00 后 `fetch_builders.log` 有新增记录
4. 每天 08:00 飞书收到10条选题推送
5. 回复 `3` → 5分钟内收到飞书云文档链接，打开有完整草稿
6. 回复 `3，聚焦在AI教育角度` → 草稿切入点按修改方向生成
