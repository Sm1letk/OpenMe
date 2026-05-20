# OpenMe — 项目上下文

> **每次新 session 开始时请先读这个文件。** 它描述了项目的当前状态、文件职责和下一步计划。

---

## 产品是什么

**OpenMe** 是一个"装载了自己"的个人 AI 系统，愿景是：**让你走过的每一天，都成为你未来的资产。**

有两个入口：
- **私人端**（飞书私聊 Bot）：完整访问个人记忆库，支持 RAG 检索、对话历史持久化、URL 入库、内容草稿生成
- **公开端**（网页 API）：只暴露个人简介，访客可以和"我的 AI 分身"对话

**北极星指标**：WAO（Weekly Adopted Outputs）≥ 3 件/周
- 包括：发布到平台的内容草稿、提交使用的 PRD、主动 `/remember` 的关键判断

**当前阶段**：自用验证 → 准备面向外部用户

---

## 文件结构与职责

### 核心服务（持续运行）

| 文件 | 职责 |
|------|------|
| `self_feishu.py` | 飞书私人 Bot 核心：对话、流式卡片、URL 入库、选题回复、草稿生成触发 |
| `self_web.py` | 公开网页 API（Flask SSE），只返回个人简介相关回答 |

### 数据入库（手动 or 定时触发）

| 文件 | 触发方式 | 数据来源 |
|------|---------|---------|
| `ingest.py` | 手动 | Flomo HTML 导出、Claude 对话 JSON、Gemini 对话 JSON |
| `ingest_wechat.py` | 手动 | WeFlow 导出的微信聊天记录 JSONL（脱敏后分段入库）|
| `ingest_wechat_articles.py` | 手动 | wechat-article-exporter 导出的公众号文章 JSON（批量抓取正文入库）|
| `ingest_inbox.py` | 手动 | `data/inbox/` 目录下的 `.md` 文件（入库后移入 `done/`）|
| `content_ingest.py` | 飞书 Bot 触发 | 用户发给 Bot 的微信公众号链接 / X 推文链接（实时抓取入库）|
| `fetch_builders.py` | 每日 17:00 cron | GitHub follow-builders JSON，拉取 AI builder 推文 |

### 内容生产（定时 or 手动触发）

| 文件 | 触发方式 | 职责 |
|------|---------|------|
| `suggest_topics.py` | 每日 08:00 cron | 从 ChromaDB 取近 3 天内容 → 生成 10 条选题 → 推送飞书卡片 → 缓存 `pending_topics.json` |
| `generate_drafts.py` | 手动 | 从 ChromaDB 按主题采样 → 生成 5 篇长文 + 5 篇短文草稿 → 保存到 `drafts-{日期}/` |
| `feishu_docs.py` | 被 `self_feishu.py` 调用 | 调用飞书 docx API 创建云文档并写入草稿内容 |

### 记忆与工具

| 文件 | 职责 |
|------|------|
| `persona.py` | 人格模块：`build_system(mode)`、`load_context()`、`append_memory()`、`reload()`，统一管理四文件 |
| `rag.py` | 向量检索：`retrieve(query)` 返回 Top-N 相似文本 |
| `extract_memories.py` | 从 ChromaDB 随机抽样 → LLM 提取关键事实（已归档，不再主动使用）|
| `who_am_i.py` | 认知指纹分析：随机采样 300 条 → 分析反复议题/惯用角度/未解张力/真实声音 → 输出 `DATA_PATH/who_am_i.json` |

---

## 数据存储

| 存储 | 路径（由 .env 中 DATA_PATH / CHROMA_PATH 决定）| 内容 |
|------|------|------|
| ChromaDB | `CHROMA_PATH` | 所有个人记忆向量，collection 名为 `memories`，当前约 1.4 万条 |
| SQLite | `DATA_PATH/conversations.db` | 飞书 Bot 对话历史（近 20 条用于上下文）|
| persona/ | `DATA_PATH/persona/` | 四文件人格系统：SOUL.md / USER.md / MEMORY.md / SKILLS.md（不纳入 git）|
| memories_archive.json | `DATA_PATH/memories_archive.json` | 旧人格文件备份，已不再使用 |
| pending_topics.json | `DATA_PATH/pending_topics.json` | 当日待确认选题缓存（24 小时有效）|
| data/inbox/ | `DATA_PATH/inbox/` | 待入库的 .md 文件（入库后移入 done/）|

### ChromaDB source 字段标签

每条记录都有 `source` metadata，用于 RAG 过滤：

| source 值 | 来源 | 入库脚本 |
|-----------|------|---------|
| `follow_builders` | fetch_builders.py 每日拉取的 AI builder 推文 | `fetch_builders.py` |
| `wechat_article` | 公众号文章（wechat-article-exporter 导出）| `ingest_wechat_articles.py` |
| `wechat_manual` | 微信聊天记录片段 | `ingest_wechat.py` |
| `x_manual` | 手动导入的 X 推文 | `content_ingest.py` |
| `general_manual` | 用户发给 Bot 的普通 URL | `content_ingest.py` |
| `manual_save` | ingest.py 导入的 Flomo/对话记录 | `ingest.py` |
| `inbox` | data/inbox/ 目录下的 .md 文件 | `ingest_inbox.py` |

RAG 可按 source 过滤，例如查询提到"36氪"或"公众号"时自动加 `where={"source": "wechat_article"}`。

---

## 内容流水线（已实现）

**完整链路：**
```
外部内容来源
  ├── 用户发公众号/X链接 → content_ingest.py → ChromaDB
  ├── fetch_builders.py 每日拉取 AI builder 推文 → ChromaDB
  └── 手动入库（ingest_wechat_articles.py 等）→ ChromaDB
            ↓
  suggest_topics.py 每日 08:00
  → 取近 3 天内容 → 生成 10 条选题 → 推飞书卡片
            ↓
  用户回复选题编号（如 "3" 或 "3，聚焦XX角度"）
  → self_feishu.py 解析 → 调用 MiniMax 生成草稿
  → feishu_docs.py 创建飞书文档 → bot 回复文档链接
```

**选题回复格式**：`3`（按原方向）或 `3，但我想聚焦在 XX 角度`

---

## 人格系统（已上线）

**当前状态**：四文件系统已实现并部署（2026-05-19）。

四个文件存放在服务器 `DATA_PATH/persona/`，不纳入 git：
- `SOUL.md` — 核心人格：语气、风格、行为约束、三种子模式（推演/镜子/知识库）
- `USER.md` — 当前状态：职业、正在做的事、短期目标（变化最频繁，定期手动更新）
- `MEMORY.md` — 跨 session 积累的关键事实，`/remember` 指令自动追加写入
- `SKILLS.md` — Bot 当前能力说明：RAG 逻辑、指令列表、内容生成规格

**接口**（`persona.py`）：
- `build_system("private")` — 完整 system prompt，飞书端使用
- `build_system("public")` — 仅 SOUL + 公开约束，网页端使用
- `load_context()` — 返回 USER.md + MEMORY.md 纯文本，供草稿/选题生成使用
- `append_memory(content)` — 追加写入 MEMORY.md
- `reload()` — 强制重读文件，返回新 system prompt

---

## RAG 检索现状

- **触发方式**：`is_memory_query()` 检测到 20+ 个关键词（"记得"、"之前"、"36氪"、"公众号"、"文章"等）才查向量库，否则不检索
- **Source 过滤**：查询中含特定关键词时自动加 `where` 过滤（如提到"36氪"→ `source=wechat_article`）
- **召回数量**：n_results = 5（路线图计划改为 10）
- **待改进**：改为语义触发（每次都检索，由调用方决定是否注入），更智能

---

## 部署

服务器上以 **nohup** 后台运行两个服务（非 systemd）：
```bash
nohup python self_feishu.py >> logs/feishu.log 2>&1 &
nohup python self_web.py >> logs/web.log 2>&1 &
```

Nginx 反代 `self_web.py`（Flask，默认 5000 端口），飞书 Bot 通过长轮询接收消息（不需要公网回调地址）。

Cron 任务（服务器本地时间 UTC+8）：
- `00:00 UTC`（北京 08:00）— `suggest_topics.py`
- `09:00 UTC`（北京 17:00）— `fetch_builders.py`

**inbox 工作流**：无法从服务器直接抓取的内容（如需登录的页面、WeChat 文章正文），先在本地整理成 `.md` 文件放入 `DATA_PATH/inbox/`，上传服务器后运行 `python ingest_inbox.py` 批量入库，处理完自动移入 `done/` 子目录。

---

## 已知限制

| 问题 | 原因 | 绕过方式 |
|------|------|---------|
| 服务器无法抓取微信公众号正文 | WeChat 反爬，服务器 IP 被封返回验证码页 | 本地 wechat-article-exporter 导出 JSON，仅用标题+摘要入库；或手动整理 .md 放 inbox |
| 服务器无法用 r.jina.ai / defuddle.md | 代理服务在中国大陆服务器被屏蔽 | 无（只能依赖本地 inbox 流程）|
| X Article 正文为空 | Twitter/X 的长文格式抓不到正文 | 手动复制内容到 inbox/.md |
| `ingest_wechat_articles.py` 只有摘要 | 服务器 IP 被 WeChat 封锁，无法实时抓取 | 接受现状：摘要已足够做选题参考 |

---

## 当前最高优先级

**P0（已完成）**：
- ✅ 人格四文件系统 — persona.py 已上线，四文件已部署至服务器
- ✅ `who_am_i.py` — 认知指纹分析脚本，已部署并跑通（2026-05-20）
- ✅ `suggest_topics.py` bug fix — 修复 LLM 返回 markdown 代码块导致 JSON 解析失败、KeyError 问题（2026-05-20）
- ✅ 代码架构清理 — 提取 `embedding.py` / `feishu_client.py` / `stream.py` 三个共享模块，消除 11 处重复逻辑，修复 `self_feishu.py` 跨 chunk think 过滤 bug（2026-05-20，已部署）

**P0（进行中）**：
1. **野生西兰花第一条内容** — 用户已知自己的真实声音（把沉重说得轻巧）和惯用角度（成本视角、第一性原理），但还未迈出发布第一步。核心卡点：等待完全想清楚才发，而清晰只会在发布后产生。
2. **who_am_i 结果的消化与应用** — 认知指纹已生成（保存在 `DATA_PATH/who_am_i.json`），下一步：用结果指导野生西兰花的内容角度选择

**P1**：
3. **RAG 触发改为语义判断** — 当前关键词覆盖不足，改为每次都检索由调用方决定是否注入
4. **L1 Wiki 层** — Karpathy 模式，LLM 入库时自动更新 Markdown 知识页面

## 代码架构待改进（P1）

- `config.py` — 集中管理全部环境变量（P2）

### 已完成（2026-05-20）

- ✅ `embedding.py` — 统一 5 处 embed() + 重试逻辑，所有入库脚本 `from embedding import embed`
- ✅ `feishu_client.py` — 统一 3 处 token 缓存，进程内 token 共享，修复 self_feishu.py 缺失的错误检查
- ✅ `stream.py` — 统一流式 `<think>` 过滤，`filter_think_stream(stream)` 生成器，修复 self_feishu.py 跨 chunk 边界 bug

---

## 关键环境变量

| 变量 | 用途 |
|------|------|
| `QIANWEN_API_KEY` | 千问 embedding API |
| `MINIMAX_API_KEY` | MiniMax 对话生成 API |
| `CHROMA_PATH` | ChromaDB 存储路径 |
| `DATA_PATH` | 数据文件根目录 |
| `SELF_FEISHU_APP_ID` / `SELF_FEISHU_APP_SECRET` | 飞书 Bot 凭证 |
| `TARGET_CHAT_ID` | 选题推送目标飞书会话 ID |

---

## 文档目录

- `docs/product/` — 产品文档（roadmap、北极星、假设、pre-mortem 等）
  - `openme-reference-projects.md` — 参考项目研究笔记（Second-Me、Hermes、MiroFish 等 16 个项目的洞见与对应待办）
- `docs/plans/` — 实现计划
- `docs/specs/` — 技术设计文档
- `docs/superpowers/` — Claude Code session 生成的计划和设计文档

---

## 产品定位（2026-05-20 再次澄清）

**核心痛点（用户自述）**：想做内容创作（野生西兰花），但不知道发什么、不知道怎么写适合自己的内容。

这个痛点揭示了 OpenMe 真正要解决的问题不是"找回信息"，而是：

> **帮我看看我到底在关心什么** — 从积累的内容里，照出自己的认知指纹。

这是一个**自我认知系统**，不是内容生成工具，也不只是检索工具。

**三个核心功能层次**（重新梳理）：
1. **向内照镜子** — `who_am_i.py`：从 ChromaDB 随机采样，分析认知模式（反复议题 / 惯用角度 / 未解张力 / 真实声音）
2. **向外找素材** — RAG 检索：给定问题时找到相关积累
3. **输出辅助** — 草稿生成、选题推送：现有功能，是输出口不是核心

**竞争对手**：Notion（太重）、Flomo（只存不用）、Mem.ai（英文，无中文生态）。

**开源方向**：中文个人认知积累系统 + 飞书入口，目前没有人做好这个组合。

---

## 产品定位（2026-05-19 澄清，已被上述更新）

**OpenMe 的核心不是内容生成工具，而是认知积累基础设施。**

> 把每天的碎片认知，变成未来可用的资产。

内容草稿（公众号）只是一个输出口，不是核心价值。真正的差异化：
- 输入零摩擦（飞书 bot，随手发）
- 向量检索解决"找不到"问题
- 把"积累"和"输出"连起来

竞争对手不是 AI 写作工具，而是 Notion（太重）、Flomo（只存不用）、Mem.ai（英文，无中文生态）。

### 为什么 Second Brain 没做成（OpenMe 的差异化来源）

1. **输入太重** — Notion/Obsidian 要主动整理打标签，人在最有想法时不想做这些
2. **只存不用** — 捕捉了大量内容，但没有输出倒逼机制，变成数字仓库
3. **检索烂** — 关键词搜索对个人知识库没用，记不住自己三个月前写过什么词
4. **组织系统比使用系统更性感** — 用户沉迷配置 PARA，但不知道"用"在哪里
5. **AI 之前做不了** — 语义检索、自动摘要、内容生成都需要 LLM，之前天花板就在这

OpenMe 用飞书 bot 解决了输入摩擦，用向量检索解决了找不到，用草稿生成把积累和输出连起来。

---

## 开源策略（2026-05-19）

**目标路径**：参考 BettaFish 作者经验——自用验证 → 开源 → 积累 star → 转化为机会（offer/投资/合作）

**当前阶段**：自用验证中，**尚未完成验证，不到开源时机。**

### 验证完成的标准（满足后再考虑开源）

1. **草稿发布率 ≥50%** — 生成的草稿有一半真的发出去了，证明内容质量过关
2. **WAO ≥ 3 件/周持续 4 周** — 系统真的在产出，不是偶尔跑一次
3. **能用一句话解释价值** — "我用它做了什么，得到了什么结果"，有具体案例

### 开源时的差异化定位

**中文个人认知积累系统 + 飞书入口**，目前没有人做好这个组合。
- 不是又一个 AI 写作工具
- 不是又一个 RAG Demo
- 是"让每天的认知变成资产"这件事的最小可用实现
---

## 记忆架构：三层模型最终确认（2026-05-19）

Andrej Karpathy 在 2026-04-xx 发布的 [Wiki 模式 gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 与已研究的 L1 摘要层 + Ars Contexta 方案本质相同，验证了方向正确。

**三层架构：**

```
L0  原始存档（ChromaDB）
    所有碎片原文，向量化，完整保留，RAG 检索
        ↓ 入库时同步整理
L1  Wiki 知识页面（待实现）
    LLM 在每次入库时自动更新对应 Markdown 页面
    例：MiroFish.md、职场判断.md、创业思考.md
    查询时优先走 Wiki，找不到再走 L0 RAG
        ↓ 长期提炼
L2  人格四文件（待实现）
    SOUL / USER / MEMORY / SKILLS
    常驻 system prompt，几乎不变
```

**RAG 不会被替代**：Wiki 和 RAG 是分工关系。
- Wiki = 百科全书，结构化结论，直接翻页
- RAG = 原始书库，找具体出处和细节

**实现顺序**：
1. ✅ 四文件人格系统（已完成 2026-05-19）
2. 🟡 L1 Wiki 层（Karpathy 模式，中等成本，价值最高）
3. 🟡 Zep 替代 SQLite

---

## 参考项目研究（摘要）

完整研究见 `docs/product/openme-reference-projects.md`，核心结论：

| 方向 | 参考项目 | 核心贡献 |
|------|---------|---------|
| 人格系统 | Hermes Agent | SOUL/USER/MEMORY/SKILLS 四文件，agent 自动写 skill |
| 记忆架构 | Second-Me | HMM 三层记忆（L0 原始/L1 聚类摘要/L2 核心身份） |
| 记忆管理 | MiroFish | Zep 统一管理对话历史 + 向量记忆，时序感知 |
| 知识图谱 | Ars Contexta / MiroFish | GraphRAG 替代纯向量检索，理解记忆间关系 |
| 入库质量 | second-brain-ai-assistant-course | LLM 对 chunk 打质量分，过滤噪声 |
| 主动服务 | QwenPaw + OpenHanako | Hub 后台进程 + 主动触达模式 |
| 渠道解耦 | PocketPaw | 渠道适配器模式，core agent 只写一次 |
