#!/usr/bin/env python3
"""
发送所有推送类型的测试消息
让用户在微信中确认所有格式都正确
"""
import requests
import json
import os
import time

# PushPlus token
SETTINGS_PATH = os.path.expanduser("~/.worldcup_overlay/settings.json")
with open(SETTINGS_PATH, "r") as f:
    settings = json.load(f)
TOKEN = settings.get("pushplus_token", "accae3466f374091b5b041c843f50e6e")

url = "https://www.pushplus.plus/api/send"
headers = {"Content-Type": "application/json"}

# 测试推送 1: 比赛开始/进行中
print("发送测试推送 1：比赛开始/进行中...")
payload1 = {
    "token": TOKEN,
    "title": "墨西哥 VS 英格兰 下半场 64'",
    "content": "下半场 64' · 墨西哥 vs 英格兰<br>当前比分：2 - 3<br>进球明细：<br>  36' 裘德·贝林厄姆（英格兰） 头球  0-1<br>  38' 裘德·贝林厄姆（英格兰） 进球  0-2<br>  42' Julián Quiñones（墨西哥） 进球  1-2<br>  60' 哈里·凯恩（英格兰） 点球  1-3<br>  69' Raúl Jiménez（墨西哥） 点球  2-3",
}
resp1 = requests.post(url, json=payload1, headers=headers, timeout=10)
print(f"  状态：{resp1.status_code} - {resp1.json().get('msg')}")
time.sleep(1)

# 测试推送 2: 进球推送
print("发送测试推送 2：进球推送...")
payload2 = {
    "token": TOKEN,
    "title": "⚽ 69' Raúl Jiménez（墨西哥） 点球  2-3",
    "content": "墨西哥 2-3 英格兰  进球明细：<br>  36' 裘德·贝林厄姆（英格兰） 头球  0-1<br>  38' 裘德·贝林厄姆（英格兰） 进球  0-2<br>  42' Julián Quiñones（墨西哥） 进球  1-2<br>  60' 哈里·凯恩（英格兰） 点球  1-3<br>  69' Raúl Jiménez（墨西哥） 点球  2-3",
}
resp2 = requests.post(url, json=payload2, headers=headers, timeout=10)
print(f"  状态：{resp2.status_code} - {resp2.json().get('msg')}")
time.sleep(1)

# 测试推送 3: 比赛结束
print("发送测试推送 3：比赛结束...")
payload3 = {
    "token": TOKEN,
    "title": "墨西哥 VS 英格兰 比赛结束",
    "content": "最终比分：2 - 3<br>全场比赛结束 🏁<br>进球明细：<br>  36' 裘德·贝林厄姆（英格兰） 头球  0-1<br>  38' 裘德·贝林厄姆（英格兰） 进球  0-2<br>  42' Julián Quiñones（墨西哥） 进球  1-2<br>  60' 哈里·凯恩（英格兰） 点球  1-3<br>  69' Raúl Jiménez（墨西哥） 点球  2-3",
}
resp3 = requests.post(url, json=payload3, headers=headers, timeout=10)
print(f"  状态：{resp3.status_code} - {resp3.json().get('msg')}")
time.sleep(1)

# 测试推送 4: 比分修正
print("发送测试推送 4：比分修正（进球取消）...")
payload4 = {
    "token": TOKEN,
    "title": "🔄 比分修正（进球取消）",
    "content": "墨西哥 vs 英格兰<br>当前比分：2 - 2 · 下半场<br>（英格兰 -1）<br>进球明细：<br>  36' 裘德·贝林厄姆（英格兰） 头球  0-1<br>  38' 裘德·贝林厄姆（英格兰） 进球  0-2<br>  42' Julián Quiñones（墨西哥） 进球  1-2",
}
resp4 = requests.post(url, json=payload4, headers=headers, timeout=10)
print(f"  状态：{resp4.status_code} - {resp4.json().get('msg')}")

print()
print("=" * 60)
print("✅ 所有测试推送已发送！")
print("请检查微信中的消息格式是否正确。")
print("=" * 60)
