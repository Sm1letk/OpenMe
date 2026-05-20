try:
    __import__('pysqlite3')
    import sys
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ModuleNotFoundError:
    pass

import os, re, hashlib, time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from embedding import embed

load_dotenv()

# ── 懒加载客户端（测试时不会触发连接） ────────────────────────────────────

_chroma = None
_col = None


def _get_col():
    global _chroma, _col
    if _col is None:
        import chromadb
        CHROMA_PATH = os.environ["CHROMA_PATH"]
        _chroma = chromadb.PersistentClient(path=CHROMA_PATH)
        _col = _chroma.get_or_create_collection(
            "memories",
            metadata={"hnsw:space": "cosine"},
        )
    return _col


# ── URL 类型识别 ──────────────────────────────────────────────────────────

def detect_url_type(url: str) -> str | None:
    """
    返回 URL 类型：
    - "wechat"  if URL contains mp.weixin.qq.com
    - "x"       if URL matches x.com or twitter.com status pattern
    - None      otherwise
    """
    if "mp.weixin.qq.com" in url:
        return "wechat"
    if re.search(r'(?:x|twitter)\.com/\w+/status/\d+', url):
        return "x"
    return None


def extract_tweet_info(url: str) -> tuple[str, str]:
    """
    从 X/Twitter URL 中提取 (username, tweet_id)。
    例：https://x.com/btcbears/status/2055923801519542485?s=46
      → ("btcbears", "2055923801519542485")
    """
    m = re.search(r'(?:x|twitter)\.com/(\w+)/status/(\d+)', url)
    if not m:
        raise ValueError(f"无法从 URL 提取 tweet 信息: {url}")
    return m.group(1), m.group(2)


# ── 去重 & 入库 ───────────────────────────────────────────────────────────

def _url_doc_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _already_ingested(doc_id: str) -> bool:
    col = _get_col()
    existing = col.get(ids=[doc_id])
    return bool(existing["ids"])


def _store(doc_id: str, text: str, title: str, url: str, source: str) -> None:
    col = _get_col()
    vec = embed(text)
    col.add(
        ids=[doc_id],
        embeddings=[vec],
        documents=[text],
        metadatas=[{
            "source": source,
            "url": url,
            "title": title,
            "timestamp": int(time.time()),
        }],
    )
    print(f"  [ingest] 已入库: {title!r} ({len(text)} chars)")


# ── 微信文章抓取 ──────────────────────────────────────────────────────────

WECHAT_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Mobile Safari/537.36 "
    "MicroMessenger/8.0.50.2701(0x2800323A) "
    "NetType/WIFI Language/zh_CN"
)


def _fetch_wechat(url: str) -> tuple[str, str]:
    """
    抓取微信公众号文章，返回 (title, body_text)。
    使用微信内置浏览器 UA 伪装。
    """
    headers = {"User-Agent": WECHAT_UA}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # 标题
    title_tag = soup.find("h1", id="activity-name") or soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "微信文章"

    # 正文
    content_div = (
        soup.find("div", id="js_content")
        or soup.find("div", class_=re.compile(r"rich_media_content"))
        or soup.find("div", id="content")
    )
    if content_div:
        body = content_div.get_text(separator="\n", strip=True)
    else:
        body = soup.get_text(separator="\n", strip=True)

    return title, body


# ── X / Twitter 抓取（fxtwitter API） ────────────────────────────────────

def _fetch_tweet(url: str) -> tuple[str, str]:
    """
    通过 fxtwitter API 抓取推文，返回 (title, body_text)。
    若推文正文为空（纯链接转发），自动跟进链接用 jina 抓目标页内容。
    """
    username, tweet_id = extract_tweet_info(url)
    api_url = f"https://api.fxtwitter.com/{username}/status/{tweet_id}"
    resp = requests.get(api_url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    tweet = data.get("tweet") or {}
    text = tweet.get("text", "").strip()
    author = tweet.get("author", {}).get("name") or username

    if text:
        title = f"@{username}: {text[:60]}{'…' if len(text) > 60 else ''}"
        body = f"@{author}\n{text}"
        return title, body

    # 推文正文为空（X Article 或纯链接转发），直接用 jina 抓原始推文 URL
    try:
        title, body = _fetch_general(url)
        return f"@{username} 分享：{title}", body
    except Exception:
        pass

    raise ValueError("推文正文为空（纯图片/视频/已删除），请用 /save 手动存入文字内容")


# ── 通用抓取（r.jina.ai 兜底） ────────────────────────────────────────────

_LOGIN_WALL_SIGNALS = [
    "Don't miss what's happening",
    "Sign in to",
    "Access Denied",
    "404 Not Found",
    "请登录",
    "登录后查看",
]


def _is_login_wall(text: str) -> bool:
    first_500 = text[:500]
    return any(s in first_500 for s in _LOGIN_WALL_SIGNALS) or len(text.splitlines()) < 5


def _fetch_general(url: str) -> tuple[str, str]:
    """
    级联抓取：jina → defuddle.md，返回 (title, body_text)。
    支持小红书、知乎、Medium 等需要 JS 渲染的页面。
    """
    for proxy_url in [f"https://r.jina.ai/{url}", f"https://defuddle.md/{url}"]:
        try:
            resp = requests.get(
                proxy_url,
                headers={"Accept": "text/markdown"},
                timeout=30,
            )
            if resp.status_code != 200:
                continue
            text = resp.text.strip()
            if _is_login_wall(text):
                continue

            # 提取标题（第一个 # 行，或前 60 字）
            title = ""
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("#"):
                    title = line.lstrip("#").strip()
                    break
            if not title:
                title = text[:60].replace("\n", " ")

            return title, text
        except Exception:
            continue

    raise ValueError("两个代理均无法抓取（可能是登录墙），请复制内容后用 /save 存入")


# ── 主入口 ────────────────────────────────────────────────────────────────

def ingest_url(url: str) -> tuple[bool, str]:
    """
    识别 URL 类型 → 抓取正文 → 向量化 → 入库 ChromaDB。
    返回 (success, title)。
    已入库则跳过并返回 (True, "标题（已入库）")。
    """
    url_type = detect_url_type(url)

    doc_id = _url_doc_id(url)

    # 去重检查
    if _already_ingested(doc_id):
        col = _get_col()
        existing = col.get(ids=[doc_id], include=["metadatas"])
        title = ""
        if existing["metadatas"]:
            title = existing["metadatas"][0].get("title", "")
        if not title:
            title = url
        return True, f"{title}（已入库）"

    try:
        if url_type == "wechat":
            title, body = _fetch_wechat(url)
            source = "wechat_manual"
        elif url_type == "x":
            title, body = _fetch_tweet(url)
            source = "x_manual"
        else:
            # 兜底：r.jina.ai 通用抓取（小红书、知乎、Medium 等）
            title, body = _fetch_general(url)
            source = "general_manual"
    except Exception as e:
        return False, f"抓取失败: {e}"

    try:
        _store(doc_id, body, title, url, source)
    except Exception as e:
        return False, f"入库失败: {e}"

    return True, title
