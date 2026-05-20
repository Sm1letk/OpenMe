# generate_drafts.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 ChromaDB 采样内容，结合 memories.json 用户身份，用 MiniMax-M2.7 生成 10 篇内容草稿（5 长文 + 5 短文），保存至 /home/bots/data/drafts/ 供用户审阅发布。

**Architecture:** 独立脚本，不依赖 self_feishu.py 的对话流程。直接连 ChromaDB 做多主题采样，每个主题单独调用 MiniMax 生成草稿，结果写成带元数据的 Markdown 文件。两类草稿（长文/短文）用不同 prompt 模板，各生成 5 篇。

**Tech Stack:** Python 3.11, chromadb, pysqlite3-binary (workaround), openai SDK (MiniMax 兼容接口), python-dotenv

---

## 文件结构

```
/home/bots/
├── generate_drafts.py        # 新建：主脚本
└── data/
    └── drafts/               # 新建目录：输出草稿
        ├── 2026-05-15_long_01_职场判断力.md
        ├── 2026-05-15_short_01_产品思考.md
        └── ...（共 10 篇）
```

每篇草稿文件格式：
```markdown
---
类型: 长文
适合平台: 公众号 / 知乎
生成时间: 2026-05-15 14:30
主题关键词: 职场, 判断力, 产品
---

# 标题

正文内容...
```

---

## Task 1: pysqlite3 workaround + 环境初始化

**Files:**
- Create: `/home/bots/generate_drafts.py`（前 40 行）

- [ ] **Step 1: 创建文件，写 pysqlite3 workaround 和环境加载**

```python
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
```

- [ ] **Step 2: 验证环境连通**

在服务器上运行（用 python3.11）：
```bash
cd /home/bots
python3.11 -c "
__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
import chromadb, os
from dotenv import load_dotenv
load_dotenv()
c = chromadb.PersistentClient(path=os.environ['CHROMA_PATH'])
col = c.get_or_create_collection('memories')
print('总条数:', col.count())
"
```

Expected: `总条数: 13768`（或接近的数字）

---

## Task 2: memories.json 加载函数

**Files:**
- Modify: `/home/bots/generate_drafts.py`（追加）

- [ ] **Step 1: 写 load_memories()**

```python
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
```

- [ ] **Step 2: 验证加载**

```bash
python3.11 -c "
__import__('pysqlite3')
import sys; sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
import os; from dotenv import load_dotenv; load_dotenv()
from pathlib import Path
import json
path = Path(os.environ['DATA_PATH']) / 'memories.json'
data = json.loads(path.read_text(encoding='utf-8'))
print(type(data), str(data)[:200])
"
```

Expected: 打印出 memories.json 的结构和前 200 字符

---

## Task 3: ChromaDB 多主题采样函数

**Files:**
- Modify: `/home/bots/generate_drafts.py`（追加）

- [ ] **Step 1: 写 sample_by_themes()**

策略：用 10 个预设主题关键词做语义检索，每个主题取 Top-3 片段，合并去重后得到内容素材池。

```python
# ── ChromaDB 主题采样 ──────────────────────────────────────────────────────────

from openai import OpenAI as _QW

_qw = _QW(
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


def sample_by_themes(n_per_theme: int = 3) -> dict[str, list[str]]:
    """
    对每个主题做语义检索，返回 {主题: [片段, ...]} 字典。
    """
    theme_docs: dict[str, list[str]] = {}
    seen: set[str] = set()

    for theme in THEMES:
        vec = _embed(theme)
        results = _col.query(query_embeddings=[vec], n_results=n_per_theme)
        docs = results["documents"][0] if results["documents"] else []
        unique_docs = []
        for doc in docs:
            key = doc[:100]  # 用前100字符做去重key
            if key not in seen:
                seen.add(key)
                unique_docs.append(doc)
        if unique_docs:
            theme_docs[theme] = unique_docs
        print(f"  主题「{theme}」: 采样 {len(unique_docs)} 条")

    return theme_docs
```

- [ ] **Step 2: 验证采样**

```bash
python3.11 -c "
__import__('pysqlite3')
import sys; sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
exec(open('/home/bots/generate_drafts.py').read())
theme_docs = sample_by_themes(n_per_theme=2)
for theme, docs in list(theme_docs.items())[:3]:
    print(f'\n== {theme} ==')
    print(docs[0][:150])
"
```

Expected: 打印出 3 个主题各 1 条片段预览，无报错

---

## Task 4: 长文生成函数（公众号/知乎，800-1200字）

**Files:**
- Modify: `/home/bots/generate_drafts.py`（追加）

- [ ] **Step 1: 写 generate_long_draft()**

```python
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
- 直接输出文章内容，第一行是标题（不加"标题："前缀）

现在开始写："""


def generate_long_draft(theme: str, docs: list[str], memories: str) -> str:
    """生成长文草稿，返回完整 markdown 文本（含标题）。"""
    materials = "\n\n---\n\n".join(docs[:5])  # 最多用5条素材
    prompt = LONG_PROMPT_TEMPLATE.format(
        memories=memories[:1500],  # 控制token
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
    # 过滤 <think> 标签
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    return content
```

- [ ] **Step 2: 单篇测试**

```bash
python3.11 -c "
__import__('pysqlite3')
import sys; sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
exec(open('/home/bots/generate_drafts.py').read())
memories = load_memories()
theme_docs = sample_by_themes(n_per_theme=3)
theme = list(theme_docs.keys())[0]
docs = theme_docs[theme]
result = generate_long_draft(theme, docs, memories)
print(result[:500])
"
```

Expected: 打印出一篇文章的前 500 字，有标题，无 `<think>` 标签，中文

---

## Task 5: 短文生成函数（即刻/小红书，150-300字）

**Files:**
- Modify: `/home/bots/generate_drafts.py`（追加）

- [ ] **Step 1: 写 generate_short_draft()**

```python
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
- 直接输出内容，不加任何前缀说明

现在写："""


def generate_short_draft(theme: str, docs: list[str], memories: str) -> str:
    """生成短文草稿，返回正文文本（无标题）。"""
    materials = "\n\n".join(docs[:3])  # 短文用3条素材足够
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
```

- [ ] **Step 2: 单篇测试**

```bash
python3.11 -c "
__import__('pysqlite3')
import sys; sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
exec(open('/home/bots/generate_drafts.py').read())
memories = load_memories()
theme_docs = sample_by_themes(n_per_theme=3)
theme = list(theme_docs.keys())[2]
docs = theme_docs[theme]
result = generate_short_draft(theme, docs, memories)
print(result)
"
```

Expected: 150-300字的短文，无标题，口语化风格

---

## Task 6: 保存草稿到文件

**Files:**
- Modify: `/home/bots/generate_drafts.py`（追加）

- [ ] **Step 1: 写 save_draft()**

```python
# ── 文件保存 ──────────────────────────────────────────────────────────────────

def save_draft(
    content: str,
    draft_type: str,   # "long" or "short"
    theme: str,
    index: int,
    platform: str,
) -> Path:
    """保存草稿为 markdown 文件，返回文件路径。"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 从内容第一行提取标题（长文），短文用主题作标题
    lines = content.strip().split('\n')
    if draft_type == "long":
        title = lines[0].lstrip('#').strip()
        body = '\n'.join(lines[1:]).strip()
    else:
        title = theme
        body = content.strip()

    # 清理文件名中的特殊字符
    safe_theme = re.sub(r'[^\w一-鿿]', '_', theme)[:20]
    filename = f"{date_str}_{draft_type}_{index:02d}_{safe_theme}.md"
    filepath = DRAFTS_DIR / filename

    # 写入带元数据的 markdown
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
```

- [ ] **Step 2: 验证文件写入**

```bash
python3.11 -c "
__import__('pysqlite3')
import sys; sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
exec(open('/home/bots/generate_drafts.py').read())
p = save_draft('# 测试标题\n\n测试内容正文', 'long', '测试主题', 1, '公众号 / 知乎')
print('文件路径:', p)
print('内容:', p.read_text()[:200])
"
```

Expected: 打印文件路径，读取内容能看到 YAML 元数据 + 标题 + 正文

---

## Task 7: 主函数 main() — 生成全部 10 篇

**Files:**
- Modify: `/home/bots/generate_drafts.py`（追加）

- [ ] **Step 1: 写 main()**

```python
# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("OpenMe 内容草稿生成器")
    print("=" * 50)

    # 1. 加载用户身份
    print("\n[1/3] 加载用户记忆...")
    memories = load_memories()
    print(f"  记忆库加载完成（{len(memories)} 字符）")

    # 2. 采样主题素材
    print("\n[2/3] 从 ChromaDB 采样主题内容...")
    theme_docs = sample_by_themes(n_per_theme=4)
    themes = list(theme_docs.keys())

    # 确保有足够主题（取前10个，不足则重复）
    if len(themes) < 10:
        themes = (themes * 3)[:10]

    long_themes = themes[:5]    # 前5个主题出长文
    short_themes = themes[5:10] # 后5个主题出短文

    # 3. 生成草稿
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

    # 4. 汇总
    print("\n" + "=" * 50)
    print(f"✅ 生成完成！共 {len(saved_files)} 篇草稿")
    print(f"📁 保存目录: {DRAFTS_DIR}")
    print("\n草稿列表:")
    for f in saved_files:
        print(f"  - {f.name}")
    print("\n下一步：审阅草稿，记录发布率，填入 WAO 追踪表。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 完整运行（干跑，只看日志）**

先在本地验证脚本语法无误：
```bash
python3.11 -m py_compile /home/bots/generate_drafts.py && echo "语法OK"
```

Expected: `语法OK`

---

## Task 8: 上传到服务器并完整运行

**Files:**
- 无新文件，执行部署

- [ ] **Step 1: 上传脚本到服务器**

```bash
scp /home/bots/generate_drafts.py root@47.108.56.88:/home/bots/generate_drafts.py
```

或在服务器上直接用 nano/vim 创建文件（如果本地已测试完成）。

- [ ] **Step 2: 在服务器上完整运行**

```bash
ssh root@47.108.56.88
cd /home/bots
python3.11 generate_drafts.py
```

Expected 输出（约 5-10 分钟，取决于 MiniMax API 速度）：
```
==================================================
OpenMe 内容草稿生成器
==================================================

[1/3] 加载用户记忆...
  记忆库加载完成（XXXX 字符）

[2/3] 从 ChromaDB 采样主题内容...
  主题「职场判断与决策」: 采样 3 条
  主题「产品思维与用户洞察」: 采样 3 条
  ...

[3/3] 开始生成草稿...

--- 长文（5篇）---
  正在生成长文：职场判断与决策...
  已保存: 2026-05-15_long_01_职场判断与决策.md
  ...

--- 短文（5篇）---
  正在生成短文：...
  ...

==================================================
✅ 生成完成！共 10 篇草稿
```

- [ ] **Step 3: 查看生成结果**

```bash
ls -la /home/bots/data/drafts/
cat /home/bots/data/drafts/2026-05-15_long_01_*.md | head -30
```

Expected: 列出 10 个文件，cat 能看到 YAML header + 标题 + 正文

- [ ] **Step 4: 把草稿 scp 到本地审阅**

```bash
scp -r root@47.108.56.88:/home/bots/data/drafts/ ~/Desktop/openme_drafts/
```

- [ ] **Step 5: commit**

```bash
git add generate_drafts.py
git commit -m "feat: add generate_drafts.py for content quality validation experiment"
```

---

## Task 9: 审阅草稿并记录结果（实验环节）

**Files:**
- 无代码，人工操作

- [ ] **Step 1: 逐篇审阅 10 篇草稿**

对每篇草稿评估：
- A：可直接发布（无需修改）
- B：小改后可发布（调整 1-2 句）
- C：大改（结构/内容需要重写）
- D：不发布（质量太差）

- [ ] **Step 2: 记录发布率**

| 草稿 | 类型 | 主题 | 评级 | 实际发布 |
|------|------|------|------|---------|
| long_01 | 长文 | 职场判断 | A/B/C/D | Y/N |
| ... | | | | |

**成功标准**：≥5 篇评级 A 或 B（WAO 核心假设验证通过）

- [ ] **Step 3: 如果通过（≥5篇 A/B）**

→ 继续 Framework 2（人格系统四文件升级）

**如果未通过（< 5篇 A/B）**：

常见原因及调整方向：
- 内容太通用 → 增加 memories.json 中的个人风格描述
- 结构太模板化 → 调整 LONG_PROMPT_TEMPLATE，减少结构约束
- 与自己风格不符 → 在 prompt 里加入真实的个人写作样例

---

## 自检结果

**Spec coverage ✅**
- 10 篇草稿（5 长 + 5 短）✅
- ChromaDB 采样 ✅
- memories.json 结合 ✅
- MiniMax-M2.7 生成 ✅
- /home/bots/data/drafts/ 保存 ✅
- Markdown + 元数据格式 ✅
- pysqlite3 workaround ✅
- 审阅记录（实验环节）✅

**Placeholder scan ✅** 所有步骤均有完整代码

**Type consistency ✅** `theme_docs: dict[str, list[str]]`、`save_draft() -> Path` 全程一致
