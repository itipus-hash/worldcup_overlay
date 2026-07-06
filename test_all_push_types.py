#!/usr/bin/env python3
"""
测试所有推送类型的格式
包括：比赛开始、比赛结束、进球推送、比分变化等
"""
import sys
import os

PROJECT_DIR = "/Users/fish/WorkBuddy/2026-07-03-16-00-57/worldcup_overlay"
sys.path.insert(0, PROJECT_DIR)

from main import WorldCupAPI, BEIJING_TZ, get_beijing_today

api = WorldCupAPI()

print("=" * 60)
print("测试所有推送类型的格式")
print("=" * 60)
print()

# 获取比赛
today = get_beijing_today()
matches, _ = api.fetch_matches_by_beijing_date(today)

if not matches:
    print("未找到比赛，使用模拟数据测试...")
    # 创建模拟比赛对象
    from main import Match
    m = Match()
    m.match_id = "test123"
    m.home_team = "巴西"
    m.away_team = "挪威"
    m.home_score = 1
    m.away_score = 2
    m.status = "finished"
    m.minute = ""
    matches = [m]

print(f"使用比赛：{matches[0].home_team} vs {matches[0].away_team}")
print()

m = matches[0]

# 测试 1: 比赛开始/进行中推送
print("【推送类型 1】比赛开始/进行中")
print("-" * 60)
title, content = api._format_match_push(m, "start") if hasattr(api, '_format_match_push') else ("", "")
# 直接从 main.py 导入 _format_match_push 的模拟
def simulate_format_start(m):
    stage_map = {"1h": "上半场", "2h": "下半场", "ht": "中场休息", "et": "加时", "pen": "点球大战", "live": "进行中"}
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
    
    if m.home_score == 0 and m.away_score == 0 and (not m.minute or m.minute <= 10):
        title = f"{m.home_team} VS {m.away_team} 比赛开始"
    else:
        title = f"{m.home_team} VS {m.away_team} {status_display(m)}"
    
    lines = [
        f"{status_display(m)} · {m.home_team} vs {m.away_team}",
        f"当前比分：{m.home_score} - {m.away_score}",
    ]
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
        except:
            pass
    content = "<br>".join(lines)
    return title, content

title, content = simulate_format_start(m)
print(f"TITLE: {title}")
print("CONTENT:")
for line in content.split("<br>"):
    print(f"  {line}")
print()

# 测试 2: 比赛结束推送
print("【推送类型 2】比赛结束")
print("-" * 60)
def simulate_format_end(m):
    title = f"{m.home_team} VS {m.away_team} 比赛结束"
    lines = [
        f"最终比分：{m.home_score} - {m.away_score}",
        "全场比赛结束 🏁",
    ]
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
        except:
            pass
    content = "<br>".join(lines)
    return title, content

title, content = simulate_format_end(m)
print(f"TITLE: {title}")
print("CONTENT:")
for line in content.split("<br>"):
    print(f"  {line}")
print()

# 测试 3: 进球推送
print("【推送类型 3】进球推送")
print("-" * 60)
def simulate_format_goal(m, home_delta=1, away_delta=0):
    scorer = "未知球员"
    method = "进球"
    minute = "42'"
    score_str = f"{m.home_score}-{m.away_score}"
    team_cn = ""
    
    try:
        summary = api.fetch_match_summary(m.match_id)
        if summary:
            last_goal = api.extract_last_goal_from_summary(summary, m)
            if last_goal:
                scorer = last_goal.get("scorer", scorer)
                method = last_goal.get("method", method)
                minute = last_goal.get("minute", minute)
                score_str = last_goal.get("score", score_str)
                team_cn = last_goal.get("team_cn", "")
    except:
        pass
    
    if team_cn:
        head_team = f"（{team_cn}）"
    else:
        head_team = ""
    
    if minute and minute not in ("0'", "0''"):
        title = f"⚽ {minute} {scorer}{head_team} {method}  {score_str}"
    else:
        title = f"⚽ {scorer}{head_team} {method}  {score_str}"
    
    # Content: 完整进球日志
    content_lines = [f"{m.home_team} {m.home_score}-{m.away_score} {m.away_team}  进球明细："]
    try:
        summary = api.fetch_match_summary(m.match_id)
        if summary:
            all_goals = api.extract_all_goals_from_summary(summary, m)
            if all_goals:
                for g in all_goals:
                    g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                    content_lines.append(f"  {g['minute']} {g['scorer']}{g_team} {g['method']}  {g['score']}")
    except:
        pass
    
    content = "<br>".join(content_lines)
    return title, content

title, content = simulate_format_goal(m)
print(f"TITLE: {title}")
print("CONTENT:")
for line in content.split("<br>"):
    print(f"  {line}")
print()

# 测试 4: 比分变化推送（非进球，比如比分修正）
print("【推送类型 4】比分变化（修正）")
print("-" * 60)
def simulate_format_score_change(m, home_delta=0, away_delta=-1):
    has_goal = home_delta > 0 or away_delta > 0
    has_correction = home_delta < 0 or away_delta < 0
    if has_goal and has_correction:
        title = "🔄 比分有调整"
    elif has_correction:
        title = "🔄 比分修正（进球取消）"
    else:
        title = "⚽ 进球啦！"
    
    delta_parts = []
    if home_delta != 0:
        delta_parts.append(f"{m.home_team} {home_delta:+d}")
    if away_delta != 0:
        delta_parts.append(f"{m.away_team} {away_delta:+d}")
    delta_line = "（" + " / ".join(delta_parts) + "）" if delta_parts else ""
    
    # 添加比赛进度
    stage_map = {"1h": "上半场", "2h": "下半场", "ht": "中场休息", "et": "加时赛", "pen": "点球大战", "live": "进行中"}
    stage_tag = ""
    if m.status in stage_map:
        stage_tag = f" · {stage_map[m.status]}"
    
    content = (
        f"{m.home_team} vs {m.away_team}<br>"
        f"当前比分：{m.home_score} - {m.away_score}{stage_tag}<br>"
        f"{delta_line}".rstrip()
    )
    return title, content

title, content = simulate_format_score_change(m, 0, -1)
print(f"TITLE: {title}")
print("CONTENT:")
for line in content.split("<br>"):
    print(f"  {line}")
print()

print("=" * 60)
print("所有推送类型测试完成")
print("=" * 60)
