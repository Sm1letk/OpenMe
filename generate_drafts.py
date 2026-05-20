# /home/bots/generate_drafts.py
__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

import os
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# ── 环境变量验证 ──────────────────────────────────────────────────────────────

_REQUIRED_ENVS = ["MINIMAX_API_KEY", "QIANWEN_API_KEY", "CHROMA_PATH", "DATA_PATH"]
_missing = [k for k in _REQUIRED_ENVS if not os.environ.get(k)]
if _missing:
    raise EnvironmentError(f"缺少必要环境变量: {', '.join(_missing)}")

# ── 客户端初始化 ──────────────────────────────────────────────────────────────

_minimax = OpenAI(
    api_key=os.environ["MINIMAX_API_KEY"],
    base_url="https://api.minimaxi.com/v1",
)

_chroma = chromadb.PersistentClient(path=os.environ["CHROMA_PATH"])
_col = _chroma.get_or_create_collection(
    "memories",
    metadata={"hnsw:space": "cosine"},
)

DATA_PATH = os.environ["DATA_PATH"]
DRAFTS_DIR = Path(DATA_PATH) / "drafts"
DRAFTS_DIR.mkdir(exist_ok=True)

MODEL = "MiniMax-M2.7"

# ── 加载用户身份 ──────────────────────────────────────────────────────────────

def load_memories() -> str:
    """加载 memories.json，返回纯文本供 prompt 使用。"""
    path = Path(DATA_PATH) / "memories.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return "\n".join(item.get("content", str(item)) for item in data)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] 加载 memories.json 失败: {e}")
        return ""


# ── ChromaDB 主题采样 ──────────────────────────────────────────────────────────

_qw = OpenAI(
    api_key=os.environ["QIANWEN_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

THEMES = [
    "职场判断与决策",
    "产品思维与用户洞察",
    "个人成长与反思",
    "技术与工具使用",
    "创业与副业",
    "人际关系与沟通",
    "学习方法与知识积累",
    "生活方式与习惯",
    "价值观与人生选择",
    "行业趋势与机会判断",
]


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
            print(f"[WARN] embed 失败 (attempt {attempt+1}): {e}, 重试中...")
            time.sleep(2)


def sample_by_themes(n_per_theme: int = 4) -> dict[str, list[str]]:
    """对每个主题做语义检索，优先取 flomo 来源，不足时补 wechat，
    排除 gemini/claude（以 AI 回答为主，非用户原创思考）。
    返回 {主题: [片段, ...]} 字典。
    """
    theme_docs: dict[str, list[str]] = {}
    seen: set[str] = set()

    # 优先级：flomo > wechat > 其他（排除 gemini/claude）
    SOURCE_PRIORITY = [
        {"source": "flomo"},
        {"source": {"$nin": ["gemini", "claude"]}},  # wechat 等
    ]

    for theme in THEMES:
        vec = _embed(theme)
        unique_docs: list[str] = []

        for where_filter in SOURCE_PRIORITY:
            if len(unique_docs) >= n_per_theme:
                break
            needed = n_per_theme - len(unique_docs)
            try:
                results = _col.query(
                    query_embeddings=[vec],
                    n_results=needed + 5,  # 多取几条备去重
                    where=where_filter,
                )
                docs = results["documents"][0] if results["documents"] else []
                for doc in docs:
                    key = doc[:100]
                    if key not in seen and len(unique_docs) < n_per_theme:
                        seen.add(key)
                        unique_docs.append(doc)
            except Exception:
                pass  # 某些过滤条件在小集合上可能报错，跳过

        if unique_docs:
            theme_docs[theme] = unique_docs
        print(f"  主题「{theme}」: 采样 {len(unique_docs)} 条")

    return theme_docs


# ── 草稿生成 ──────────────────────────────────────────────────────────────────

LONG_PROMPT_TEMPLATE = """你是一个正在写作的知识工作者，根据下面的个人背景和素材，写一篇深度文章。

## 我的个人背景
{memories}

## 本篇素材（来自我的真实经历和思考）
{materials}

## 写作要求
- 主题聚焦：围绕「{theme}」展开，不要面面俱到
- 字数：800-1200字
- 风格：有观点，有例子，有判断，不说废话
- 结构：开头一句话抓住核心观点，正文展开，结尾一句话收尾
- 语气：第一人称，像在和朋友分享，不用"首先/其次/最后"等套路词
- 不要写"作为一个XX"这类开头
- 【脱敏要求】不得出现任何真实公司名、产品名、客户名；用通用描述替代，例如"某制造业客户"→"客户"，"某公司"→"公司"，具体项目名一律省去
- 【语言要求】必须全程中文，不得出现英文
- 【格式要求】直接输出最终内容，不要输出任何分析、思考过程或写作计划
- 直接输出文章内容，第一行是标题（不加"标题："前缀）

现在开始写："""


def generate_long_draft(theme: str, docs: list[str], memories: str) -> str:
    """生成长文草稿，返回完整 markdown 文本（含标题）。"""
    materials = "\n\n---\n\n".join(docs[:5])
    prompt = LONG_PROMPT_TEMPLATE.format(
        memories=memories[:1500],
        materials=materials[:3000],
        theme=theme,
    )
    print(f"  正在生成长文：{theme}...")
    resp = _minimax.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
        temperature=0.8,
    )
    content = resp.choices[0].message.content
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    return content


SHORT_PROMPT_TEMPLATE = """根据下面的个人背景和素材，写一条适合即刻/小红书的短内容。

## 我的个人背景
{memories}

## 素材（来自我的真实思考）
{materials}

## 写作要求
- 主题：「{theme}」
- 字数：150-300字
- 风格：口语化，有个人观点，像朋友圈分享
- 开头一句话要有冲击力，让人想继续读
- 可以有1-3个要点，但不要用数字编号
- 结尾可以有一个反问或开放性的问题
- 不加标签（#），第一行直接是正文
- 【脱敏要求】不得出现任何真实公司名、产品名、客户名，用通用描述替代
- 【语言要求】必须全程中文，不得出现英文
- 【格式要求】直接输出最终内容，不要输出任何分析、思考过程或写作计划
- 直接输出内容，不加任何前缀说明

现在写："""


def generate_short_draft(theme: str, docs: list[str], memories: str) -> str:
    """生成短文草稿，返回正文文本（无标题）。"""
    materials = "\n\n".join(docs[:3])
    prompt = SHORT_PROMPT_TEMPLATE.format(
        memories=memories[:800],
        materials=materials[:1500],
        theme=theme,
    )
    print(f"  正在生成短文：{theme}...")
    resp = _minimax.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
        temperature=0.9,
    )
    content = resp.choices[0].message.content
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    return content


# ── 文件保存 ──────────────────────────────────────────────────────────────────

def save_draft(
    content: str,
    draft_type: str,
    theme: str,
    index: int,
    platform: str,
) -> Path:
    """保存草稿为 markdown 文件，返回文件路径。"""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%Y-%m-%d %H:%M")

    lines = content.strip().split('\n')
    if draft_type == "long":
        title = lines[0].lstrip('#').strip() or theme
        body = '\n'.join(lines[1:]).strip()
    else:
        title = theme
        body = content.strip()

    safe_theme = re.sub(r'[^\w一-鿿]', '_', theme)[:20]
    filename = f"{date_str}_{draft_type}_{index:02d}_{safe_theme}.md"
    filepath = DRAFTS_DIR / filename

    metadata = f"""---
类型: {"长文" if draft_type == "long" else "短文"}
适合平台: {platform}
生成时间: {time_str}
主题关键词: {theme}
发布状态: 待审阅
---

"""
    if draft_type == "long":
        full_content = metadata + f"# {title}\n\n{body}"
    else:
        full_content = metadata + body

    filepath.write_text(full_content, encoding="utf-8")
    print(f"  已保存: {filename}")
    return filepath


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("OpenMe 内容草稿生成器")
    print("=" * 50)

    print("\n[1/3] 加载用户记忆...")
    memories = load_memories()
    print(f"  记忆库加载完成（{len(memories)} 字符）")

    print("\n[2/3] 从 ChromaDB 采样主题内容...")
    theme_docs = sample_by_themes(n_per_theme=4)
    themes = list(theme_docs.keys())

    if len(themes) < 10:
        print(f"[WARN] 仅 {len(themes)} 个主题有素材，将重复主题补足 10 篇（部分草稿内容可能相近）")
        themes = (themes * 3)[:10]

    long_themes = themes[:5]
    short_themes = themes[5:10]

    print("\n[3/3] 开始生成草稿...")
    saved_files = []

    print("\n--- 长文（5篇）---")
    for i, theme in enumerate(long_themes, start=1):
        docs = theme_docs.get(theme, [])
        if not docs:
            print(f"  跳过「{theme}」：无素材")
            continue
        try:
            content = generate_long_draft(theme, docs, memories)
            path = save_draft(content, "long", theme, i, "公众号 / 知乎")
            saved_files.append(path)
        except Exception as e:
            print(f"  [ERROR] 长文「{theme}」生成失败: {e}")

    print("\n--- 短文（5篇）---")
    for i, theme in enumerate(short_themes, start=1):
        docs = theme_docs.get(theme, [])
        if not docs:
            print(f"  跳过「{theme}」：无素材")
            continue
        try:
            content = generate_short_draft(theme, docs, memories)
            path = save_draft(content, "short", theme, i, "即刻 / 小红书")
            saved_files.append(path)
        except Exception as e:
            print(f"  [ERROR] 短文「{theme}」生成失败: {e}")

    print("\n" + "=" * 50)
    print(f"✅ 生成完成！共 {len(saved_files)} 篇草稿")
    print(f"📁 保存目录: {DRAFTS_DIR}")
    print("\n草稿列表:")
    for f in saved_files:
        print(f"  - {f.name}")
    print("\n下一步：审阅草稿，记录发布率，填入 WAO 追踪表。")


if __name__ == "__main__":
    main()
