# OpenMe — 部署在云端的第二自我

把自己的笔记、对话历史向量化，部署一个真正了解你的个人 bot。私人端作为"第二自我"，公开端作为个人主页名片。

## 架构

```
你的数据（Flomo / Claude对话 / 微信记录）
    ↓ ingest.py / ingest_wechat.py
ChromaDB（向量存储，约 1-2万条）
    ↓ rag.py（按需检索，Top-5 相似片段）
memories.json（核心身份，常驻 system prompt）
    ↓
self_feishu.py → 飞书私聊 bot（完整记忆 + RAG + 对话历史）
self_web.py    → 个人主页 API（仅公开简介，Flask SSE 流式）
```

## 两种人格

| 入口 | 模式 | 记忆访问 |
|------|------|----------|
| 飞书私聊 | 私人（第二自我） | memories.json + RAG + SQLite 对话历史 |
| 个人主页 | 公开（名片式） | 仅 public_bio.md |

## 核心特性

- **飞书打字机效果**：使用 CardKit v1 流式卡片，边生成边更新（每 0.3 秒推送一次）
- **RAG 按需检索**：检测到记忆类问题时自动检索 ChromaDB，Top-5 相似片段注入 system prompt
- **SQLite 对话持久化**：重启不丢失历史，`/reset` 指令清空当前用户记录
- **微信记录匿名入库**：真实姓名 → 关系标签，自动过滤手机号/身份证/邮箱
- **断点续传**：MD5 chunk_id 去重，重跑 ingest 不会重复入库

## 快速开始

### 1. 环境准备

```bash
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入你的 API 密钥
```

### 2. 准备数据

```bash
# Flomo HTML 导出、Claude/Gemini 对话 JSON 放入 DATA_PATH 目录
python3 ingest.py

# 微信记录（WeFlow 导出的 JSONL）
# 先编辑 ingest_wechat.py 中的 RELATIONSHIP_MAP 和 USER_NAMES
python3 ingest_wechat.py
```

### 3. 启动服务

```bash
# 飞书私人 bot
python3 self_feishu.py

# 公开网页 API
python3 self_web.py
```

### systemd（生产环境）

```bash
# 参考 deploy/ 目录下的 service 文件
systemctl start self-feishu
systemctl start self-web
```

## 所需 API

| 服务 | 用途 | 获取方式 |
|------|------|----------|
| 飞书开放平台 | 私人 bot | 创建企业自建应用 |
| MiniMax | 生成回复 | api.minimaxi.com |
| 阿里云千问 | 向量 embedding | dashscope.aliyuncs.com |

所需飞书权限：`im:message`、`im:message:send_as_bot`、`cardkit:card:write`

## 数据目录结构

```
data/
├── memories.json        # 核心身份（手动维护，常驻 system prompt）
├── public_bio.md        # 公开简介（用于网页端）
├── conversations.db     # SQLite 对话历史（自动生成）
├── texts/               # 微信 JSONL 原始文件（不入 git）
└── chroma_db/           # 向量库（不入 git）
```

## memories.json 格式

支持两种格式：

```json
// 对象格式（推荐）
{
  "工作与职业": ["..."],
  "价值观": ["..."],
  "生活偏好": ["..."]
}

// 列表格式
[
  {"content": "..."},
  {"content": "..."}
]
```

用 `extract_memories.py` 可以从 ChromaDB 自动提取草稿：

```bash
python3 extract_memories.py
# 输出到 data/memories_extracted.json，审阅后合并进 memories.json
```

## 飞书流式卡片实现要点

飞书客户端 7.20+ 支持卡片打字机效果。关键点：

- 创建卡片：`POST /cardkit/v1/cards`，type 必须是 `"card_json"`
- 发消息：content 为 `{"type":"card","data":{"card_id":"..."}}` 序列化字符串
- 更新文本：`PUT` 而非 PATCH，传全量文本，sequence 严格递增
- streaming_config 需要嵌套 config 层：`{"config": {"print_frequency_ms": 30, "print_step": 2}}`
- 关闭流式：settings 字段为 JSON 字符串，需带 sequence

## 技术选型说明

**为什么用千问 embedding**：服务器 2GB 内存跑不起本地 embedding 模型，千问按量计费，日常查询量极小。

**为什么 memories.json 不入 ChromaDB**：精炼的自我认知适合常驻 prompt；历史对话量大细节多，适合按需检索。

**为什么公开端不用 RAG**：所有 RAG 数据源都是私人内容，公开端只展示主动整理的 public_bio.md，隔离清晰。

**pysqlite3 workaround**：Alibaba Cloud Linux 3 的 sqlite3 版本太旧，ChromaDB 要求 3.35+。所有用到 chromadb 的文件头部加三行替换。
