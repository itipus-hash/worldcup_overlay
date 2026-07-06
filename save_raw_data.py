#!/usr/bin/env python3
"""
获取 2026 世界杯全量比赛数据（原始 API 数据，北京时间），
保存到 /tmp/raw_worldcup_data.txt，然后复制到桌面。
"""
import json
import urllib.request
from datetime import datetime, timedelta
import shutil

ESPN_API_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"

BEIJING_TZ = timedelta(hours=8)
TMP_PATH = "/tmp/raw_worldcup_data.txt"
OUTPUT_PATH = "/Users/fish/Desktop/raw_worldcup_data.txt"


def fetch_all_events():
    start = datetime(2026, 6, 11)
    end   = datetime(2026, 7, 20)
    utc_start = (start - timedelta(days=2)).strftime("%Y%m%d")
    utc_end   = (end   + timedelta(days=2)).strftime("%Y%m%d")

    url = f"{ESPN_API_BASE}?dates={utc_start}-{utc_end}&limit=100"
    print(f"正在获取 {utc_start}~{utc_end} 的数据...")

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    req.add_header("Accept", "application/json,text/plain,*/*")
    req.add_header("Referer", "https://www.espn.com/soccer/scoreboard")

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    events = data.get("events", [])
    print(f"获取到 {len(events)} 场比赛")
    return events, data


def parse_match(ev):
    """解析单场比赛，返回可读信息"""
    try:
        # 比赛 ID（顶层）
        match_id = ev.get("id", "N/A")

        # 比赛时间（顶层 UTC）
        utc_str = ev.get("date", "")
        bj_str = "N/A"
        if utc_str:
            utc_dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ")
            bj_dt = utc_dt + BEIJING_TZ
            bj_str = bj_dt.strftime("%Y-%m-%d %H:%M:%S")

        # 队伍信息（在 competitions[0].competitors 中）
        competitions = ev.get("competitions", [])
        if competitions:
            comp = competitions[0]
            competitors = comp.get("competitors", [])
            home = away = None
            for c in competitors:
                team = c.get("team", {})
                if c.get("homeAway") == "home":
                    home = team.get("displayName", "")
                elif c.get("homeAway") == "away":
                    away = team.get("displayName", "")

            # 比赛状态
            status_info = comp.get("status", {})
            status_type = status_info.get("type", {})
            status_state = status_type.get("state", "")
            status_detail = status_type.get("detail", "")
            status_short = status_type.get("shortDetail", "")
        else:
            home = away = None
            status_state = status_detail = status_short = "N/A"

        return {
            "match_id": match_id,
            "utc_time": utc_str,
            "beijing_time": bj_str,
            "home_team": home or "N/A",
            "away_team": away or "N/A",
            "status_type": status_state,
            "status_detail": status_detail,
            "status_desc": status_short,
            "raw_event": ev,
        }
    except Exception as e:
        return {"error": str(e), "raw_event": ev}


def main():
    events, raw_data = fetch_all_events()
    results = [parse_match(ev) for ev in events]

    with open(TMP_PATH, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write("2026 FIFA World Cup - ESPN 原始数据（北京时间）\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"比赛场数: {len(results)}\n")
        f.write("=" * 80 + "\n\n")

        for i, item in enumerate(results, 1):
            f.write(f"--- 比赛 {i} ---\n")
            f.write(f"  比赛 ID     : {item.get('match_id', 'N/A')}\n")
            f.write(f"  UTC 时间   : {item.get('utc_time', 'N/A')}\n")
            f.write(f"  北京时间   : {item.get('beijing_time', 'N/A')}\n")
            f.write(f"  主队        : {item.get('home_team', 'N/A')}\n")
            f.write(f"  客队        : {item.get('away_team', 'N/A')}\n")
            f.write(f"  状态        : {item.get('status_type', 'N/A')} - {item.get('status_detail', '')}\n")
            f.write(f"  状态描述    : {item.get('status_desc', 'N/A')}\n")
            f.write("  原始数据 (完整 JSON) :\n")
            f.write(json.dumps(item.get("raw_event"), ensure_ascii=False, indent=2))
            f.write("\n\n")

        # 附完整原始 API 响应
        f.write("\n" + "=" * 80 + "\n")
        f.write("完整原始 API 响应 (JSON)\n")
        f.write("=" * 80 + "\n")
        f.write(json.dumps(raw_data, ensure_ascii=False, indent=2))

    print(f"已保存到: {TMP_PATH}")
    
    # 尝试复制到桌面
    try:
        shutil.copy(TMP_PATH, OUTPUT_PATH)
        print(f"已复制到桌面: {OUTPUT_PATH}")
    except Exception as e:
        print(f"复制到桌面失败: {e}")
        print(f"请手动复制: cp {TMP_PATH} {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
