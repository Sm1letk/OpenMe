__import__('pysqlite3')
import sys
sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')

import os, json, random, time
from openai import OpenAI
import chromadb
from dotenv import load_dotenv

load_dotenv()

CHROMA_PATH  = os.environ["CHROMA_PATH"]
DATA_PATH    = os.environ["DATA_PATH"]
OUTPUT_PATH  = os.path.join(DATA_PATH, "memories_extracted.json")
SAMPLE_TOTAL = 200   # 从 ChromaDB 随机抽取的总条数
BATCH_SIZE   = 20    # 每次送给 LLM 的条数

ai = OpenAI(
    api_key=os.environ["MINIMAX_API_KEY"],
    base_url="https://api.minimaxi.com/v1",
)

chroma = chromadb.PersistentClient(path=CHROMA_PATH)
col    = chroma.get_or_create_collection("memories", metadata={"hnsw:space": "cosine"})


EXTRACT_PROMPT = """你是一个记忆整理助手。下面是一个人（以下简称"我"）的私人笔记和对话片段，请提取关于"我"自身的事实性信息。

**核心规则——归属判断：**
- 对话格式为 "我：xxx" 和 "对方：xxx"（对方可能是 朋友A、同事B、前女友 等标签）
- 只提取"我"说的内容所反映的事实：我的经历、我的观点、我的习惯、我的状态
- 对方说的话、对方的经历、对方的看法，一律不提取，即使听起来很有趣
- 如果不确定某件事是"我"的还是"对方"的，跳过，不提取
- 笔记类内容（无对话格式）默认全部属于"我"

**提取规则：**
- 只提取有明确依据的事实，不要猜测或推断
- 每条事实要具体，避免"喜欢思考"这类空洞表述
- 如果某类别没有内容，输出空数组

**输出格式（严格JSON，不加任何说明文字）：**
{
  "工作与职业": ["..."],
  "生活偏好": ["..."],
  "价值观与信念": ["..."],
  "人际关系": ["..."],
  "习惯与作息": ["..."],
  "兴趣爱好": ["..."],
  "当前状态": ["..."],
  "过往经历": ["..."]
}

**待分析的内容：**
"""


def sample_docs(n: int) -> list[str]:
    total = col.count()
    print(f"ChromaDB 共 {total} 条，随机抽取 {n} 条...")
    all_ids = col.get(limit=total)["ids"]
    sampled_ids = random.sample(all_ids, min(n, len(all_ids)))
    result = col.get(ids=sampled_ids)
    return result["documents"]


def extract_batch(docs: list[str]) -> dict:
    content = "\n---\n".join(docs)
    prompt = EXTRACT_PROMPT + content

    for attempt in range(3):
        try:
            resp = ai.chat.completions.create(
                model="MiniMax-M2.7",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.choices[0].message.content or ""
            # 过滤 think 标签
            import re
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            # 提取 JSON
            start = text.find('{')
            end   = text.rfind('}') + 1
            if start == -1 or end == 0:
                print(f"  [WARN] LLM 未返回有效 JSON，跳过本批次")
                return {}
            return json.loads(text[start:end])
        except Exception as e:
            if attempt == 2:
                print(f"  [ERROR] 提取失败: {e}")
                return {}
            print(f"  [WARN] attempt {attempt+1} 失败: {e}，重试...")
            time.sleep(3)
    return {}


def merge_results(batches: list[dict]) -> dict:
    merged = {}
    for batch in batches:
        for category, items in batch.items():
            if category not in merged:
                merged[category] = []
            merged[category].extend(items)

    # 简单去重（完全相同的句子）
    for category in merged:
        merged[category] = list(dict.fromkeys(merged[category]))

    return merged


def deduplicate_with_llm(merged: dict) -> dict:
    """用 LLM 对合并后的结果做语义去重和精炼"""
    print("\n用 LLM 做语义去重和精炼...")

    dedup_prompt = """下面是从多批次文本中提取的关于"我"的事实，同一类别内可能有重复或矛盾的条目。
请：
1. 删除明显属于"对方"而非"我"的条目（如对方的家庭背景、对方的经历）
2. 合并语义相近的条目，保留最具体的表述
3. 如有矛盾，保留看起来更新/更具体的那条
4. 删除过于模糊的条目（如"喜欢思考"这类废话）
5. 每个类别最多保留 15 条

严格输出 JSON，不加任何说明：
""" + json.dumps(merged, ensure_ascii=False, indent=2)

    for attempt in range(3):
        try:
            resp = ai.chat.completions.create(
                model="MiniMax-M2.7",
                max_tokens=2048,
                messages=[{"role": "user", "content": dedup_prompt}],
            )
            text = resp.choices[0].message.content or ""
            import re
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            start = text.find('{')
            end   = text.rfind('}') + 1
            if start != -1 and end > 0:
                return json.loads(text[start:end])
        except Exception as e:
            if attempt == 2:
                print(f"  [WARN] 去重失败，返回未精炼版本: {e}")
                return merged
            time.sleep(3)
    return merged


if __name__ == "__main__":
    print("=== 从 ChromaDB 自动提取记忆草稿 ===\n")

    docs = sample_docs(SAMPLE_TOTAL)

    # 分批处理
    batches = []
    total_batches = (len(docs) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(docs), BATCH_SIZE):
        batch_docs = docs[i:i + BATCH_SIZE]
        batch_num  = i // BATCH_SIZE + 1
        print(f"处理第 {batch_num}/{total_batches} 批...")
        result = extract_batch(batch_docs)
        if result:
            batches.append(result)
        time.sleep(1)  # 避免触发频率限制

    if not batches:
        print("所有批次均提取失败，退出")
        exit(1)

    print(f"\n合并 {len(batches)} 批次结果...")
    merged = merge_results(batches)

    # 统计合并后的数量
    total_facts = sum(len(v) for v in merged.values())
    print(f"合并后共 {total_facts} 条事实，开始精炼...")

    final = deduplicate_with_llm(merged)

    # 写入文件
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    final_total = sum(len(v) for v in final.values())
    print(f"\n完成！共提取 {final_total} 条记忆，保存到：{OUTPUT_PATH}")
    print("\n--- 提取结果预览 ---")
    for category, items in final.items():
        if items:
            print(f"\n【{category}】")
            for item in items[:3]:
                print(f"  · {item}")
            if len(items) > 3:
                print(f"  ... 共 {len(items)} 条")
