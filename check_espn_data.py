#!/usr/bin/env python3
"""
检查 ESPN summary API 的 keyEvents 数据结构
看看是否有字段标记进球被 VAR 取消
"""
import sys
import os
import json

PROJECT_DIR = "/Users/fish/WorkBuddy/2026-07-03-16-00-57/worldcup_overlay"
sys.path.insert(0, PROJECT_DIR)

from main import WorldCupAPI, BEIJING_TZ, get_beijing_today

api = WorldCupAPI()

# 获取今天的一场比赛（墨西哥 vs 英格兰）
today = get_beijing_today()
matches, _ = api.fetch_matches_by_beijing_date(today)

if not matches:
    print("未找到比赛")
    sys.exit(1)

# 找墨西哥 vs 英格兰
m = None
for match in matches:
    if "墨西哥" in match.home_team or "英格兰" in match.away_team:
        m = match
        break

if not m:
    print("未找到墨西哥 vs 英格兰")
    sys.exit(1)

print(f"比赛：{m.home_team} vs {m.away_team}")
print(f"Match ID：{m.match_id}")
print()

# 获取 summary
summary = api.fetch_match_summary(m.match_id)
if not summary:
    print("无法获取 summary")
    sys.exit(1)

print("=" * 80)
print("Key Events 完整数据（前10个）：")
print("=" * 80)
key_events = summary.get("keyEvents", [])
for i, event in enumerate(key_events[:15]):
    print(f"\n--- Event {i} ---")
    print(json.dumps(event, indent=2, ensure_ascii=False))
    
print()
print("=" * 80)
print("Scoring Plays（进球事件）详细信息：")
print("=" * 80)
scoring = [k for k in key_events if k.get("scoringPlay")]
for i, goal in enumerate(scoring):
    print(f"\n=== Goal {i} ===")
    print(f"  text: {goal.get('text', 'N/A')}")
    print(f"  shortText: {goal.get('shortText', 'N/A')}")
    print(f"  type: {goal.get('type', {})}")
    print(f"  period: {goal.get('period', {})}")
    print(f"  clock: {goal.get('clock', {})}")
    print(f"  scoringPlay: {goal.get('scoringPlay', 'N/A')}")
    # 检查是否有取消相关的字段
    print(f"  所有 keys: {list(goal.keys())}")
    # 检查一些可能的取消相关字段
    for key in ['cancelled', 'disallowed', 'var', 'review', 'reversed']:
        val = goal.get(key, 'N/A')
        if val != 'N/A':
            print(f"  *** {key}: {val}")

print()
print(f"\n总共有 {len(scoring)} 个 scoring plays")
