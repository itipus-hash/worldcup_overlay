#!/usr/bin/env python3
"""
演示推送缓存的数据结构
"""
import json

# 模拟有数据时的 settings.json 结构
sample = {
    "refresh_interval": 60,
    "live_only": False,
    "is_pinned": False,
    "window_x": 945,
    "window_y": 61,
    "pushplus_token": "accae3466f374091b5b041c843f50e6e",
    "pushplus_on_start": True,
    "pushplus_on_goal": True,
    "end_pushed_ids": ["760505"],
    "last_pushed_score": {
        "760505": [2, 3]
    },
    "saved_at": 1783308481.2608798
}

print("=== 推送缓存数据结构（settings.json）===")
print(json.dumps(sample, indent=2, ensure_ascii=False))
print()
print("字段说明：")
print("  last_pushed_score: 字典")
print("    key   = match_id（字符串）")
print("    value = [主队比分, 客队比分]")
print("  end_pushed_ids: 列表，已推送'比赛结束'的 match_id")
print("  saved_at: 时间戳，>2小时则加载时自动清空")
print()
print("清空方式：直接把这两个字段设为空，不需要删除文件。")
print("删除文件会导致其他设置（窗口位置、PushPlus token等）也丢失。")
