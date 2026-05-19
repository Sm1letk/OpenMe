# Persona 四文件系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 提取 `persona.py` 模块，把 `memories.json` 的人格逻辑迁移到 SOUL/USER/MEMORY/SKILLS 四个 Markdown 文件，`self_feishu.py` 和 `self_web.py` 通过 `persona.build_system()` 统一获取 system prompt。

**Architecture:** 新建 `persona.py` 提供 `build_system(mode)` / `append_memory(content)` / `reload()` 三个接口，读取 `DATA_PATH/persona/` 目录下四个 Markdown 文件拼装 system prompt。`self_feishu.py` 删除原有三个函数并改为 import persona，`self_web.py` / `suggest_topics.py` / `generate_drafts.py` 同步替换 `load_memories()` 调用。

**Tech Stack:** Python 3.11, pathlib, pytest, python-dotenv

---

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `persona.py` | 核心模块，三个公开接口 |
| 新建 | `tests/test_persona.py` | persona.py 单元测试 |
| 修改 | `self_feishu.py` | 删除三个函数，import persona |
| 修改 | `self_web.py` | system prompt 改用 persona |
| 修改 | `suggest_topics.py` | `_load_memories()` 改用 persona |
| 修改 | `generate_drafts.py` | `load_memories()` 改用 persona |

---

## Task 1: 新建 persona.py 并写测试

**Files:**
- Create: `persona.py`
- Create: `tests/test_persona.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_persona.py`：

```python
import os
import pytest
from pathlib import Path


@pytest.fixture
def persona_dir(tmp_path, monkeypatch):
    """创建临时 persona 目录，设置 DATA_PATH 环境变量。"""
    p = tmp_path / "persona"
    p.mkdir()
    monkeypatch.setenv("DATA_PATH", str(tmp_path))
    return p


def test_build_system_private_includes_all_files(persona_dir):
    (persona_dir / "SOUL.md").write_text("语气直接", encoding="utf-8")
    (persona_dir / "USER.md").write_text("AI产品经理", encoding="utf-8")
    (persona_dir / "MEMORY.md").write_text("喜欢挑战", encoding="utf-8")
    (persona_dir / "SKILLS.md").write_text("RAG检索", encoding="utf-8")

    import importlib, persona
    importlib.reload(persona)

    result = persona.build_system("private")
    assert "语气直接" in result
    assert "AI产品经理" in result
    assert "喜欢挑战" in result
    assert "RAG检索" in result
    assert "第二自我" in result


def test_build_system_public_excludes_private_files(persona_dir):
    (persona_dir / "SOUL.md").write_text("语气直接", encoding="utf-8")
    (persona_dir / "USER.md").write_text("AI产品经理", encoding="utf-8")

    import importlib, persona
    importlib.reload(persona)

    result = persona.build_system("public")
    assert "语气直接" in result
    assert "AI产品经理" not in result
    assert "公开端" in result


def test_build_system_missing_files_no_error(persona_dir):
    import importlib, persona
    importlib.reload(persona)

    result = persona.build_system("private")
    assert isinstance(result, str)  # 不抛异常，返回字符串


def test_append_memory_writes_to_file(persona_dir):
    memory_file = persona_dir / "MEMORY.md"
    memory_file.write_text("# 初始内容\n", encoding="utf-8")

    import importlib, persona
    importlib.reload(persona)

    persona.append_memory("新的判断：直接比绕弯更有效")
    content = memory_file.read_text(encoding="utf-8")
    assert "新的判断：直接比绕弯更有效" in content


def test_reload_picks_up_file_changes(persona_dir):
    soul_file = persona_dir / "SOUL.md"
    soul_file.write_text("版本一", encoding="utf-8")

    import importlib, persona
    importlib.reload(persona)

    soul_file.write_text("版本二", encoding="utf-8")
    result = persona.reload()
    assert "版本二" in result
    assert "版本一" not in result
```

- [ ] **Step 2: 运行测试，确认全部失败**

```bash
cd /Users/aero/Desktop/常用文件汇总/代码/projects/openme
python -m pytest tests/test_persona.py -v
```

预期：`ImportError: No module named 'persona'`

- [ ] **Step 3: 实现 persona.py**

创建 `persona.py`：

```python
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _persona_dir() -> Path:
    return Path(os.environ["DATA_PATH"]) / "persona"


def _read_file(filename: str) -> str:
    """读取 persona 目录下的文件，不存在时静默返回空字符串。"""
    try:
        return (_persona_dir() / filename).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def build_system(mode: str = "private") -> str:
    """
    构建 system prompt。
    mode="private": 加载 SOUL + USER + MEMORY + SKILLS（私人飞书端）
    mode="public":  只加载 SOUL + 公开端行为约束（网页端）
    任何文件缺失时静默跳过，不抛异常。
    """
    soul = _read_file("SOUL.md")
    sections = []

    if soul:
        sections.append(f"## 我是谁\n{soul}")

    if mode == "private":
        prefix = "你是我自己的第二自我。你完整了解我的经历、想法和决策历史。"
        for filename, heading in [
            ("USER.md", "## 当前状态"),
            ("MEMORY.md", "## 关键记忆"),
            ("SKILLS.md", "## 我的能力说明"),
        ]:
            content = _read_file(filename)
            if content:
                sections.append(f"{heading}\n{content}")
    else:
        prefix = "你是正觉的 AI 分身，代表他和访客对话。"
        sections.append(
            "## 行为约束（公开端）\n"
            "- 不透露私人信息（具体公司名、客户名、收入数字等）\n"
            "- 不访问记忆库，只聊公开的背景和思考\n"
            "- 如果被问到私密内容，说「这个我不对外聊」，不解释原因"
        )

    return prefix + "\n\n" + "\n\n".join(sections)


def append_memory(content: str) -> None:
    """把新内容追加写入 MEMORY.md 末尾（一行一条）。"""
    path = _persona_dir() / "MEMORY.md"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n- {content}")


def reload() -> str:
    """强制重读四个文件，返回新的 private system prompt。供文件被直接编辑后手动调用。"""
    return build_system("private")
```

- [ ] **Step 4: 运行测试，确认全部通过**

```bash
python -m pytest tests/test_persona.py -v
```

预期：5 个测试全部 PASS

- [ ] **Step 5: 提交**

```bash
git add persona.py tests/test_persona.py
git commit -m "feat: add persona.py module for four-file persona system"
```

---

## Task 2: 更新 self_feishu.py

**Files:**
- Modify: `self_feishu.py`

- [ ] **Step 1: 在文件顶部 import 列表后加 `import persona`**

在 `self_feishu.py` 第 17 行（`from feishu_docs import create_doc` 之后）加一行：

```python
import persona
```

- [ ] **Step 2: 删除 `load_memories()` 函数（约第 145–153 行）**

删除以下整段：

```python
def load_memories() -> str:
    path = os.path.join(os.environ["DATA_PATH"], "memories.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, list):
            return "\n".join(item.get("content", str(item)) for item in data)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return ""
```

- [ ] **Step 3: 删除 `build_system()` 函数（约第 156–184 行）**

删除以下整段：

```python
def build_system() -> str:
    memories = load_memories()
    return f"""你是我自己的第二自我。你完整了解我的经历、想法和决策历史。
...（到函数结尾）"""
```

- [ ] **Step 4: 将 `SYSTEM = build_system()` 替换为 persona 调用（约第 187 行）**

将：
```python
SYSTEM = build_system()
```
替换为：
```python
SYSTEM = persona.build_system("private")
```

- [ ] **Step 5: 替换 `append_memory()` 函数（约第 190–204 行）**

删除原有 `append_memory()` 函数，替换为：

```python
def _remember(content: str):
    """把新内容追加写入 MEMORY.md，并热重载全局 SYSTEM。"""
    global SYSTEM
    persona.append_memory(content)
    SYSTEM = persona.build_system("private")
```

- [ ] **Step 6: 更新 `/remember` 指令的调用点（约第 407 行）**

将：
```python
append_memory(content)
```
替换为：
```python
_remember(content)
```

- [ ] **Step 7: 替换草稿生成中的 `load_memories()` 调用（约第 499 行）**

将：
```python
memories = load_memories()
```
替换为：
```python
memories = persona.build_system("private")
```

- [ ] **Step 8: 确认服务启动不报错**

```bash
cd /Users/aero/Desktop/常用文件汇总/代码/projects/openme
python -c "import self_feishu; print('OK')"
```

预期：打印 `OK`（没有 ImportError 或 NameError）

- [ ] **Step 9: 提交**

```bash
git add self_feishu.py
git commit -m "feat: migrate self_feishu.py to use persona module"
```

---

## Task 3: 更新 self_web.py

**Files:**
- Modify: `self_web.py`

- [ ] **Step 1: 在文件顶部加 `import persona`**

在 `self_web.py` 第 1 行 import 列表末尾加：

```python
import persona
```

- [ ] **Step 2: 删除 `load_public_bio()` 函数和 `PUBLIC_BIO` 变量（约第 19–27 行）**

删除：

```python
def load_public_bio() -> str:
    path = os.path.join(os.environ["DATA_PATH"], "public_bio.md")
    try:
        return open(path, encoding="utf-8").read()
    except FileNotFoundError:
        return "（暂无公开简介）"


PUBLIC_BIO = load_public_bio()
```

- [ ] **Step 3: 替换 `SYSTEM` 构建（约第 29–39 行）**

删除：

```python
SYSTEM = f"""你代表我（网站主人）和访问我个人主页的访客交流。

## 关于我
{PUBLIC_BIO}

## 行为规则
- 第一层：介绍我的经历、项目、核心观点
- 第二层：聊深了，用我自己的思维框架回应访客问题
- 只分享公开信息，不透露私人日记、私下想法、未发布内容
- 如果被问到私人信息，礼貌说"这部分我没有公开"
- 语气自然，像本人在聊天，不是客服"""
```

替换为：

```python
SYSTEM = persona.build_system("public")
```

- [ ] **Step 4: 确认服务启动不报错**

```bash
python -c "import self_web; print('OK')"
```

预期：打印 `OK`

- [ ] **Step 5: 提交**

```bash
git add self_web.py
git commit -m "feat: migrate self_web.py to use persona.build_system(public)"
```

---

## Task 4: 更新 suggest_topics.py 和 generate_drafts.py

**Files:**
- Modify: `suggest_topics.py`
- Modify: `generate_drafts.py`

- [ ] **Step 1: 更新 suggest_topics.py — 加 import**

在 `suggest_topics.py` 顶部 import 列表末尾加：

```python
import persona
```

- [ ] **Step 2: 更新 suggest_topics.py — 删除 `_load_memories()` 函数（约第 65–70 行）**

删除：

```python
def _load_memories() -> str:
    path = os.path.join(DATA_PATH, "memories.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
        if isinstance(data, list):
            return "\n".join(item.get("content", str(item)) for item in data)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return ""
```

- [ ] **Step 3: 更新 suggest_topics.py — 替换调用点（约第 172 行）**

将：
```python
memories = _load_memories()
```
替换为：
```python
memories = persona.build_system("private")
```

- [ ] **Step 4: 更新 generate_drafts.py — 加 import**

在 `generate_drafts.py` 顶部 import 列表末尾加：

```python
import persona
```

- [ ] **Step 5: 更新 generate_drafts.py — 找到并删除 `load_memories()` 函数**

在 `generate_drafts.py` 中找到 `load_memories()` 函数定义（grep 确认行号）：

```bash
grep -n "def load_memories" generate_drafts.py
```

删除该函数整体。

- [ ] **Step 6: 更新 generate_drafts.py — 替换调用点**

找到 `memories = load_memories()` 调用：

```bash
grep -n "load_memories" generate_drafts.py
```

将所有调用替换为：

```python
memories = persona.build_system("private")
```

- [ ] **Step 7: 确认两个文件语法无误**

```bash
python -c "import suggest_topics; print('suggest_topics OK')"
python -c "import generate_drafts; print('generate_drafts OK')"
```

预期：两行均打印 OK

- [ ] **Step 8: 运行全部测试，确认没有回归**

```bash
python -m pytest tests/ -v
```

预期：全部通过

- [ ] **Step 9: 提交**

```bash
git add suggest_topics.py generate_drafts.py
git commit -m "feat: migrate suggest_topics and generate_drafts to use persona module"
```

---

## Task 5: 服务器迁移（人工操作）

> 代码上线后，在服务器上执行以下步骤完成迁移。

**Files:**
- Server: `DATA_PATH/persona/` 目录（新建）
- Server: `DATA_PATH/memories_archive.json`（重命名）

- [ ] **Step 1: 上传 persona 文件**

本地执行（将 `47.108.56.88` 替换为服务器 IP，`/home/bots/data` 替换为实际 DATA_PATH）：

```bash
scp -r /Users/aero/Desktop/常用文件汇总/代码/projects/openme/persona/ root@47.108.56.88:/home/bots/data/
```

- [ ] **Step 2: 上传新代码**

```bash
scp persona.py self_feishu.py self_web.py suggest_topics.py generate_drafts.py root@47.108.56.88:/home/bots/
```

- [ ] **Step 3: 服务器上备份 memories.json**

SSH 进服务器执行：

```bash
ssh root@47.108.56.88
cd /home/bots/data
mv memories.json memories_archive.json
```

- [ ] **Step 4: 重启两个服务**

```bash
# 停止旧进程
pkill -f self_feishu.py
pkill -f self_web.py

# 重新启动
cd /home/bots
nohup python3.11 self_feishu.py >> logs/feishu.log 2>&1 &
nohup python3.11 self_web.py >> logs/web.log 2>&1 &
```

- [ ] **Step 5: 验证运行正常**

```bash
# 检查进程是否启动
ps aux | grep self_

# 检查日志无报错
tail -20 /home/bots/logs/feishu.log
tail -20 /home/bots/logs/web.log
```

预期：两个进程均在运行，日志无 ImportError 或 FileNotFoundError。

- [ ] **Step 6: 在飞书发一条测试消息**

发送任意消息，确认 bot 正常回复。发送 `/remember 测试记忆写入`，确认 bot 回复「已记住」且 `MEMORY.md` 文件末尾有新内容。
