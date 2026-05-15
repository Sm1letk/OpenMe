#!/bin/bash
# 部署步骤说明
# 在服务器 root@47.108.56.88 上执行

set -e

# 1. 创建目录
mkdir -p /home/bots/data /home/bots/chroma_db

# 2. 安装依赖（仅首次）
pip3 install lark-oapi openai flask chromadb beautifulsoup4 python-dotenv

# 3. 上传代码（在本地执行这些命令）
# scp /Users/aero/Desktop/常用文件汇总/代码/skills/self-bot/*.py root@47.108.56.88:/home/bots/
# scp /Users/aero/Desktop/常用文件汇总/代码/skills/self-bot/.env root@47.108.56.88:/home/bots/
# scp /Users/aero/Desktop/常用文件汇总/代码/skills/self-bot/requirements.txt root@47.108.56.88:/home/bots/

# 4. 上传数据文件（在本地执行）
# scp /path/to/memories.json root@47.108.56.88:/home/bots/data/
# scp /path/to/正觉的笔记.html root@47.108.56.88:/home/bots/data/
# scp /path/to/conversations.json root@47.108.56.88:/home/bots/data/
# scp /path/to/chat-memo_49.txt root@47.108.56.88:/home/bots/data/
# 手动在服务器上创建 /home/bots/data/public_bio.md

# 5. 填写 .env（先填写后上传）
# 必填：SELF_FEISHU_APP_ID, SELF_FEISHU_APP_SECRET, QIANWEN_API_KEY

# 6. 运行数据入库（首次，可能需要几分钟）
# cd /home/bots && python3 ingest.py

# 7. 安装 systemd service
cp /path/to/deploy/self-feishu.service /etc/systemd/system/
cp /path/to/deploy/self-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable self-feishu self-web
systemctl start self-feishu self-web
systemctl status self-feishu self-web

# 8. 配置 nginx
cp /path/to/deploy/self-bot.nginx.conf /etc/nginx/conf.d/
nginx -t && systemctl reload nginx

# 9. 验证
# curl -X POST http://47.108.56.88/api/chat \
#   -H "Content-Type: application/json" \
#   -d '{"message": "你好", "session_id": "test"}' --no-buffer

echo "部署完成！"
echo "查看日志："
echo "  journalctl -u self-feishu -f"
echo "  journalctl -u self-web -f"
