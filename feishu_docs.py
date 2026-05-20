# /home/bots/feishu_docs.py
"""
飞书云文档创建工具。
调用 Feishu docx v1 API 在指定文件夹创建文档并写入内容。
"""

import os
import requests
from dotenv import load_dotenv
from feishu_client import get_token

load_dotenv()

FEISHU_API = "https://open.feishu.cn/open-apis"
FOLDER_TOKEN = "WFqPfQ3RElxZdEdj93lceJbknBd"
FEISHU_DOMAIN = "acn0f8jrf3fc.feishu.cn"


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}


def _create_document(title: str) -> str:
    """在指定文件夹创建空白文档，返回 document_id"""
    resp = requests.post(
        f"{FEISHU_API}/docx/v1/documents",
        headers=_headers(),
        json={"folder_token": FOLDER_TOKEN, "title": title},
        timeout=10,
    ).json()
    if resp.get("code") != 0:
        raise RuntimeError(f"创建文档失败: {resp}")
    return resp["data"]["document"]["document_id"]


def _add_content_blocks(document_id: str, content: str):
    """将正文按段落拆分，批量插入为 paragraph 块"""
    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
    if not paragraphs:
        return

    children = []
    for para in paragraphs:
        children.append({
            "block_type": 2,
            "text": {
                "elements": [{"text_run": {"content": para}}],
                "style": {}
            }
        })

    resp = requests.post(
        f"{FEISHU_API}/docx/v1/documents/{document_id}/blocks/{document_id}/children",
        headers=_headers(),
        json={"children": children, "index": 0},
        timeout=15,
    ).json()
    if resp.get("code") != 0:
        raise RuntimeError(f"写入内容失败: {resp}")


def create_doc(title: str, content: str) -> str:
    """
    在草稿文件夹创建飞书文档，写入内容，返回文档 URL。
    """
    document_id = _create_document(title)
    _add_content_blocks(document_id, content)
    return f"https://{FEISHU_DOMAIN}/docx/{document_id}"
