#!/usr/bin/env python3
"""发送带比赛阶段的进球明细测试推送"""
import requests
import json
import os

SETTINGS_PATH = os.path.expanduser("~/.worldcup_overlay/settings.json")
with open(SETTINGS_PATH, "r") as f:
    settings = json.load(f)
TOKEN = settings.get("pushplus_token", "accae3466f374091b5b041c843f50e6e")

url = "https://www.pushplus.plus/api/send"
headers = {"Content-Type": "application/json"}

print("发送【带比赛阶段】的测试推送...")
print()

payload = {
    "token": TOKEN,
    "title": "墨西哥 VS 英格兰 比赛结束",
    "content": """最终比分：2 - 3
全场比赛结束 🏁
进球明细：
  上半场 36' 裘德·贝林厄姆（英格兰） 头球  0-1
  上半场 38' 裘德·贝林厄姆（英格兰） 进球  0-2
  上半场 42' Julián Quiñones（墨西哥） 进球  1-2
  下半场 60' 哈里·凯恩（英格兰） 点球  1-3
  下半场 69' Raúl Jiménez（墨西哥） 点球  2-3""".replace("\n", "<br>")
}

resp = requests.post(url, json=payload, headers=headers, timeout=10)
print(f"状态码：{resp.status_code}")
print(f"响应：{resp.text}")

if resp.ok:
    print()
    print("=" * 60)
    print("✅ 测试推送已发送！")
    print("请检查微信中是否收到，格式是否正确。")
    print("=" * 60)
