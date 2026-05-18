import sys
sys.path.insert(0, '/Users/aero/Desktop/常用文件汇总/代码/skills/self-bot')
from content_ingest import detect_url_type, extract_tweet_info

def test_detect_wechat():
    assert detect_url_type("https://mp.weixin.qq.com/s/KjJf8nGXRJUiuDg5E6Uh4g") == "wechat"

def test_detect_x():
    assert detect_url_type("https://x.com/btcbears/status/2055923801519542485?s=46") == "x"

def test_detect_twitter():
    assert detect_url_type("https://twitter.com/sama/status/1234567890") == "x"

def test_detect_unknown():
    assert detect_url_type("https://github.com/foo/bar") is None

def test_extract_tweet_info():
    username, tweet_id = extract_tweet_info("https://x.com/btcbears/status/2055923801519542485?s=46")
    assert username == "btcbears"
    assert tweet_id == "2055923801519542485"
