#!/usr/bin/env python3
"""
发送测试推送（通过 PushPlus）
模拟 app 推送比赛结束通知（包含进球明细）
"""
import requests
import json
import os

# PushPlus token (从 settings.json 读取）
SETTINGS_PATH = os.path.expanduser("~/.worldcup_overlay/settings.json")
with open(SETTINGS_PATH, "r") as f:
    settings = json.load(f)
TOKEN = settings.get("pushplus_token", "accae3466f374091b5b041c843f50e6e")

# 测试推送内容（修复后，包含进球明细）
title = "墨西哥 VS 英格兰 比赛结束"

content = """最终比分：2 - 3
全场比赛结束 🏁
进球明细：
  36' 裘德·贝林厄姆（英格兰） 头球  0-1
  38' 裘德·贝林厄姆（英格兰） 进球  0-2
  42' Julián Quiñones（墨西哥） 进球  1-2
  60' 哈里·凯恩（英格兰） 点球  1-3
  69' Raúl Jiménez（墨西哥） 点球  2-3
"""

# 使用 <br> 分隔（微信渲染 HTML）
content_html = content.replace("\n", "<br>")

print("=" * 60)
print("发送测试推送")
print("=" * 60)
print()
print(f"TITLE: {title}")
print()
print("CONTENT:")
print(content)
print()
print("CONTENT (HTML):")
print(content_html)
print()
print("-" * 60)
print()

# 发送推送
url = "https://www.pushplus.plus/api/send"
headers = {"Content-Type": "application/json"}
payload = {
    "token": TOKEN,
    "title": title,
    "content": content_html,
}

print("正在发送推送...")
try:
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    print(f"状态码：{resp.status_code}")
    print(f"响应：{resp.text}")
    if resp.ok:
        print("\n✅ 推送发送成功！")
        print("请检查微信是否收到消息，以及格式是否正确。")
    else:
        print(f"\n❌ 推送发送失败：{resp.status_code}")
        print(f"响应：{resp.text}")
except Exception as e:
    print(f"\n❌ 推送发送失败：{e}")

print()
print("=" * 60)
