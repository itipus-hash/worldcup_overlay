#!/usr/bin/env python3
"""
发送修正后的"比分修正"测试推送
包含 VAR 取消提示
"""
import requests
import json
import os

SETTINGS_PATH = os.path.expanduser("~/.worldcup_overlay/settings.json")
with open(SETTINGS_PATH, "r") as f:
    settings = json.load(f)
TOKEN = settings.get("pushplus_token", "accae3466f374091b5b041c843f50e6e")

url = "https://www.pushplus.plus/api/send"
headers = {"Content-Type": "application/json"}

print("发送【修正后的】比分修正测试推送...")
print()

# 修正后的格式（包含 VAR 取消提示）
payload = {
    "token": TOKEN,
    "title": "🔄 比分修正（进球取消）",
    "content": """墨西哥 vs 英格兰
当前比分：2 - 2 · 下半场
（英格兰 -1）
进球明细：
  36' 裘德·贝林厄姆（英格兰） 头球  0-1
  42' Julián Quiñones（墨西哥） 进球  1-2
  60' 哈里·凯恩（英格兰） 点球  1-3
  69' Raúl Jiménez（墨西哥） 点球  2-3
⚠️ 注：英格兰一粒进球已被VAR取消（原比分 2-3 → 2-2）""".replace("\n", "<br>")
}

resp = requests.post(url, json=payload, headers=headers, timeout=10)
print(f"状态码：{resp.status_code}")
print(f"响应：{resp.text}")

if resp.ok:
    print()
    print("=" * 60)
    print("✅ 测试推送已发送！")
    print("请检查微信中是否收到，以及格式是否正确。")
    print("=" * 60)
