#!/usr/bin/env python3
"""
测试推送内容生成
获取当前比赛，生成并打印推送的 title 和 content，
让用户检查生成的内容是否正确。
"""
import sys
import os

# 添加项目目录到 sys.path
PROJECT_DIR = "/Users/fish/WorkBuddy/2026-07-03-16-00-57/worldcup_overlay"
sys.path.insert(0, PROJECT_DIR)

from main import WorldCupAPI, BEIJING_TZ, get_beijing_today
import json

api = WorldCupAPI()

print("=" * 60)
print("获取当前比赛并生成推送内容")
print("=" * 60)
print()

# 获取今天北京时间的比赛
today = get_beijing_today()
print(f"查询日期（北京时间）：{today}")
print()

matches, has_data = api.fetch_matches_by_beijing_date(today)

if not matches:
    print("未找到比赛。尝试获取未来 3 天的比赛...")
    for i in range(1, 4):
        from datetime import timedelta
        date = (BEIJING_TZ.fromutc(BEIJING_TZ.localize(__import__('datetime').datetime.now())).replace(tzinfo=None) + timedelta(days=i)).strftime("%Y-%m-%d")
        matches, _ = api.fetch_matches_by_beijing_date(date)
        if matches:
            print(f"找到比赛：{date}")
            break

if not matches:
    print("❌ 未找到任何比赛。")
    sys.exit(1)

print(f"找到 {len(matches)} 场比赛：")
print()

# 模拟 _format_match_push 方法（从 main.py 提取）
def format_match_push(m, kind):
    """模拟 _format_match_push 方法"""
    stage_map = {
        "1h": "上半场",
        "2h": "下半场",
        "ht": "中场休息",
        "et": "加时",
        "pen": "点球大战",
        "live": "进行中",
    }
    
    # _status_display 模拟
    def status_display(m):
        stage = stage_map.get(m.status, "进行中")
        if m.status in ("ht", "pen"):
            return stage
        minute_raw = str(m.minute) if m.minute else ""
        if minute_raw and not minute_raw.endswith("'"):
            minute_raw = minute_raw + "'"
        if m.status == "et":
            return f"加时 {minute_raw}" if minute_raw else "加时赛"
        return f"{stage} {minute_raw}" if minute_raw else stage
    
    if kind == "start":
        if m.home_score == 0 and m.away_score == 0 and (not m.minute or m.minute <= 10):
            title = f"{m.home_team} VS {m.away_team} 比赛开始"
        else:
            title = f"{m.home_team} VS {m.away_team} {status_display(m)}"
        lines = [
            f"{status_display(m)} · {m.home_team} vs {m.away_team}",
            f"当前比分：{m.home_score} - {m.away_score}",
        ]
        # 获取进球明细
        if (m.home_score > 0 or m.away_score > 0):
            try:
                summary = api.fetch_match_summary(m.match_id)
                if summary:
                    all_goals = api.extract_all_goals_from_summary(summary, m)
                    if all_goals:
                        lines.append("进球明细：")
                        for g in all_goals:
                            g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                            lines.append(f"  {g['minute']} {g['scorer']}{g_team} {g['method']}  {g['score']}")
            except Exception as e:
                lines.append(f"（无法获取进球明细：{e}）")
        content = "<br>".join(lines)
    elif kind == "end":
        title = f"{m.home_team} VS {m.away_team} 比赛结束"
        content = f"最终比分：{m.home_score} - {m.away_score}<br>全场比赛结束 🏁"
    else:
        title = f"⚽ {status_display(m)} · {m.home_team} vs {m.away_team}"
        content = f"{status_display(m)} · 当前比分：{m.home_score} - {m.away_score}"
    
    return title, content

# 对每个比赛生成推送内容
for i, m in enumerate(matches):
    print(f"【比赛 {i+1}】{m.home_team} vs {m.away_team}")
    print(f"  状态：{m.status} | 比分：{m.home_score}-{m.away_score} | 分钟：{m.minute}")
    print()
    
    # 强制生成 "start" 类型的推送内容（包含进球明细）
    print("生成【比赛开始/进行中】推送内容（包含进球明细）：")
    title, content = format_match_push(m, "start")
    print(f"  TITLE: {title}")
    print(f"  CONTENT:")
    for line in content.split("<br>"):
        print(f"    {line}")
    print()
    print("-" * 60)
    print()
    
    # 如果是已结束的比赛，也生成 "end" 类型的推送内容
    if m.status == "finished":
        print("生成【比赛结束】推送内容：")
        title, content = format_match_push(m, "end")
        print(f"  TITLE: {title}")
        print(f"  CONTENT:")
        for line in content.split("<br>"):
            print(f"    {line}")
        print()
        print("=" * 60)
        print()

print("完成。")
