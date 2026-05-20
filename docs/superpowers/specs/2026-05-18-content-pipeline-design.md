# OpenMe 内容流水线设计文档

**日期：** 2026-05-18  
**状态：** 已确认  

---

## 目标

为「野生西兰花」个人 IP 建立一套内容生产流水线：自动聚合外部优质内容作为素材，每日推送选题建议，用户确认后一键生成草稿并存入飞书云文档。

---

## 数据来源

| 来源 | 方式 | source 标记 |
|------|------|-------------|
| 微信公众号文章 | 用户手动发链接给飞书 bot，UA 伪装抓正文 | `wechat_manual` |
| X 推文 | 用户手动发链接给飞书 bot，fxtwitter API 抓正文 | `x_manual` |
| AI builder 动态 | 每日定时拉取 follow-builders GitHub JSON | `follow_builders` |

**质量策略：** 全部先存，生成选题时再筛，不在入库阶段过滤。

---

## 模块设计

### 1. 入库模块（集成进 `self_feishu.py`）

**触发：** 用户在飞书发送一条消息，内容为 URL。

**逻辑：**
- 检测消息是否为链接（`http://` 或 `https://` 开头）
- 微信链接（包含 `mp.weixin.qq.com`）→ 调用 `fetch_wechat.py` 抓正文
- X 链接（包含 `x.com` 或 `twitter.com`）→ 调用 fxtwitter API 抓正文
- 其他链接 → 暂不处理，bot 回复「暂不支持该链接类型」
- 抓取成功 → 向量化后存入 ChromaDB，打上 source / url / timestamp 元数据
- bot 回复：「已入库：{文章标题}」

**依赖：**
- `fetch_wechat.py`（已有，UA 伪装）
- fxtwitter API：`https://api.fxtwitter.com/{username}/status/{tweet_id}`
- ChromaDB（已有）
- Qianwen text-embedding-v3（已有）

---

### 2. follow-builders 定时拉取（`fetch_builders.py`）

**触发：** 每日 cron，建议 UTC 09:00（北京时间 17:00，follow-builders 更新约 UTC 07:45）

**逻辑：**
1. GET `https://raw.githubusercontent.com/zarazhangrui/follow-builders/main/feed-x.json`
2. 遍历所有 builder 的 tweets
3. 按 tweet id 去重（ChromaDB 中检查是否已存在）
4. 新 tweet → 向量化 → 存入 ChromaDB，source=`follow_builders`，附带 author/handle/url 元数据
5. 打印入库数量日志

**字段映射：**
```
tweet.id        → doc id
tweet.text      → 正文
tweet.createdAt → timestamp
builder.name    → author
builder.handle  → handle
tweet.url       → url（可回溯原推）
```

---

### 3. 选题推送（`suggest_topics.py`）

**触发：** 每日 cron，北京时间 08:00

**逻辑：**
1. 从 ChromaDB 取近 3 天内入库的内容（按 timestamp 过滤）
2. 读取 `memories.json` 用户身份
3. 调用 MiniMax 生成 10 条选题建议，每条格式：
   ```
   {序号}. {选题标题}
   角度：{一句话说明创作切入点}
   来源：{原文摘要 30 字以内}（{原文链接}）
   ```
4. 通过飞书 bot 推送给用户
5. 将 10 条选题和对应素材缓存到本地（`/home/bots/data/pending_topics.json`），等待用户回复

**无新内容处理：** 近 3 天无入库内容时，跳过推送，不发消息。

---

### 4. 用户交互与草稿生成（集成进 `self_feishu.py`）

**触发：** 用户回复选题推送消息

**支持的回复格式：**
- `3` → 按第 3 条原方向生成
- `3，但我想聚焦在 XX 角度` → 按修改后方向生成
- `长文` / `短文` → 指定草稿类型（默认长文）

**草稿生成逻辑：**
1. 解析用户回复，取出选题编号 + 可选的方向修改
2. 从 `pending_topics.json` 取出对应选题的源材料
3. 拼接 prompt（参考 `generate_drafts.py` 现有模板）
4. 调用 MiniMax 生成草稿
5. 调用飞书云文档 API，在指定文件夹创建新文档
   - 文件夹：`https://acn0f8jrf3fc.feishu.cn/drive/folder/WFqPfQ3RElxZdEdj93lceJbknBd`
   - 文档标题：`{日期}_{选题标题}`
   - 写入草稿正文（含 frontmatter：类型/主题/生成时间/发布状态）
6. bot 回复：「草稿已生成：{飞书文档链接}」

**长文规格：** 800-1200 字，适合公众号/知乎  
**短文规格：** 150-300 字，适合即刻/小红书

---

## 会话状态管理

`self_feishu.py` 需要维护简单的会话状态，区分两种用户消息：
- **链接消息** → 触发入库流程
- **数字回复**（如 `3` 或 `3，但...`）→ 触发草稿生成流程
- **其他消息** → 走现有 RAG 问答流程

状态通过 `pending_topics.json` 是否存在且未过期（24 小时内）来判断是否处于「等待选题回复」状态。

---

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `self_feishu.py` | 修改 | 新增链接入库 + 选题回复处理逻辑 |
| `fetch_builders.py` | 新建 | follow-builders 定时拉取脚本 |
| `suggest_topics.py` | 新建 | 选题生成 + 推送脚本 |
| `fetch_wechat.py` | 复用 | 已有，直接调用 |
| `data/pending_topics.json` | 运行时生成 | 缓存待确认选题 |

---

## Cron 配置（服务器）

```bash
# 每日 08:00 北京时间推送选题（UTC 00:00）
0 0 * * * /usr/bin/python3.11 /home/bots/suggest_topics.py

# 每日 17:00 北京时间拉取 follow-builders（UTC 09:00）
0 9 * * * /usr/bin/python3.11 /home/bots/fetch_builders.py
```

---

## 不在本次范围内

- 微信公众号定时自动抓取（方案复杂度高，后续迭代）
- 内容矩阵管理（YAGNI）
- 多用户支持
- 草稿发布后的数据追踪
