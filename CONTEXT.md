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
| `rag.py` | 向量检索：`retrieve(query)` 返回 Top-N 相似文本 |
| `extract_memories.py` | 从 ChromaDB 随机抽样 → LLM 提取关键事实（定期运行，更新 memories.json）|

---

## 数据存储

| 存储 | 路径（由 .env 中 DATA_PATH / CHROMA_PATH 决定）| 内容 |
|------|------|------|
| ChromaDB | `CHROMA_PATH` | 所有个人记忆向量，collection 名为 `memories`，当前约 1.4 万条 |
| SQLite | `DATA_PATH/conversations.db` | 飞书 Bot 对话历史（近 20 条用于上下文）|
| memories.json | `DATA_PATH/memories.json` | 人格文件：个人风格、判断、偏好（即将拆分为四文件系统）|
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

## 人格系统（待升级）

**当前状态**：所有人格信息混在 `memories.json` 一个文件里。

**计划升级为四文件系统**（P0 任务，尚未实现）：
- `SOUL.md` — 核心人格：语气、风格、行为约束、三种子模式（推演/镜子/记忆）
- `USER.md` — 当前状态：职业、正在做的事、短期目标（变化最频繁）
- `MEMORY.md` — 跨 session 积累的关键事实：经历、观点、判断、偏好
- `SKILLS.md` — Bot 当前能力说明：推演框架、RAG 逻辑、指令说明

**负责模块**：需提取 `persona.py`，统一管理四文件，`self_feishu.py` 通过 `persona.py` 访问。

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

## 当前最高优先级（P0）

1. **人格四文件系统** — 拆分 memories.json，提取 persona.py，这是内容草稿质量的直接前提
2. **RAG 触发改为语义判断** — 当前 9 个关键词覆盖不足
3. **内容草稿质量验证** — 生成 10 篇草稿，统计实际发布率，目标 ≥50%（核心假设验证）

## 代码架构待改进（P1）

- `embedding.py` — 合并 5 处重复的 embed() + 重试逻辑
- `stream.py` — 统一飞书端和网页端的 `<think>` 过滤逻辑
- `feishu_client.py` — 合并 3 处重复的 token 缓存逻辑
- `config.py` — 集中管理全部环境变量（P2）

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
