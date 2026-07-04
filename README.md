# 2026 世界杯桌面悬浮窗

桌面悬浮窗，实时显示 2026 FIFA 世界杯赛程、比分和进球通知。

## 功能

- 赛程展示（昨天 + 今天 + 未来 20 天）
- 实时比分（比赛进行中时 30 秒自动刷新）
- 进球 / 加时 / 点球大战 / 乌龙球全标记
- PushPlus 推送（比赛开始、比分变化）
- 智能跳过：无比赛进行中且下场比赛 >30min 远时，不消耗 API
- 一次启动预加载 22 天数据（0 API 切换日期）
- macOS 通知 + 可选菜单栏图标

## 数据源

- [ESPN API](https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard) — 赛程和比分
- [ESPN summary endpoint](https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary) — 进球详情
- [PushPlus](https://www.pushplus.plus) — 微信推送

## 开发

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

## 打包

### macOS

```bash
.venv/bin/pyinstaller --noconfirm --clean WorldCupOverlay.spec
# 产物：dist/WorldCupOverlay.app
```

### Windows

PyInstaller 不能 cross-compile，需在 Windows 机器上：

```powershell
pip install -r requirements.txt
pyinstaller --noconfirm --clean WorldCupOverlay.spec
# 产物：dist\WorldCupOverlay\WorldCupOverlay.exe
```

或用 GitHub Actions（见 `.github/workflows/build-windows.yml`）自动构建。

## 系统要求

- macOS 10.13+ / Windows 10+
- Python 3.10+（仅开发需要；运行打包后的 .app/.exe 不需要）

## License

MIT
