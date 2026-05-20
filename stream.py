from typing import Generator


def filter_think_stream(stream) -> Generator[str, None, None]:
    """从 OpenAI 流式响应中过滤 <think>...</think>，按块 yield 净文本。
    使用状态机处理跨 chunk 的标签边界，保证正确性。
    """
    buffer = ""
    in_think = False
    for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        buffer += delta
        while True:
            if in_think:
                end = buffer.find("</think>")
                if end == -1:
                    buffer = ""
                    break
                buffer = buffer[end + 8:]
                in_think = False
            else:
                start = buffer.find("<think>")
                if start == -1:
                    if buffer:
                        yield buffer
                    buffer = ""
                    break
                if start > 0:
                    yield buffer[:start]
                buffer = buffer[start + 7:]
                in_think = True
    if buffer and not in_think:
        yield buffer
