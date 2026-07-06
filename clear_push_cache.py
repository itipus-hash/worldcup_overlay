#!/usr/bin/env python3
"""
清空世界杯 App 推送缓存
清除 last_pushed_score 和 end_pushed_ids，
让 app 下次启动时重新推送所有比赛通知。
"""
import json
import os
import sys
import subprocess
import time

SETTINGS_PATH = os.path.expanduser("~/.worldcup_overlay/settings.json")

def read_settings():
    with open(SETTINGS_PATH, "r") as f:
        return json.load(f)

def write_settings(d):
    # Remove extended attributes that may cause permission issues
    subprocess.run(["xattr", "-c", SETTINGS_PATH], capture_output=True)
    subprocess.run(["chmod", "u+w", SETTINGS_PATH], capture_output=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)

def format_cache(d):
    """Return a readable string of the push cache."""
    lines = []
    lps = d.get("last_pushed_score", {})
    end = d.get("end_pushed_ids", [])
    if lps:
        lines.append("已推送比分记录：")
        for mid, score in lps.items():
            lines.append(f"  {mid}: {score[0]}-{score[1]}")
    else:
        lines.append("已推送比分记录：（无）")
    lines.append(f"比赛结束推送记录：{len(end)} 场")
    return "\n".join(lines)

def main():
    print("=" * 50)
    print("世界杯 App 推送缓存清空工具")
    print("=" * 50)
    print()

    if not os.path.exists(SETTINGS_PATH):
        print(f"❌ 未找到设置文件：{SETTINGS_PATH}")
        print("请先运行世界杯 App 一次，让 app 创建设置文件。")
        print("\n3秒后自动关闭窗口...")
        time.sleep(3)
        sys.exit(1)

    # Step1: Show current cache
    try:
        d = read_settings()
    except Exception as e:
        print(f"❌ 读取设置文件失败：{e}")
        print("\n3秒后自动关闭窗口...")
        time.sleep(3)
        sys.exit(1)

    print("【当前推送缓存内容】")
    print(format_cache(d))
    print()

    # 检查缓存是否为空
    lps = d.get("last_pushed_score", {})
    end = d.get("end_pushed_ids", [])
    if not lps and not end:
        print("⚠️  当前缓存已经是空的，无需清空。")
        print("\n3秒后自动关闭窗口...")
        time.sleep(3)
        sys.exit(0)

    # Step2: Wait 3 seconds
    print("3秒后开始清空...")
    print("（如果不需要清空，请现在关闭终端窗口）")
    time.sleep(3)
    print()

    # Step3: Clear cache
    print("正在清空缓存...")
    d["last_pushed_score"] = {}
    d["end_pushed_ids"] = []
    try:
        write_settings(d)
        print("✅ 缓存已清空")
    except Exception as e:
        print(f"❌ 写入设置文件失败：{e}")
        print("\n3秒后自动关闭窗口...")
        time.sleep(3)
        sys.exit(1)

    # Step4: Re-read and show
    print("\n【清空后推送缓存内容】")
    try:
        d2 = read_settings()
        print(format_cache(d2))
    except Exception:
        print("（无法重新读取）")

    # Step5: Success
    print("\n" + "=" * 50)
    print("✅ 推送缓存清空成功！")
    print("=" * 50)
    print("\n下次打开 app 时会重新推送所有比赛通知。")
    print("\n3秒后自动关闭窗口...")
    time.sleep(3)

if __name__ == "__main__":
    main()
