import os, time
import requests

FEISHU_API = "https://open.feishu.cn/open-apis"
_token_cache = {"token": "", "expires_at": 0}


def get_token() -> str:
    if time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    resp = requests.post(
        f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
        json={
            "app_id": os.environ["SELF_FEISHU_APP_ID"],
            "app_secret": os.environ["SELF_FEISHU_APP_SECRET"],
        },
        timeout=10,
    ).json()
    if "tenant_access_token" not in resp:
        raise RuntimeError(f"获取 token 失败: {resp}")
    _token_cache["token"] = resp["tenant_access_token"]
    _token_cache["expires_at"] = time.time() + resp.get("expire", 7200)
    return _token_cache["token"]
