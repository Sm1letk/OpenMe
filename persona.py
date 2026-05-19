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
