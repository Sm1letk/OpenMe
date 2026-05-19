# Persona 四文件系统设计文档

**日期**：2026-05-19  
**状态**：已确认  
**关联产品目标**：人格四文件系统（P0），提升内容草稿个人化程度，支撑 WAO ≥ 3 件/周

---

## 目标

把 `memories.json` 拆分为四个职责清晰的 Markdown 文件，提取 `persona.py` 模块统一管理，让 `self_feishu.py` 和 `self_web.py` 通过同一个接口获取 system prompt，改变的核心是：每次新 session 的人格数据来自结构化的文件，而不是混合的 JSON 加硬编码 Python 字符串。

---

## 文件结构

四个文件放在服务器 `DATA_PATH/persona/` 目录下，不纳入 git（个人隐私数据）：

```
DATA_PATH/persona/
├── SOUL.md      # 核心人格：语气、风格、行为约束、三种子模式。几乎不变。
├── USER.md      # 当前状态：职业、正在做的事、短期目标。每周可能更新。
├── MEMORY.md    # 跨 session 积累的关键事实：经历、判断、偏好。/remember 写入这里。
└── SKILLS.md    # Bot 当前能力说明：RAG 逻辑、指令列表、内容风格。初期人工维护。
```

`DATA_PATH/memories.json` 重命名为 `memories_archive.json`，不删除。

**文件大小约束**（参照 Hermes Agent）：
- SOUL.md：不限制，完整注入 prompt
- USER.md：保持在 1,500 字以内
- MEMORY.md：保持在 2,500 字以内
- SKILLS.md：按需增长

---

## persona.py 接口

```python
def build_system(mode: str = "private") -> str:
    """
    mode="private"  → 加载 SOUL + USER + MEMORY + SKILLS，返回完整 system prompt
    mode="public"   → 只加载 SOUL，返回公开端 system prompt（加公开端行为约束）
    文件缺失时静默跳过，不抛异常。
    """

def append_memory(content: str) -> None:
    """
    把新内容追加写入 MEMORY.md 末尾，并热重载全局 SYSTEM。
    替代原来的 memories.json 写入逻辑。
    """

def reload() -> str:
    """
    强制重读四个文件，返回新的 system prompt。
    供手动触发热重载用（文件被直接编辑后调用）。
    """
```

---

## System Prompt 拼装结构

**Private 模式（完整）**：

```
你是我自己的第二自我。你完整了解我的经历、想法和决策历史。

## 我是谁
{SOUL.md 全文}

## 当前状态
{USER.md 全文}

## 关键记忆
{MEMORY.md 全文}

## 我的能力说明
{SKILLS.md 全文}
```

**Public 模式（仅 SOUL + 公开约束）**：

```
你是正觉的 AI 分身，代表他和访客对话。

## 我是谁
{SOUL.md 全文}

## 行为约束（公开端）
- 不透露私人信息（具体公司名、客户名、收入数字等）
- 不访问记忆库，只聊公开的背景和思考
- 如果被问到私密内容，说"这个我不对外聊"，不解释原因
```

任何一个文件不存在时，该段落静默跳过，不报错。

---

## 受影响文件

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `persona.py` | 新建 | 核心模块，约80行 |
| `self_feishu.py` | 修改 | 删除 `load_memories` / `build_system` / `append_memory` 三个函数，改为 `import persona` |
| `self_web.py` | 修改 | 加 `import persona`，system prompt 改用 `persona.build_system(mode="public")` |
| `generate_drafts.py` | 修改 | `load_memories()` 改为 `persona.build_system()` 取背景段落 |
| `suggest_topics.py` | 修改 | 同上 |

---

## 迁移步骤（代码之外，人工完成）

1. 在服务器创建 `DATA_PATH/persona/` 目录
2. 上传 `persona/SOUL.md`、`USER.md`、`MEMORY.md`、`SKILLS.md`（本地已写好初稿，路径：`openme/persona/`）
3. 将服务器上的 `memories.json` 重命名为 `memories_archive.json`

代码改动不依赖内容文件是否存在——文件都为空，bot 也能跑，不会崩。可以先上线代码，再补充文件内容。

---

## 不在本次范围内

- SKILLS.md 自动写入（Hermes 风格的 agent 自我进化）——远期
- 文件变更监听热重载（watchdog）——YAGNI，当前 `append_memory` 已满足需求
- persona 文件的版本历史追踪
