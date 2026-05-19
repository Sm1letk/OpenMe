import os
import pytest
from pathlib import Path

import persona


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

    result = persona.build_system("private")
    assert "语气直接" in result
    assert "AI产品经理" in result
    assert "喜欢挑战" in result
    assert "RAG检索" in result
    assert "第二自我" in result


def test_build_system_public_excludes_private_files(persona_dir):
    (persona_dir / "SOUL.md").write_text("语气直接", encoding="utf-8")
    (persona_dir / "USER.md").write_text("AI产品经理", encoding="utf-8")

    result = persona.build_system("public")
    assert "语气直接" in result
    assert "AI产品经理" not in result
    assert "公开端" in result


def test_build_system_missing_files_no_error(persona_dir):
    result = persona.build_system("private")
    assert isinstance(result, str)  # 不抛异常，返回字符串


def test_append_memory_writes_to_file(persona_dir):
    memory_file = persona_dir / "MEMORY.md"
    memory_file.write_text("# 初始内容\n", encoding="utf-8")

    persona.append_memory("新的判断：直接比绕弯更有效")
    content = memory_file.read_text(encoding="utf-8")
    assert "新的判断：直接比绕弯更有效" in content


def test_reload_picks_up_file_changes(persona_dir):
    soul_file = persona_dir / "SOUL.md"
    soul_file.write_text("版本一", encoding="utf-8")

    soul_file.write_text("版本二", encoding="utf-8")
    result = persona.reload()
    assert "版本二" in result
    assert "版本一" not in result
