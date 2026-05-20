# OpenMe 参考项目研究笔记

**整理时间**：2026-05-15  
**用途**：研究开源项目对 OpenMe 的启发，记录核心洞见和对应待办

---

## 一、研究项目全景

| 项目 | 方向 | 对 OpenMe 的核心贡献 |
|------|------|---------------------|
| [Second-Me](https://github.com/mindverse/Second-Me) | 记忆架构 | HMM 三层记忆模型（L0/L1/L2），DPO 自我评估 |
| [Khoj](https://github.com/khoj-ai/khoj) | 数据入库 | 多格式文档解析（PDF/Notion/Word），持续同步 |
| [Ars Contexta](https://github.com/agenticnotetaking/arscontexta) | 知识图谱 | AI 生成持续更新的 Markdown 知识图谱，无需向量数据库 |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | 人格系统 | SOUL.md / MEMORY.md / USER.md / SKILLS.md，agent 自动写 skill，三层记忆 |
| [OpenClaw](https://github.com/openclaw/openclaw) | 人格系统（前代参考） | SOUL.md / IDENTITY.md / USER.md，Hermes 的前身，仍有参考价值 |
| [ClawMem](https://github.com/yoloshii/ClawMem) | 记忆中间层 | 混合 RAG + MCP，桥接 Claude Code / Hermes / OpenClaw |
| [SoulClaw](https://github.com/clawsouls/soulclaw) | 人格质量 | OpenClaw fork，加入人格漂移检测（persona drift detection） |
| [nanobot](https://github.com/just-an-experiment/nanobot) | Agent 架构 | 基于 token 的轻量记忆管理，MCP 工具接入 |
| [zeroclaw](https://github.com/claw-ai/zeroclaw) | SOP 引擎 | 把工作流程编码为 SOP，减少 LLM 自由发挥 |
| [QwenPaw](https://github.com/QwenPaw) | 主动服务 | 定时主动触达，早间问题、对话洞察推送 |
| [OpenHanako](https://github.com/openhanako) | 后台进程 | Hub 设计：独立进程运行后台任务，不阻塞对话 |
| [PocketPaw](https://github.com/pocketpaw) | 消息总线 | 渠道适配器模式，核心 agent 与渠道解耦 |
| [obsidian-second-brain](https://github.com/nickmilo/obsidian-second-brain) | 记忆更新 | 重写而非追加，双时态事实追踪 |
| [second-brain-ai-assistant-course](https://github.com/decodingml/second-brain-ai-assistant-course) | 入库质量 | LLM 对 chunk 打质量分，过滤低价值内容 |
| [My-Brain-Is-Full-Crew](https://github.com/my-brain-is-full-crew) | 记忆关联 | Connector agent：跨来源关联分析，发现隐含主题 |
| [MiroFish](https://github.com/666ghj/MiroFish) | 多智能体模拟 | Zep 记忆管理、GraphRAG、种子→模拟→报告工作流 |

---

## 二、分方向深度笔记

### 记忆架构：Second-Me HMM 模型

**核心思想**：把记忆分为三层，各层有不同的粒度和更新频率

| 层级 | 内容 | OpenMe 现状 | 目标 |
|------|------|------------|------|
| L0 原始数据 | 对话片段、笔记原文 | ✅ ChromaDB 13,768 条 | 继续扩充数据源 |
| L1 模式摘要 | 按主题聚类后的行为模式 | ❌ 未实现 | 写定期聚类+摘要脚本 |
| L2 核心身份 | 价值观、边界、长期目标 | 部分实现（memories.json） | 结构化，拆分为多文件 |

**L1 层实现思路**：对 ChromaDB 内容按主题聚类，LLM 生成段落级摘要，存入独立 collection。

示例输出：
```
[关于职场关系的模式]
在上下级关系中，倾向于...从与老板的多次对话可以看出...

[关于决策风格的模式]
面对选择时，通常先独自消化，再寻求外部视角...
```

**DPO 自我评估**：Second-Me 用 DPO 方法让模型学习"什么样的回答更像我"，得分 0.91。OpenMe 暂不实现，但可作为长期方向（Fine-tune 路线）。

---

### 数据入库：Khoj

**核心贡献**：支持 PDF / Notion / Word / GitHub 直接入库，有持续同步机制

**OpenMe 现状**：手写 ingest 脚本，只支持 HTML（Flomo）、JSON（Claude/Gemini 对话）、JSONL（微信 WeFlow 导出）

**借鉴方向**：调研 Khoj 的文档解析模块，扩展 ingest.py 支持更多格式，减少手动转换

---

### 知识图谱：Ars Contexta

**核心思想**：用 AI 生成持续更新的 Markdown 知识图谱，不依赖向量数据库

**适用场景**：笔记系统打通后，让新内容自动流进知识库。比 ChromaDB 更轻，但检索语义相似度能力弱

**对 OpenMe 的意义**：作为 L1 层的替代实现思路，Markdown 知识图谱可读性更好，也更容易人工审阅和修正

---

### 人格系统：Hermes Agent（主要参考）+ OpenClaw（前代参考）

**Hermes Agent**（Nous Research，2026年2月，140K+ stars，增速超过 OpenClaw）

核心文件系统：

| 文件 | 内容 | 大小约束 |
|------|------|---------|
| `SOUL.md` | 主身份——语气、风格、沟通习惯、禁止行为 | 无上限，完整注入 prompt |
| `MEMORY.md` | 跨 session 学到的事实和用户偏好 | ~2,200 字符上限 |
| `USER.md` | 用户画像，持续更新 | ~1,375 字符上限 |
| `SKILLS.md` | **Agent 自动写**：完成任务后自动总结经验为 skill 条目 | 按需增长 |

**Hermes 的核心创新：自我进化循环**
- 每完成约 15 次工具调用或复杂任务后，自动暂停、反思
- 把"这次任务的经验"写入 SKILLS.md，下次做同类事情直接调用
- 三层记忆：session（当前对话）/ episodic SQLite（历史事件）/ procedural（SKILLS.md 能力积累）

**OpenClaw**（2025年11月，347K stars，Hermes 出现前的行业标准）

| 文件 | 内容 |
|------|------|
| `SOUL.md` | 价值观、信念、底线 |
| `IDENTITY.md` | 对外表现、沟通风格、习惯用语 |
| `USER.md` | 自我画像、当前状态、短期目标 |

OpenClaw 的 skill 文件由人工维护（Markdown runbook），Hermes 改为 agent 自动生成——这是两者最根本的差异。

**ClawMem**（混合 RAG + MCP 记忆中间层）
- 桥接 Claude Code、Hermes、OpenClaw 三个系统
- 提供 on-device 混合 RAG 搜索（语义 + 关键词）
- 作为 ChromaDB 的补充思路值得关注

**SoulClaw**（OpenClaw fork，加入人格质量保障）
- **Persona drift detection**：检测 bot 的回答是否随时间偏离原始人格设定
- 4层记忆架构
- Swarm memory sync（多 agent 共享记忆）
- 对 OpenMe 的价值：避免 bot 在大量对话后"变得不像你"

**对 OpenMe 的意义**：

当前 memories.json 是单一混合文件，应按 Hermes 的模式拆分：
- `SOUL.md`：几乎不变，精雕细琢你的核心风格（公开端/私有端都用）
- `USER.md`：变化最频繁，记录当前状态和短期目标（大小有限制，保持精简）
- `MEMORY.md`：跨 session 积累的关键事实（从 memories.json 的"补充"条目迁移过来）
- `SKILLS.md`：记录 bot 学会的能力——"如何帮你写 PRD""你的内容风格偏好"等，初期人工维护，远期自动生成

bot 根据情境选择性加载：公开端只加载 SOUL.md + 部分 IDENTITY，私有飞书端加载完整四文件

---

### Agent 架构：nanobot

**核心贡献**：
1. **基于 token 的记忆管理**：不是简单的"最近 N 条"，而是动态压缩历史，在 token 预算内最大化保留信息
2. **MCP 支持**：通过 MCP 协议接入外部工具（日历、搜索、文件系统）

**对 OpenMe 的意义**：
- 当前对话历史是"最近 20 条"，nanobot 的 token-based 方案可以更智能地管理上下文
- MCP 接入路径：飞书日历 → 知道你的日程；搜索工具 → 遇到不知道的事能查

---

### SOP 引擎：zeroclaw

**核心思想**：把常见工作流程编码为固定步骤（SOP），agent 按流程执行，不完全依赖 LLM 发挥

**适用场景**：
- 处理某类飞书消息的标准流程
- 回复某类问题时的框架（先确认理解 → 再给建议 → 最后反问）
- 定期任务（每周总结、记忆整理）

**对 OpenMe 的意义**：让 bot 行为更可预测，减少"有时好有时差"的随机性

---

### 主动服务：QwenPaw + OpenHanako

**QwenPaw 贡献**：主动触达模式
- 早间问题（"今天打算做什么？"）
- 基于近期对话的洞察推送
- 待办提醒

**OpenHanako 贡献**：Hub 架构
- 独立后台进程（Hub）负责定时任务、记忆整理、主动触达
- 与对话进程完全分离，不互相阻塞
- Hub 可以在你不说话时默默工作

**对 OpenMe 的意义**：从"被动回答"升级为"主动陪伴"。当前 bot 只在你发消息时才工作。

---

### 消息总线：PocketPaw

**核心思想**：把渠道（飞书/微信/Telegram/Web）抽象为适配器，核心 agent 逻辑只写一次

```
渠道适配器 (飞书) ──┐
渠道适配器 (Web)  ──┤──→ 核心 Agent ──→ 渠道适配器 (输出)
渠道适配器 (微信) ──┘
```

**对 OpenMe 的意义**：现在 self_feishu.py 和 self_web.py 各自实现了对话逻辑。重构后新增渠道只需写适配器。

---

### 记忆质量：obsidian-second-brain + second-brain-ai-assistant-course

**obsidian-second-brain 贡献**：
- **重写而非追加**：新内容入库时检测矛盾，更新旧记忆而不是堆叠
- **双时态事实追踪**：记录"在某时间点相信 X，后来转向 Z"，反映成长轨迹

**second-brain-ai-assistant-course 贡献**：
- 入库前用 LLM 对每个 chunk 打质量分（0-10）
- 低于阈值的 chunk 不入库（过滤闲聊、重复、无实质内容）
- 提升检索精准度，减少噪声

---

### 多智能体模拟：MiroFish

**项目背景**：盛大集团孵化，基于 CAMEL-AI 的 OASIS 框架，群体智能引擎，通过多智能体模拟预测现实走向。

**核心架构**：
1. 种子提取（新闻/政策/金融信号）→ GraphRAG 构建知识图谱
2. 实体关系提取 + 每个 agent 配置独立人格和长期记忆
3. 多平台并行模拟推演
4. ReportAgent 生成深度交互报告

**对 OpenMe 的借鉴**：

| MiroFish 设计 | 对应 OpenMe 场景 | 可行性 |
|-------------|----------------|--------|
| **Zep Cloud** 管理 agent 长期记忆（时序感知、结构化提取） | 替代 SQLite 的对话历史存储，统一记忆管理，与四文件系统协同 | ⭐⭐⭐ 短期可落地 |
| **GraphRAG** 构建知识图谱，理解记忆间关系 | 替代 ChromaDB 纯向量检索，发现"这篇文章和那条判断有关联" | ⭐⭐ P1 阶段 |
| 种子 → 模拟 → 报告工作流 | 与 OpenMe 的"外部内容入库 → 选题 → 草稿"结构同构，**验证现有设计方向正确** | 验证意义 |
| "上帝视角"变量注入 | 用户回复选题时注入角度（已有雏形：`3，但聚焦 XX`），可结构化为受众/语气/切入点参数 | ⭐ 低优先级 |

**最值得跟进的一点**：Zep 专为 AI agent 设计，支持时序感知（知道记忆的新旧）和结构化提取，且有免费额度。与 P0 任务"人格四文件系统"的记忆管理部分直接对应，可以用 Zep 替代现有的 SQLite 对话历史 + ChromaDB 记忆的分裂状态。

---

### 记忆关联：My-Brain-Is-Full-Crew

**核心贡献**：Connector agent——定期对向量库内容做跨来源关联分析

**示例**：工作对话里提到"想创业"+ 朋友聊天里提到"对某个赛道感兴趣"+ Flomo 笔记里有相关思考 → 自动发现这三者指向同一主题，显式存入 L1 摘要层

**对 OpenMe 的意义**：让 bot 能说"我注意到你在很多场合都提到了 X，我们聊聊这个？"

---

## 三、优先级建议

按实现成本 vs 效果排序：

| 优先级 | 待办 | 成本 | 效果 |
|--------|------|------|------|
| 🔴 高 | 扩大 RAG 触发范围（降低关键词门槛） | 低 | 高 |
| 🔴 高 | memories.json → 四文件拆分（Hermes 方案：SOUL / USER / MEMORY / SKILLS） | 低 | 高 |
| 🟡 中 | Zep 替代 SQLite 对话历史（MiroFish 方案，统一记忆管理） | 中 | 中 |
| 🟡 中 | L1 摘要层（定期聚类脚本） | 中 | 很高 |
| 🟡 中 | 入库质量过滤（chunk 打分） | 中 | 中 |
| 🟡 中 | 对话后反思（自动提取关键信息写入记忆） | 中 | 高 |
| 🟢 低 | 消息总线重构（PocketPaw 模式） | 高 | 架构 |
| 🟢 低 | 主动触达后台进程（OpenHanako Hub） | 高 | 高 |
| 🟢 低 | SOP 引擎（zeroclaw） | 高 | 中 |
| 🔵 远期 | Fine-tune / DPO（Second-Me 路线） | 极高 | 极高 |

---

## 四、还未深入研究的方向

- **情绪感知**：bot 感知当前情绪状态，调整回应策略（搜 `emotional AI companion`）
- **Fine-tune / LoRA**：把人格编进模型权重，而不只靠 RAG（搜 `personal LLM fine-tuning`）
- **评估体系**：量化"这个回答有多像我"（搜 `me-alignment score`）
- **隐私安全架构**：如果公开端规模扩大，需要完整的安全设计
- **多模态输入**：语音、图片记忆（搜 `multimodal personal assistant`）
