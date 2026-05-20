"""
who_am_i.py — 认知指纹分析

从 ChromaDB 随机采样，让 LLM 回答：
"从这个人存的东西里，你能看出他是什么人？他在关心什么？"

输出不是传记事实，而是认知模式：
- 反复出现的议题（什么让他停不下来）
- 惯用的观察角度
- 思维里未解决的张力
- 如果他写内容，他的真实声音会说什么
"""

__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

import os, json, random, re, time
from openai import OpenAI
import chromadb
from dotenv import load_dotenv

load_dotenv()

SAMPLE_TOTAL = 300
BATCH_SIZE   = 30

ai = OpenAI(
    api_key=os.environ["MINIMAX_API_KEY"],
    base_url="https://api.minimaxi.com/v1",
)

chroma = chromadb.PersistentClient(path=os.environ["CHROMA_PATH"])
col    = chroma.get_or_create_collection("memories", metadata={"hnsw:space": "cosine"})


PATTERN_PROMPT = """\
下面是一个人（以下简称"我"）的私人笔记、收藏文章和对话片段的随机样本。

请作为一个观察者，只根据这些内容，回答以下问题。不要猜测，只写你真正看到的。

**输出格式（严格 JSON）：**
{
  "反复出现的议题": [
    "用一句话描述一个他反复回到的主题，附上你判断的依据（哪类内容让你这么判断）"
  ],
  "惯用的观察角度": [
    "他看待事物时习惯从哪个切入点出发？给出具体例子"
  ],
  "思维里的未解张力": [
    "他似乎同时持有哪两种相互拉扯的想法或倾向？"
  ],
  "他可能的真实声音": [
    "如果他写内容，他最自然会说的是什么类型的话？给出一个具体的句子示例"
  ],
  "你注意到的反常": [
    "有什么让你觉得出乎意料或者矛盾的地方？"
  ]
}

**待分析的内容样本：**
"""


def sample_docs(n: int) -> list[str]:
    total = col.count()
    print(f"ChromaDB 共 {total} 条，随机抽取 {n} 条...")
    all_ids = col.get(limit=total)["ids"]
    sampled_ids = random.sample(all_ids, min(n, len(all_ids)))
    result = col.get(ids=sampled_ids)
    return result["documents"]


def analyze_batch(docs: list[str], batch_num: int, total_batches: int) -> dict:
    content = "\n---\n".join(d[:500] for d in docs)  # 每条最多 500 字
    prompt = PATTERN_PROMPT + content

    print(f"  分析第 {batch_num}/{total_batches} 批（{len(docs)} 条）...")
    for attempt in range(3):
        try:
            resp = ai.chat.completions.create(
                model="MiniMax-M2.7",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content or ""
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            start = text.find('{')
            end   = text.rfind('}') + 1
            if start == -1 or end == 0:
                print(f"    [WARN] 未返回有效 JSON，跳过")
                return {}
            return json.loads(text[start:end])
        except Exception as e:
            if attempt == 2:
                print(f"    [ERROR] {e}")
                return {}
            time.sleep(3)
    return {}


def synthesize(batches: list[dict]) -> dict:
    """把多批分析结果合并，再让 LLM 综合提炼一次"""
    merged = {}
    for batch in batches:
        for key, items in batch.items():
            merged.setdefault(key, []).extend(items)

    print("\n综合提炼中...")
    synth_prompt = """\
下面是对同一个人的多批次认知分析结果，每个维度可能有重复或矛盾。

请综合提炼，每个维度保留 3-5 条最有洞察力、最具体的观察。
删除空洞、重复、过于笼统的条目。
如有矛盾，保留最具体的那条，或在同一条目里呈现张力。

严格输出 JSON，格式与输入相同，不加任何说明：
""" + json.dumps(merged, ensure_ascii=False, indent=2)

    for attempt in range(3):
        try:
            resp = ai.chat.completions.create(
                model="MiniMax-M2.7",
                max_tokens=2000,
                messages=[{"role": "user", "content": synth_prompt}],
            )
            text = resp.choices[0].message.content or ""
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            start = text.find('{')
            end   = text.rfind('}') + 1
            if start != -1 and end > 0:
                return json.loads(text[start:end])
        except Exception as e:
            if attempt == 2:
                print(f"  [WARN] 综合失败，返回合并版本: {e}")
                return merged
            time.sleep(3)
    return merged


def print_result(result: dict):
    print("\n" + "="*50)
    print("你的认知指纹")
    print("="*50)
    for section, items in result.items():
        if not items:
            continue
        print(f"\n【{section}】")
        for item in items:
            print(f"  · {item}")


if __name__ == "__main__":
    print("=== who_am_i：从你存的东西里，照出你是谁 ===\n")

    docs = sample_docs(SAMPLE_TOTAL)
    random.shuffle(docs)

    batches = []
    total_batches = (len(docs) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(docs), BATCH_SIZE):
        batch = docs[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        result = analyze_batch(batch, batch_num, total_batches)
        if result:
            batches.append(result)
        time.sleep(1)

    if not batches:
        print("所有批次均失败，退出")
        exit(1)

    final = synthesize(batches)

    print_result(final)

    # 保存
    output_path = os.path.join(os.environ.get("DATA_PATH", "."), "who_am_i.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存至：{output_path}")
