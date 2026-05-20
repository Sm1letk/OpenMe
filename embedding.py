import os, time

_qw = None


def _get_client():
    global _qw
    if _qw is None:
        from openai import OpenAI
        _qw = OpenAI(
            api_key=os.environ["QIANWEN_API_KEY"],
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    return _qw


def embed(text: str) -> list[float]:
    client = _get_client()
    for attempt in range(3):
        try:
            resp = client.embeddings.create(
                model="text-embedding-v3",
                input=text[:2000],
            )
            return resp.data[0].embedding
        except Exception as e:
            if attempt == 2:
                raise
            print(f"[WARN] embed failed (attempt {attempt+1}): {e}, retrying...")
            time.sleep(2)
