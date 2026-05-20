# Self Bot 项目总结

**最后更新**：2026-05-15  
**服务器**：阿里云 47.108.56.88（litai.studio）  
**代码位置**：`/Users/aero/Desktop/常用文件汇总/代码/skills/self-bot/`  
**服务器代码**：`/home/bots/`

---

## 是什么

部署在阿里云的个人 bot，有两个入口、两种人格：

| 入口 | 模式 | 记忆访问 |
|------|------|----------|
| 飞书私聊 | 私人（第二自我） | 完整 RAG + memories.json + SQLite 对话历史 |
| 个人主页网页 | 公开（名片式介绍） | 仅 public_bio.md |

---

## 实现逻辑

### 第一层：数据入库（一次性，可重跑）

**数据源：**
- `ingest.py`：Flomo 笔记 HTML、Claude 对话 JSON、Gemini 对话 TXT
- `ingest_wechat.py`：20 个微信私聊 JSONL（WeFlow 导出），匿名化处理

**入库流程：**
1. 解析各数据源，切成 chunk
2. 调千问 `text-embedding-v3` 转向量（OpenAI 兼容接口）
3. 向量 + 原文存进 ChromaDB（余弦距离），MD5 chunk_id 支持断点续传
4. 微信记录额外做匿名化（真实姓名 → 关系标签，去除手机号/邮箱/身份证）

**当前数据量**：ChromaDB 共 13,768 条（Flomo/Claude/Gemini + 微信 3,747 条）

### 第二层：RAG 检索（每次对话按需触发）

`rag.py` 负责检索：
1. 检测消息是否含关键词（之前/曾经/记得/怎么看等）
2. 有则把问题转向量，在 ChromaDB 做 Top-5 余弦相似度搜索
3. 检索结果临时拼入 system prompt

### 第三层：对话历史（SQLite 持久化）

`conversations.db` 存储所有对话记录：
- 表结构：`id, user_id, role, content, timestamp`
- 每次对话读取最近 20 条作为上下文，重启不丢失
- 支持 `/reset` 指令清空当前用户的历史记录

### 第四层：生成回复

**System prompt 结构：**
- 常驻：`memories.json`（经历、观点）+ 人格设定 + 行为约束
- 动态：RAG 检索结果（仅记忆查询时追加）

**行为约束（当前版本）：**
- 不表演聪明，不给标准答案
- 镜子优先：先确认我在说什么，再给意见
- 可以直接挑战，但要说明理由
- 直接输出答案，不输出思考过程，不使用英文
- 不以 AI 身份回答，始终用第一人称

**模型**：MiniMax-M2.7，max_tokens=512，过滤 `<think>...</think>`

**公开网页端**：不用 RAG，只加载 `public_bio.md`，Flask SSE 流式输出

---

## 部署架构

```
阿里云 47.108.56.88 (litai.studio)
├── nginx (1.20.1)
│   ├── HTTPS litai.studio (Let's Encrypt 证书)
│   ├── /api/ → localhost:5001 (self_web.py，SSE配置)
│   └── / → localhost:3000 (个人主页)
├── systemd
│   ├── self-feishu.service (python3.11 self_feishu.py，开机自启)
│   └── self-web.service    (python3.11 self_web.py，开机自启)
└── /home/bots/
    ├── self_feishu.py       # 私人飞书 bot
    ├── self_web.py          # 公开网页 API
    ├── rag.py               # RAG 检索模块
    ├── ingest.py            # 基础数据入库
    ├── ingest_wechat.py     # 微信记录入库
    ├── requirements.txt
    ├── .env
    └── data/
        ├── memories.json        # 常驻 system prompt（核心身份）
        ├── public_bio.md        # 公开简介（待补充）
        ├── conversations.db     # SQLite 对话历史
        ├── texts/               # 微信 JSONL 原始文件（20个）
        └── chroma_db/           # 向量库（13,768 条）
```

---

## 关键技术决策

**为什么用千问 embedding 而不是本地模型**  
服务器只有 2GB 内存，跑不起任何 embedding 模型。千问按量计费，入库后日常查询量极小。

**为什么 memories.json 不入库**  
memories.json 是精炼的自我认知，信息密度高、量少，适合常驻 system prompt。历史对话内容量大细节多，适合按需检索。

**为什么公开端不用 RAG**  
所有 RAG 数据源（Flomo 笔记、各类对话）都是私人内容。公开端只展示主动整理过的 public_bio.md，隔离清晰。

**pysqlite3 workaround**  
Alibaba Cloud Linux 3 的 sqlite3 版本太旧，ChromaDB 要求 3.35+。用 `pysqlite3-binary` 替换系统 sqlite3 模块（所有用到 chromadb 的文件头部三行）。

---

## 待办 / 后续优化

### 基础完善
- [ ] 补充 `public_bio.md` 内容，完善公开端介绍
- [ ] 把 `chat-widget.html` 嵌入 litai.studio 个人主页
- [ ] 在 memories.json 补充个人生活偏好（旅行、饮食、习惯等）
- [ ] 公开端加入"升级到真人"机制：bot 评估对话是否值得通知真实的我，触发后推送飞书私信附对话摘要，我可回复指令让 bot 转达
- [ ] `/remember xxx` 指令：用户主动发送纠错或新增记忆，bot 将内容追加写入 memories.json 并重新加载（区别于自动反思，这是用户主动触发的精准修正）
- [ ] **对话式冷启动引导**：新用户进来 bot 主动提问（你做什么工作？最近在想什么？），10 分钟内完成初始记忆建立，比"上传文件"转化率更高，决定新用户留存
- [ ] **"今日可输出"主动推送**：每天定时基于当天工作沉淀生成一条内容草稿，以飞书卡片推送给用户审阅（一键发布/修改/跳过），解决"坚持不下去"的核心痛点

### 数据源扩充
- [ ] 阅读笔记（微信读书导出、Kindle 标注）→ 展示品味和思考框架
- [ ] 日记文本 → 最高密度的自我表达
- [ ] 朋友圈/收藏内容 → 兴趣图谱

### 数据持续同步（替代手动上传）

**背景**：目前数据入库是一次性手动上传，后续希望与笔记系统打通，让新内容自动流进 ChromaDB，OpenMe 的记忆库持续生长而不是静止的快照。参考项目：[Ars Contexta](https://github.com/agenticnotetaking/arscontexta)（用 AI 生成持续更新的 Markdown 知识图谱，无需专有数据库）。

- [ ] 确认主要笔记软件（Flomo/Notion/Obsidian），调研对应 API 自动同步方案
- [ ] bot 对话记录（SQLite）定期自动向量化入库，历史对话也成为可检索记忆
- [ ] 飞书消息自动同步：重要对话自动流进 ChromaDB，不需要手动导出 JSONL

### RAG 优化
- [ ] **智能引用**：bot 回答时标注来源（来自哪段对话/哪篇笔记/哪个关系），让用户可以追溯原始记忆片段
- [ ] n_results 从 5 调大到 8~12（当前召回数量偏少）
- [ ] 利用 metadata 里的 `relation` 字段做上下文过滤（聊工作只检索同事/老板相关片段）
- [ ] 关键词触发范围扩大（当前只有 9 个词，可以改为语义判断）

### 主动服务与反思机制（参考 QwenPaw + OpenHanako）
- [ ] **独立后台进程**：参考 OpenHanako 的 Hub 设计，用独立进程运行后台任务（定时巡检、记忆整理、主动触达），与当前对话进程分离，不互相阻塞
- [ ] **主动触达**：bot 在特定时间主动给你发消息（早间问题、基于近期对话的洞察、待办提醒），而不只是被动等待
- [ ] **对话后反思**：每次对话结束后自动总结关键信息，更新对你的理解并写入记忆层

### 消息总线架构重构（参考 PocketPaw）
- [ ] 把 self_feishu.py 和 self_web.py 的渠道逻辑抽象为适配器，核心 agent 逻辑统一到一个地方，新增渠道（微信/Telegram等）只需接适配器，不改核心代码

### SOP 引擎（参考 zeroclaw）
- [ ] 调研 zeroclaw 的 SOP 引擎设计，把常见工作流程编码为 SOP（如：处理某类飞书消息的步骤、回复某类问题的框架），让 agent 按固定流程执行而不完全依赖 LLM 自由发挥

### 轻量级 Agent 架构（参考 nanobot）
- [ ] 深入研究 nanobot 的代码实现，参考其"基于 token 的记忆管理"机制，评估是否能补充当前 RAG 的不足
- [ ] 参考 nanobot 的 MCP 支持设计，为 OpenMe 规划外部工具接入路径（飞书日历、搜索等）

### 记忆动态更新（参考 obsidian-second-brain）
- [ ] **重写而非追加**：新内容入库时检测是否与已有内容矛盾，自动更新旧记忆而不是堆叠，避免 bot 检索到自相矛盾的内容
- [ ] **双时态事实追踪**：记录观点的变化轨迹（"在某时间点相信 X，后来转向 Z"），让记忆反映人的成长而不是静态快照

### 入库质量过滤（参考 second-brain-ai-assistant-course）
- [ ] 入库前用 LLM 对每个 chunk 打质量分，过滤掉低价值内容（闲聊、重复、无实质信息的对话段），提升检索结果精准度

### 记忆关联发现（参考 My-Brain-Is-Full-Crew 的 Connector agent）
- [ ] 写一个定期脚本，对 ChromaDB 里的内容做跨来源关联分析，找出工作对话/朋友聊天/Flomo笔记之间隐含的同一主题，把关联显式存入 L1 摘要层

### 三层架构参考升级
- [ ] **入库层（参考 Khoj）**：调研 Khoj 的文档解析模块，支持 PDF/Notion/Word 直接入库，替换现有手写 ingest 脚本
- [ ] **记忆层（参考 Second Me）**：实现 L1 摘要层 + 记忆权重机制（access_count），高频记忆优先召回，低权重记忆归档
- [ ] **人格层（参考 OpenClaw）**：把 memories.json 拆分为 SOUL.md（价值观）/ IDENTITY.md（外部表现）/ USER.md（自我画像），分类管理

### Second-Me HMM 架构参考（核心优化方向）

参考 [Second-Me 项目](https://github.com/mindverse/Second-Me) 的分层记忆模型（HMM）：

| 层级 | 当前状态 | 目标 |
|------|----------|------|
| **L0 原始数据** | ✅ 已实现（ChromaDB RAG） | 继续扩充数据源 |
| **L1 模式摘要** | ❌ 未实现 | 对 RAG 内容做聚类+摘要，提炼行为模式 |
| **L2 核心身份** | 部分实现（memories.json） | 结构化，加入价值观、边界、长期目标 |

**L1 层的实现思路**：  
写一个定期脚本，对 ChromaDB 里的内容按主题聚类，用 LLM 生成段落级摘要，存入另一个 collection。例如：

```
[关于职场关系的模式]
在上下级关系中，倾向于...从与老板的多次对话可以看出...

[关于决策风格的模式]  
面对选择时，通常先独自消化，再寻求外部视角...
```

L1 摘要既不像 L0 那样碎片化，也不像 memories.json 那样抽象，填补了"内容丰富度"的空缺。

**多角色意识**：  
根据对话中检测到的情境（工作/情感/创业），自动切换侧重点，类似于人在不同场合的自然切换。

---

## 飞书卡片流式输出实现要点

飞书私聊 bot 使用卡片 JSON 2.0 实现打字机效果，需要飞书客户端 7.20+。

**流程**：创建卡片实体 → 发送卡片消息 → 边生成边 PUT 更新文本 → 关闭流式模式

**关键 API**：
- 创建卡片：`POST /cardkit/v1/cards`，`type: "card_json"`，`data` 为 JSON 字符串
- 发消息：`msg_type: "interactive"`，`content: {"type":"card","data":{"card_id":"..."}}`（序列化为字符串）
- 更新文本：`PUT /cardkit/v1/cards/{card_id}/elements/{element_id}/content`，`content` 传全量文本，`sequence` 严格递增
- 关闭流式：`PATCH /cardkit/v1/cards/{card_id}/settings`，`settings` 为 JSON 字符串，含 `sequence`

**streaming_config 正确结构**（注意多一层 `config`）：
```json
"streaming_config": {
    "config": {"print_frequency_ms": 30, "print_step": 2}
}
```

**资源消耗**：打字机效果不增加 token 消耗，只增加少量服务器 HTTP 请求（每 0.3 秒一次）。开启 `streaming_mode` 后不受飞书 10次/秒的频率限制。

**所需权限**：`im:message`、`im:message:send_as_bot`、`cardkit:card:write`

---

## 已知问题

- memories.json 缺乏个人生活类内容，问到旅行/饮食等话题时 bot 无法回答
- MiniMax-M2.7 有时不用 `<think>` 标签直接输出英文推理（已加 prompt 约束，待验证）
- 微信记录覆盖了工作/朋友/家人/恋爱多种关系，但 RAG 检索时关系类型未被利用
