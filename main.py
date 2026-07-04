#!/usr/bin/env python3
"""
2026 FIFA World Cup Desktop Overlay
====================================
A floating, frameless, always-on-top widget showing today's World Cup matches.
Features: live scores, flags, match status, date navigation.
Data source: football-data.org v4 (free tier)
"""

import sys
import os
import json
import time
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QFrame, QComboBox, QSizePolicy,
    QGraphicsDropShadowEffect, QDialog, QDialogButtonBox, QSlider,
    QCalendarWidget, QMenu, QAction, QLineEdit, QCheckBox, QMessageBox,
    QSystemTrayIcon
)
from PyQt5.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QPoint, QUrl, QSize,
    QPropertyAnimation, QEasingCurve, QRect, QObject, QDate
)
from PyQt5.QtGui import (
    QFont, QColor, QPalette, QPixmap, QPainter, QBrush, QPen,
    QPainterPath, QFontDatabase, QIcon, QLinearGradient, QMovie
)

# ============================================================================
# Configuration
# ============================================================================

# ESPN's free public scoreboard API (no key required, real-time scores).
# Note: this is ESPN's internal page-feed API, not an official public API.
# We follow safe-calling practices: browser-like headers, >=30s refresh,
# 429 backoff, and only fetching the scoreboard endpoint.
ESPN_API_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
# Browser-like headers — default python-requests UA is flagged as a bot
# by ESPN's AWS WAF. Rotating to a real Chrome UA is the single most
# effective way to avoid 429s.
ESPN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "Referer": "https://www.espn.com/soccer/scoreboard",
    "Origin": "https://www.espn.com",
}
# Configuration paths - use script directory or user home
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_HOME_CONFIG = os.path.expanduser("~/.worldcup_overlay")
# Try home dir first, but fall back to script dir if not writable.
# We must explicitly probe writability — `os.makedirs(exist_ok=True)`
# on an already-existing-but-read-only dir succeeds (no-op), so a
# PermissionError would only surface later on first file open.
def _pick_config_dir() -> str:
    try:
        os.makedirs(_HOME_CONFIG, exist_ok=True)
        # Probe: can we actually create a file here?
        probe = os.path.join(_HOME_CONFIG, ".write_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return _HOME_CONFIG
    except (PermissionError, OSError):
        return os.path.join(SCRIPT_DIR, ".config")

CONFIG_DIR = _pick_config_dir()
os.makedirs(CONFIG_DIR, exist_ok=True)

FLAG_CACHE_DIR = os.path.join(CONFIG_DIR, "flags")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
LOCK_PATH = os.path.join(CONFIG_DIR, ".lock")

# App dimensions
WINDOW_WIDTH = 478
MATCH_CARD_MIN_HEIGHT = 120
WINDOW_MIN_HEIGHT = 200
WINDOW_MAX_HEIGHT = 700

# Colors (Dark theme)
COLOR_BG = QColor(22, 22, 28, 230)
COLOR_BG_GLASS = QColor(32, 32, 42, 225)
COLOR_CARD_BG = QColor(42, 42, 56, 210)
COLOR_CARD_BORDER = QColor(60, 60, 80, 100)
COLOR_TEXT_PRIMARY = QColor(240, 240, 255)
COLOR_TEXT_SECONDARY = QColor(160, 160, 180)
COLOR_ACCENT_LIVE = QColor(255, 59, 48)
COLOR_ACCENT_UPCOMING = QColor(0, 122, 255)
COLOR_ACCENT_FINISHED = QColor(120, 120, 140)
COLOR_HOME = QColor(100, 180, 255)
COLOR_AWAY = QColor(255, 140, 100)
COLOR_SCORE_BG = QColor(50, 50, 65)
COLOR_STAT_BG = QColor(35, 35, 48)
COLOR_DIVIDER = QColor(70, 70, 90)

# Refresh intervals (seconds). Per ESPN's unofficial API safety
# guidelines, refreshes should be at least 30s apart to avoid 429
# rate limiting. Hard floor: 30s. No 10/20s options.
REFRESH_INTERVALS = [30, 60, 120, 300]
DEFAULT_REFRESH = 30

# Stage type mapping
STAGE_NAMES = {
    "group": "小组赛",
    "r64": "64强赛",
    "r32": "32强赛",
    "r16": "16强赛",
    "qf": "八强赛",
    "sf": "半决赛",
    "third": "季军赛",
    "final": "🏆 决赛",
}

# English -> Chinese team name mapping (all 48 teams)
TEAM_NAMES_CN = {
    "Mexico": "墨西哥",
    "South Africa": "南非",
    "South Korea": "韩国",
    "Czech Republic": "捷克",
    "Czechia": "捷克",
    "Canada": "加拿大",
    "Bosnia and Herzegovina": "波黑",
    "Bosnia-Herzegovina": "波黑",
    "Qatar": "卡塔尔",
    "Switzerland": "瑞士",
    "Brazil": "巴西",
    "Morocco": "摩洛哥",
    "Haiti": "海地",
    "Scotland": "苏格兰",
    "United States": "美国",
    "Paraguay": "巴拉圭",
    "Australia": "澳大利亚",
    "Turkey": "土耳其",
    "Germany": "德国",
    "Curaçao": "库拉索",
    "Ivory Coast": "科特迪瓦",
    "Ecuador": "厄瓜多尔",
    "Netherlands": "荷兰",
    "Japan": "日本",
    "Sweden": "瑞典",
    "Tunisia": "突尼斯",
    "Belgium": "比利时",
    "Egypt": "埃及",
    "Iran": "伊朗",
    "New Zealand": "新西兰",
    "Spain": "西班牙",
    "Cape Verde": "佛得角",
    "Cape Verde Islands": "佛得角",
    "Saudi Arabia": "沙特阿拉伯",
    "Uruguay": "乌拉圭",
    "France": "法国",
    "Senegal": "塞内加尔",
    "Iraq": "伊拉克",
    "Norway": "挪威",
    "Argentina": "阿根廷",
    "Algeria": "阿尔及利亚",
    "Austria": "奥地利",
    "Jordan": "约旦",
    "Portugal": "葡萄牙",
    "Congo DR": "刚果民主共和国",
    "Democratic Republic of the Congo": "刚果民主共和国",
    "Uzbekistan": "乌兹别克斯坦",
    "Colombia": "哥伦比亚",
    "England": "英格兰",
    "Croatia": "克罗地亚",
    "Ghana": "加纳",
    "Panama": "巴拿马",
}

# English -> Chinese player name map. We only seed the names we've
# actually seen score in the 2026 World Cup so far; unknown names fall
# back to the English display name. Easy to extend.
PLAYER_NAMES_CN = {
    # Colombia
    "Jhon Arias": "霍恩·阿里亚斯",
    "Luis Díaz": "路易斯·迪亚斯",
    "James Rodríguez": "哈梅斯·罗德里格斯",
    "Rafael Santos Borré": "拉斐尔·桑托斯·博雷",
    "Miguel Borja": "米格尔·博尔哈",
    "Luis Suárez": "路易斯·苏亚雷斯",
    # Argentina
    "Lionel Messi": "里奥·梅西",
    "Lisandro Martínez": "利桑德罗·马丁内斯",
    "Alexis Mac Allister": "亚历克西斯·麦卡利斯特",
    "Julián Álvarez": "朱利安·阿尔瓦雷斯",
    "Lautaro Martínez": "劳塔罗·马丁内斯",
    "Ángel Di María": "安赫尔·迪马利亚",
    "Emiliano Martínez": "埃米利亚诺·马丁内斯",
    # Egypt
    "Mohamed Salah": "穆罕默德·萨拉赫",
    "Emam Ashour": "伊马姆·阿舒尔",
    "Karim Hafez": "卡里姆·哈菲兹",
    "Mohamed Hany": "穆罕默德·哈尼",
    "Mahmoud Saber": "马哈茂德·萨比尔",
    "Ramy Rabia": "拉米·拉比亚",
    "Hossam Abdelmaguid": "霍萨姆·阿卜杜勒马吉德",
    "Marwan Attia": "马万·阿提亚",
    # Australia
    "Harry Souttar": "哈里·索塔尔",
    "Jackson Irvine": "杰克逊·欧文",
    "Awer Mabil": "阿维尔·马比尔",
    "Lucas Herrington": "卢卡斯·赫灵顿",
    "Craig Goodwin": "克雷格·古德温",
    "Mathew Leckie": "马修·莱基",
    # Ghana
    "Marvin Senaya": "马尔文·塞纳亚",
    "Alidu Seidu": "阿里杜·塞杜",
    "Mohammed Kudus": "穆罕默德·库杜斯",
    "André Ayew": "安德烈·阿尤",
    "Jordan Ayew": "乔丹·阿尤",
    "Thomas Partey": "托马斯·帕尔特伊",
    # Cape Verde
    "Deroy Duarte": "德罗伊·杜阿尔特",
    "Ryan Mendes": "瑞安·门德斯",
    "Sidny Cabral": "西德尼·卡布拉尔",
    "Sidny Lopes Cabral": "西德尼·卡布拉尔",
    "Yannick Semedo": "亚尼克·塞梅多",
    "Diney Borges": "迪内伊·博尔热斯",
    # Spain
    "Lamine Yamal": "拉明·亚马尔",
    "Dani Olmo": "丹尼·奥尔莫",
    "Nico Williams": "尼科·威廉姆斯",
    "Fabián Ruiz": "法比安·鲁伊斯",
    "Álvaro Morata": "阿尔瓦罗·莫拉塔",
    # Portugal
    "Cristiano Ronaldo": "克里斯蒂亚诺·罗纳尔多",
    "Bruno Fernandes": "布鲁诺·费尔南德斯",
    "Bernardo Silva": "贝尔纳多·席尔瓦",
    "Rafael Leão": "拉斐尔·莱昂",
    "Gonçalo Ramos": "贡萨洛·拉莫斯",
    # Austria
    "Marko Arnautović": "马尔科·阿瑙托维奇",
    "David Alaba": "大卫·阿拉巴",
    # Switzerland
    "Breel Embolo": "布雷尔·恩博洛",
    "Xherdan Shaqiri": "哲尔丹·沙奇里",
    "Granit Xhaka": "格拉尼特·扎卡",
    # Algeria
    "Riyad Mahrez": "里亚德·马赫雷斯",
    "Islam Slimani": "伊斯兰·斯利马尼",
    # Croatia
    "Luka Modrić": "卢卡·莫德里奇",
    "Ivan Perišić": "伊万·佩里西奇",
    "Joško Gvardiol": "约斯科·格瓦迪奥尔",
    # France
    "Kylian Mbappé": "基利安·姆巴佩",
    "Antoine Griezmann": "安托万·格里兹曼",
    "Ousmane Dembélé": "奥斯曼·登贝莱",
    "Aurélien Tchouaméni": "奥雷连·丘阿梅尼",
    # Norway
    "Erling Haaland": "埃尔林·哈兰德",
    "Martin Ødegaard": "马丁·厄德高",
    # Mexico
    "Hirving Lozano": "欧文·洛萨诺",
    "Santiago Giménez": "圣地亚哥·希门尼斯",
    # Canada
    "Alphonso Davies": "阿方索·戴维斯",
    "Jonathan David": "乔纳森·戴维",
    # Morocco
    "Achraf Hakimi": "阿什拉夫·哈基米",
    "Hakim Ziyech": "哈基姆·齐耶赫",
    "Youssef En-Nesyri": "优素福·恩内斯里",
    # Brazil
    "Vinícius Júnior": "维尼修斯·儒尼奥尔",
    "Rodrygo": "罗德里戈",
    "Raphinha": "拉菲尼亚",
    # England
    "Jude Bellingham": "裘德·贝林厄姆",
    "Harry Kane": "哈里·凯恩",
    "Phil Foden": "菲尔·福登",
    "Bukayo Saka": "布卡约·萨卡",
}

# ISO2 country codes for flagcdn.com (flag fallback)
TEAM_ISO2 = {
    "Mexico": "mx", "South Africa": "za", "South Korea": "kr",
    "Czech Republic": "cz", "Canada": "ca", "Bosnia and Herzegovina": "ba",
    "Bosnia-Herzegovina": "ba", "Qatar": "qa", "Switzerland": "ch",
    "Brazil": "br", "Morocco": "ma", "Haiti": "ht", "Scotland": "gb-sct",
    "United States": "us", "Paraguay": "py", "Australia": "au",
    "Turkey": "tr", "Germany": "de", "Curaçao": "cw", "Ivory Coast": "ci",
    "Ecuador": "ec", "Netherlands": "nl", "Japan": "jp", "Sweden": "se",
    "Tunisia": "tn", "Belgium": "be", "Egypt": "eg", "Iran": "ir",
    "New Zealand": "nz", "Spain": "es", "Cape Verde": "cv",
    "Cape Verde Islands": "cv", "Saudi Arabia": "sa", "Uruguay": "uy",
    "France": "fr", "Senegal": "sn", "Iraq": "iq", "Norway": "no",
    "Argentina": "ar", "Algeria": "dz", "Austria": "at", "Jordan": "jo",
    "Portugal": "pt", "Democratic Republic of the Congo": "cd",
    "Congo DR": "cd", "Uzbekistan": "uz", "Colombia": "co",
    "England": "gb-eng", "Croatia": "hr", "Ghana": "gh", "Panama": "pa",
    "Czechia": "cz",
}

# Beijing timezone
BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_today() -> str:
    """Get today's date in MM/DD/YYYY format in Beijing time."""
    return datetime.now(BEIJING_TZ).strftime("%m/%d/%Y")


def compare_beijing_dates(a: str, b: str) -> int:
    """Compare two MM/DD/YYYY date strings. Returns -1 / 0 / 1."""
    try:
        ma, da, ya = a.split("/")
        mb, db, yb = b.split("/")
        da_t = datetime(int(ya), int(ma), int(da))
        db_t = datetime(int(yb), int(mb), int(db))
        if da_t < db_t:
            return -1
        if da_t > db_t:
            return 1
        return 0
    except Exception:
        return 0


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class Match:
    """Represents a single World Cup match."""
    match_id: str
    home_team: str
    away_team: str
    home_flag: str
    away_flag: str
    home_score: int
    away_score: int
    home_penalty: int = 0
    away_penalty: int = 0
    home_extra_time: int = 0  # goals scored in extra time (period 3 + 4)
    away_extra_time: int = 0
    has_extra_time: bool = False  # match went to extra time
    status: str = "notstarted"       # notstarted | 1h | ht | 2h | et | pen | finished
    status_text: str = ""            # Human-readable status
    minute: str = ""                 # e.g. "45+2"
    group: str = ""
    stage: str = "group"
    local_date: str = ""
    finished: bool = False
    has_penalties: bool = False


# ============================================================================
# API Client
# ============================================================================

class WorldCupAPI:
    """Client for ESPN's free public scoreboard API.

    No API key required. Provides real-time scores for the FIFA World Cup.
    Docs: https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard
    """

    # ESPN season slug -> internal stage type
    ESPN_STAGE_MAP = {
        "group-stage": "group",
        "round-of-32": "r32",
        "round-of-16": "r16",
        "quarterfinals": "qf",
        "semifinals": "sf",
        "third-place": "third",
        "final": "final",
    }

    # Pseudo-team placeholders that ESPN uses in bracket/later-round
    # matches before the actual teams are decided. Translated here so
    # they don't show up in English ("RD16 W1", "SF L1", etc.) to the
    # user.
    BRACKET_TEAM_MAP = {
        # Round of 16
        "RD16 W1": "16强赛胜者1", "RD16 W2": "16强赛胜者2",
        "RD16 W3": "16强赛胜者3", "RD16 W4": "16强赛胜者4",
        "RD16 W5": "16强赛胜者5", "RD16 W6": "16强赛胜者6",
        "RD16 W7": "16强赛胜者7", "RD16 W8": "16强赛胜者8",
        # ESPN full-name form (for 7/19 final and onwards)
        "Round of 16 1 Winner": "16强赛胜者1",
        "Round of 16 2 Winner": "16强赛胜者2",
        "Round of 16 3 Winner": "16强赛胜者3",
        "Round of 16 4 Winner": "16强赛胜者4",
        "Round of 16 5 Winner": "16强赛胜者5",
        "Round of 16 6 Winner": "16强赛胜者6",
        "Round of 16 7 Winner": "16强赛胜者7",
        "Round of 16 8 Winner": "16强赛胜者8",
        # Quarterfinals
        "QFW1": "八强赛胜者1", "QFW2": "八强赛胜者2",
        "QFW3": "八强赛胜者3", "QFW4": "八强赛胜者4",
        "Quarterfinal 1 Winner": "八强赛胜者1",
        "Quarterfinal 2 Winner": "八强赛胜者2",
        "Quarterfinal 3 Winner": "八强赛胜者3",
        "Quarterfinal 4 Winner": "八强赛胜者4",
        "QW4": "八强赛胜者4",  # alternate form
        # Semifinals
        "SF L1": "半决赛1负者", "SF L2": "半决赛2负者",
        "SFW1": "半决赛胜者1", "SFW2": "半决赛胜者2",
        "Semifinal 1 Loser": "半决赛1负者",
        "Semifinal 2 Loser": "半决赛2负者",
        "Semifinal 1 Winner": "半决赛胜者1",
        "Semifinal 2 Winner": "半决赛胜者2",
    }

    def __init__(self):
        self._cache_fetched: bool = False
        self._request_times: list = []
        self._rate_limited_until: float = 0
        self._consecutive_429s: int = 0

    # ---- Quota management (lightweight, ESPN allows many reqs) ----

    def _can_make_request(self) -> bool:
        now = time.time()
        if now < self._rate_limited_until:
            return False
        self._request_times = [t for t in self._request_times if now - t < 60]
        # Conservative cap: 2 req/min per IP, well under ESPN's threshold.
        # One refresh today pulls (today + yesterday) = 2 calls, then we
        # wait 60s for the next refresh. For past/future dates there's
        # 1 call per refresh. This is the lowest rate that still
        # gives the user a real-time feel.
        return len(self._request_times) < 2

    def force_allow(self):
        """Clear all recent request timestamps so the next call is
        always allowed. Used by the PushPlus test push (one burst)."""
        self._request_times = []

    def _record_request(self):
        self._request_times.append(time.time())

    def is_rate_limited(self) -> bool:
        return time.time() < self._rate_limited_until

    def _record_429(self, retry_after: int = None):
        """Record a 429 response. Apply exponential backoff so we don't
        keep hammering ESPN during a rate-limit window."""
        # Use Retry-After header if provided, else exponential
        # backoff based on recent 429s. Cap at 30 minutes.
        if retry_after is None:
            retry_after = 60
        retry_after = max(60, min(retry_after, 1800))
        self._rate_limited_until = time.time() + retry_after
        self._consecutive_429s = getattr(self, "_consecutive_429s", 0) + 1

    def _record_success(self):
        """Reset the 429 counter on any successful call."""
        self._consecutive_429s = 0

    # ---- Data fetching ----

    def _fetch_today(self) -> list:
        """Fetch today's matches from ESPN. Returns raw event list.

        We deliberately do NOT cache today's list because scores change
        every refresh — caching would defeat the point of switching APIs.
        """
        if not self._can_make_request():
            return []
        try:
            self._record_request()
            resp = requests.get(
                ESPN_API_BASE,
                headers=ESPN_HEADERS,
                timeout=15,
            )
            if resp.status_code == 200:
                self._record_success()
                self._cache_fetched = True
                return resp.json().get("events", []) or []
            elif resp.status_code in (429, 503):
                # Honor Retry-After if provided, else exponential backoff
                retry_after = None
                try:
                    ra = resp.headers.get("Retry-After")
                    if ra:
                        retry_after = int(ra)
                except (ValueError, TypeError):
                    pass
                self._record_429(retry_after)
                return []
            return []
        except Exception:
            return []

    def _fetch_for_date(self, date_str_yyyymmdd: str) -> list:
        """Fetch matches for a specific date. date_str format: YYYYMMDD."""
        if not self._can_make_request():
            return []
        try:
            self._record_request()
            resp = requests.get(
                ESPN_API_BASE,
                params={"dates": date_str_yyyymmdd},
                headers=ESPN_HEADERS,
                timeout=15,
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json().get("events", []) or []
            elif resp.status_code in (429, 503):
                retry_after = None
                try:
                    ra = resp.headers.get("Retry-After")
                    if ra:
                        retry_after = int(ra)
                except (ValueError, TypeError):
                    pass
                self._record_429(retry_after)
                return []
            return []
        except Exception:
            return []

    def _fetch_for_date_range(self, start_yyyymmdd: str, end_yyyymmdd: str) -> list:
        """Fetch all events in the inclusive date range. Uses a SINGLE
        API call. date format: YYYYMMDD. Returns raw event list."""
        if not self._can_make_request():
            return []
        try:
            self._record_request()
            range_param = f"{start_yyyymmdd}-{end_yyyymmdd}"
            resp = requests.get(
                ESPN_API_BASE,
                params={"dates": range_param},
                headers=ESPN_HEADERS,
                timeout=15,
            )
            if resp.status_code == 200:
                self._record_success()
                return resp.json().get("events", []) or []
            elif resp.status_code in (429, 503):
                retry_after = None
                try:
                    ra = resp.headers.get("Retry-After")
                    if ra:
                        retry_after = int(ra)
                except (ValueError, TypeError):
                    pass
                self._record_429(retry_after)
                return []
            return []
        except Exception:
            return []

    def fetch_match_summary(self, event_id: str) -> dict:
        """Fetch the per-match summary endpoint for goal detail info.
        Returns the raw JSON dict, or {} on any failure. Cached for
        30s per event_id to avoid hammering the endpoint on every
        refresh tick.
        """
        if not event_id:
            return {}
        if not hasattr(self, "_summary_cache"):
            self._summary_cache = {}  # event_id -> (fetched_at, dict)
        now = time.time()
        cached = self._summary_cache.get(event_id)
        if cached and now - cached[0] < 30:
            return cached[1]
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/summary?event={event_id}"
            resp = requests.get(url, headers=ESPN_HEADERS, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                self._summary_cache[event_id] = (now, data)
                return data
        except Exception:
            pass
        return {}

    def extract_last_goal_from_summary(self, summary: dict, m) -> dict:
        """Pick the most recent goal from the summary's keyEvents and
        translate it to a compact dict for the push title.

        Returns a dict with keys:
            scorer, team, team_cn, method, minute, score
        or {} if no goal can be determined.

        Translation table for method (based on ESPN type.id / type.type):
            70  goal         → 普通进球
            137 goal-header  → 头球
            138 goal-penalty → 点球 (regular time penalty)
            139 goal-kick    → ?
            140 goal-free-kick → 任意球
            97  own-goal     → 乌龙球
            (period.number == 5 with shootout flag) → 点球大战进球
        Anything else → "进球"
        """
        if not summary:
            return {}
        key_events = summary.get("keyEvents") or []
        scoring = [k for k in key_events if k.get("scoringPlay")]
        if not scoring:
            return {}
        last = scoring[-1]
        # Try to figure out which side scored based on team.id
        team_obj = last.get("team") or {}
        team_id = str(team_obj.get("id", ""))
        team_name = team_obj.get("displayName") or ""
        # Translate to Chinese (e.g. "Colombia" → "哥伦比亚", "Ghana" → "加纳")
        team_cn = self._resolve_team_name(team_name) if team_name else ""
        is_home = None
        is_away = None
        if team_name and m.home_team and (team_name in m.home_team or m.home_team in team_name):
            is_home = True
            is_away = False
        elif team_name and m.away_team and (team_name in m.away_team or m.away_team in team_name):
            is_home = False
            is_away = True

        # Method
        tp = last.get("type") or {}
        tp_id = str(tp.get("id", ""))
        tp_type = tp.get("type", "")
        period_num = (last.get("period") or {}).get("number", 0)
        if tp_id == "137":
            method = "头球"
        elif tp_id == "138" or "penalty" in tp_type:
            method = "点球"
        elif tp_id == "97":
            method = "乌龙球"
        elif tp_id == "140" or "free-kick" in tp_type or "free kick" in tp_type:
            method = "任意球"
        elif tp_id == "70":
            method = "进球"
        else:
            method = tp.get("text") or "进球"

        # Shootout detection: if a goal is marked in period 5 OR if
        # the summary has a 'shootout' block, this goal is a PK goal
        if period_num == 5 or last.get("shootout"):
            method = "点球大战"

        # Scorer name — try to translate to Chinese via known player
        # map, fall back to the English display name.
        scorer_en = ""
        parts = last.get("participants") or []
        if parts and parts[0].get("athlete"):
            scorer_en = parts[0]["athlete"].get("displayName") or ""
        if not scorer_en:
            short = last.get("shortText") or ""
            # "Jhon Arias Goal" → "Jhon Arias"
            scorer_en = short.replace(" Goal", "").replace(" Own Goal", "").strip()
        scorer = PLAYER_NAMES_CN.get(scorer_en, scorer_en) if scorer_en else "未知球员"

        # Minute / clock
        clock = last.get("clock") or {}
        minute = clock.get("displayValue") or f"{period_num * 45}'"

        # Cumulative score (read from the goal's text if available —
        # the "X, Y" pattern is reliable)
        cumulative = ""
        goal_text = last.get("text") or ""
        # e.g. "Goal! Australia 0, Egypt 1."
        import re as _re
        m_score = _re.search(r"(\d+)[,\s]+(\d+)", goal_text)
        if m_score:
            cumulative = f"{m_score.group(1)}-{m_score.group(2)}"
        else:
            cumulative = f"{m.home_score}-{m.away_score}"

        return {
            "scorer": scorer or "未知球员",
            "scorer_en": scorer_en,
            "team": team_name,
            "team_cn": team_cn or team_name,
            "is_home": is_home,
            "is_away": is_away,
            "method": method,
            "minute": minute,
            "score": cumulative,
        }

    def extract_all_goals_from_summary(self, summary: dict, m) -> list:
        """Return ALL scoring events in the match, in chronological order,
        each as a dict with the same shape as extract_last_goal_from_summary.

        Used for the goal-push DETAIL line: "14' 阿里亚斯（哥伦比亚）头球 1-0;
        28' 萨拉赫（埃及）点球 1-1 ...".
        """
        if not summary:
            return []
        key_events = summary.get("keyEvents") or []
        scoring = [k for k in key_events if k.get("scoringPlay")]
        if not scoring:
            return []
        out = []
        # Temporarily swap `last` in extract_last_goal_from_summary by
        # calling once per event with a tiny monkey-patch. Simpler:
        # duplicate the logic inline. The duplication is small.
        import re as _re
        for k in scoring:
            team_obj = k.get("team") or {}
            team_name = team_obj.get("displayName") or ""
            team_cn = self._resolve_team_name(team_name) if team_name else ""
            tp = k.get("type") or {}
            tp_id = str(tp.get("id", ""))
            tp_type = tp.get("type", "")
            period_num = (k.get("period") or {}).get("number", 0)
            if tp_id == "137":
                method = "头球"
            elif tp_id == "138" or "penalty" in tp_type:
                method = "点球"
            elif tp_id == "97":
                method = "乌龙球"
            elif tp_id == "140" or "free-kick" in tp_type or "free kick" in tp_type:
                method = "任意球"
            elif tp_id == "70":
                method = "进球"
            else:
                method = tp.get("text") or "进球"
            if period_num == 5 or k.get("shootout"):
                method = "点球大战"
            scorer_en = ""
            parts = k.get("participants") or []
            if parts and parts[0].get("athlete"):
                scorer_en = parts[0]["athlete"].get("displayName") or ""
            if not scorer_en:
                short = k.get("shortText") or ""
                scorer_en = short.replace(" Goal", "").replace(" Own Goal", "").strip()
            scorer = PLAYER_NAMES_CN.get(scorer_en, scorer_en) if scorer_en else "未知球员"
            clock = k.get("clock") or {}
            minute = clock.get("displayValue") or f"{period_num * 45}'"
            cumulative = ""
            goal_text = k.get("text") or ""
            m_score = _re.search(r"(\d+)[,\s]+(\d+)", goal_text)
            if m_score:
                cumulative = f"{m_score.group(1)}-{m_score.group(2)}"
            else:
                cumulative = f"{m.home_score}-{m.away_score}"
            out.append({
                "scorer": scorer,
                "scorer_en": scorer_en,
                "team_cn": team_cn or team_name,
                "method": method,
                "minute": minute,
                "score": cumulative,
            })
        return out

    def extract_et_scores_from_summary(self, summary: dict) -> tuple:
        """Count goals in extra time (period 3 + period 4) per team.

        Returns (home_et, away_et, has_et_goals). The summary endpoint
        uses the home team's competitor[0] order, so we can tell home
        vs away by comparing team.id against summary.header.
        Returns (0, 0, False) if no ET goals can be determined.
        """
        if not summary:
            return 0, 0, False
        key_events = summary.get("keyEvents") or []
        et_goals = [k for k in key_events
                    if k.get("scoringPlay")
                    and (k.get("period") or {}).get("number") in (3, 4)]
        if not et_goals:
            return 0, 0, False
        # Get home team id from the summary header
        home_id = None
        hdr = summary.get("header") or {}
        comp = (hdr.get("competitions") or [{}])[0]
        for c in (comp.get("competitors") or []):
            if c.get("homeAway") == "home":
                home_id = str(c.get("id", ""))
                break
        if home_id is None and (comp.get("competitors") or []):
            home_id = str((comp["competitors"][0] or {}).get("id", ""))
        home_et = 0
        away_et = 0
        for g in et_goals:
            gid = str((g.get("team") or {}).get("id", ""))
            if home_id and gid == home_id:
                home_et += 1
            else:
                away_et += 1
        return home_et, away_et, True

    # ---- UTC -> Beijing time ----

    @staticmethod
    def _utc_to_beijing(utc_str: str) -> dict:
        try:
            utc_dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
            bj_dt = utc_dt.astimezone(BEIJING_TZ)
            return {
                "date": bj_dt.strftime("%m/%d/%Y"),
                "time": bj_dt.strftime("%H:%M"),
                "datetime": bj_dt,
            }
        except Exception:
            return {"date": "", "time": "", "datetime": None}

    # ---- Status parsing ----

    @staticmethod
    def _parse_espn_status(event: dict) -> tuple:
        """Map an ESPN event to (status_key, status_text, minute).

        ESPN's status.type.id values for soccer:
        1   = scheduled (pre)
        2   = in progress (1st half)
        17  = in progress (2nd half)  (some leagues)
        21  = halftime
        25  = extra time (1st half of ET)
        26  = extra time (2nd half of ET)
        47  = final, after penalties
        48  = final, after extra time
        28  = full time (regular)
        period=1 -> 1st half, period=2 -> 2nd half,
        period=3 -> ET 1st half, period=4 -> ET 2nd half,
        period=5 -> penalty shootout.
        """
        status = event.get("status", {}) or {}
        stype = status.get("type", {}) or {}
        sid = stype.get("id", "")
        state = stype.get("state", "")
        detail = stype.get("shortDetail", "") or stype.get("detail", "")
        desc = stype.get("description", "")
        clock = status.get("displayClock", "")
        period = status.get("period", 0) or 0

        if state == "pre":
            return ("notstarted", "即将开始", "")

        if state == "post":
            if sid == "47" or "Pen" in desc or "Pens" in detail:
                return ("finished", "已结束（点球）", "")
            # id=45 is "Final Score - After Extra Time" (AET).
            # id=48 is a generic "Final - After Extra Time" placeholder.
            if sid == "45" or sid == "48" or "Extra" in desc or "AET" in detail:
                return ("finished", "已结束（加时）", "")
            return ("finished", "已结束", "")

        # state == "in" (live)
        if "Halftime" in desc or "Half-Time" in desc or "Half Time" in desc:
            return ("ht", "⏸ 中场休息", "")
        if "Penalty" in desc or "Pens" in detail:
            return ("pen", "🎯 点球大战", clock)
        if "Extra" in desc:
            return ("et", f"⏱ 加时{clock}", clock)
        if period == 1:
            return ("1h", f"⚽ 上半场 {clock}'", clock)
        if period == 2:
            return ("2h", f"⚽ 下半场 {clock}'", clock)
        if period == 3:
            return ("et", f"⏱ 加时上半场 {clock}'", clock)
        if period == 4:
            return ("et", f"⏱ 加时下半场 {clock}'", clock)
        if period == 5:
            return ("pen", "🎯 点球大战", clock)
        # Fallback for in-progress with unknown period
        return ("live", f"⚽ {detail or '进行中'}", clock)

    # ---- Team helpers ----

    @staticmethod
    def _resolve_team_name(en_name: str) -> str:
        if not en_name or en_name == "None":
            return "待定"
        return TEAM_NAMES_CN.get(en_name, en_name)

    @staticmethod
    def _get_flag_url(team_dict: dict) -> str:
        """Get flag URL for a team.
        ESPN team dicts have a `logos` array; we still prefer flagcdn
        for actual country flags (consistency with previous look)."""
        name = team_dict.get("displayName") or team_dict.get("name") or ""
        iso2 = TEAM_ISO2.get(name, "")
        if iso2:
            return f"https://flagcdn.com/w160/{iso2}.png"
        # Fallback: ESPN's team logo
        logos = team_dict.get("logos") or []
        if logos:
            return logos[0].get("href", "")
        return ""

    @staticmethod
    def _safe_int(val, default=0) -> int:
        if val is None:
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    # ---- Match building ----

    def _build_match(self, espn_event: dict) -> Match:
        comp = (espn_event.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        if len(competitors) < 2:
            return None

        # ESPN puts home first by `order` (0) and away second; or by
        # homeAway field. We use homeAway for safety.
        home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])
        home_team_obj = home.get("team", {}) or {}
        away_team_obj = away.get("team", {}) or {}

        home_team_en = home_team_obj.get("displayName") or home_team_obj.get("name") or ""
        away_team_en = away_team_obj.get("displayName") or away_team_obj.get("name") or ""

        # Translate bracket placeholders (e.g. "RD16 W1", "SF L1",
        # "Semifinal 1 Loser") to Chinese. ESPN emits these for
        # knockout matches whose participants aren't yet decided.
        home_team_en = self.BRACKET_TEAM_MAP.get(home_team_en, home_team_en)
        away_team_en = self.BRACKET_TEAM_MAP.get(away_team_en, away_team_en)

        # Score: ESPN gives a string for live matches (e.g. "1") and
        # an int for completed. We coerce.
        home_score = self._safe_int(home.get("score"), 0)
        away_score = self._safe_int(away.get("score"), 0)

        # Penalties (shootout)
        home_pen = self._safe_int(home.get("shootoutScore"), 0)
        away_pen = self._safe_int(away.get("shootoutScore"), 0)
        has_pen = home_pen > 0 or away_pen > 0

        status_key, status_text, minute = self._parse_espn_status(espn_event)

        # Extra time score: the scoreboard endpoint doesn't return
        # keyEvents, so we can't reliably count ET goals from it. The
        # summary endpoint does — we'll resolve ET counts lazily via
        # the per-event summary cache, but the *card* (which renders
        # before the summary is fetched) needs a placeholder. So
        # we initialise to 0 here and let _enrich_match_with_et_scores
        # fill them in if a summary becomes available.
        home_et = 0
        away_et = 0
        has_et = ("加时" in status_text) or (status_key == "et")

        # Date/time -> Beijing. Use competition.startDate (always
        # populated) or event.date as fallback.
        date_src = comp.get("startDate") or espn_event.get("date") or ""
        bj = self._utc_to_beijing(date_src)

        # Stage: season.slug like "round-of-16", "group-stage", "final"
        season = espn_event.get("season", {}) or {}
        season_slug = season.get("slug", "group-stage")
        stage = self.ESPN_STAGE_MAP.get(season_slug, "group")

        # Group: comes from competition.altGameNote like "FIFA World Cup, Group H"
        group_name = ""
        alt_note = comp.get("altGameNote", "") or ""
        if "Group " in alt_note:
            try:
                group_name = alt_note.split("Group ", 1)[1].strip()
            except Exception:
                group_name = ""
        if stage != "group":
            group_name = ""

        local_date = f"{bj['date']} {bj['time']}" if bj["date"] else ""

        return Match(
            match_id=str(espn_event.get("id", "")),
            home_team=self._resolve_team_name(home_team_en),
            away_team=self._resolve_team_name(away_team_en),
            home_flag=self._get_flag_url(home_team_obj),
            away_flag=self._get_flag_url(away_team_obj),
            home_score=home_score,
            away_score=away_score,
            home_penalty=home_pen,
            away_penalty=away_pen,
            home_extra_time=home_et,
            away_extra_time=away_et,
            has_extra_time=has_et,
            status=status_key,
            status_text=status_text,
            minute=minute,
            group=group_name,
            stage=stage,
            local_date=local_date,
            finished=(status_key == "finished"),
            has_penalties=has_pen,
        )

    def _fetch_for_bj_date(self, date_str_mmddyyyy: str) -> list:
        """Fetch events whose Beijing local date matches the given date.

        ESPN stores dates in UTC; a Beijing date can span two adjacent
        UTC days. We pull a 3-day UTC range that fully covers the
        target BJ date plus its neighbours, then filter in-memory by
        the BJ local date.

        This is needed because ESPN's un-parameterized / "today" endpoint
        returns the *current UTC* date's events, which is often the
        PREVIOUS Beijing date from a Chinese user's perspective.

        Returns the raw event list (un-filtered to allow callers to
        bucket across days if needed) — actually we DO filter here
        to keep callers simple.
        """
        try:
            m, d, y = date_str_mmddyyyy.split("/")
            bj_target = f"{int(m):02d}/{int(d):02d}/{int(y):04d}"
            bj_dt = datetime(int(y), int(m), int(d))
        except Exception:
            return []

        # Pull a 3-day UTC range around the BJ date: BJ date -1 .. BJ date +1
        # is sufficient because the max offset is 16 hours (BJ is UTC+8),
        # so a BJ day's events are always within those three UTC days.
        utc_start = (bj_dt - timedelta(days=1)).strftime("%Y%m%d")
        utc_end = (bj_dt + timedelta(days=1)).strftime("%Y%m%d")

        events = self._fetch_for_date_range(utc_start, utc_end)
        if not events:
            return []

        # Filter to events whose Beijing local date == bj_target.
        # _build_match produces local_date in MM/DD/YYYY HH:MM format.
        # We do a lightweight pre-filter using the raw UTC date to
        # avoid building a Match for obviously-wrong days.
        # But simpler: build all, then filter.
        out = []
        for ev in events:
            comp = (ev.get("competitions") or [{}])[0]
            date_src = comp.get("startDate") or ev.get("date") or ""
            bj = self._utc_to_beijing(date_src)
            if bj.get("date") == bj_target:
                out.append(ev)
        return out

    # ---- Public API ----

    def fetch_matches_by_beijing_date(self, date_str: str) -> tuple:
        """Returns (matches, api_success).
        date_str: 'MM/DD/YYYY' (Beijing date, used as display key).
        """
        # We always go through _fetch_for_bj_date, which uses an
        # inclusive date range so a Beijing date that straddles two
        # UTC days is still fully covered. (The old code used the
        # un-parameterized ESPN endpoint for "today", which returns
        # the *UTC* current date — wrong from a Beijing perspective.)
        events = self._fetch_for_bj_date(date_str)

        api_success = bool(events) or self._cache_fetched

        results = []
        for ev in events:
            m = self._build_match(ev)
            if m is not None:
                results.append(m)

        return results, api_success

    def fetch_today_matches(self) -> list:
        matches, _ = self.fetch_matches_by_beijing_date(get_beijing_today())
        return matches


# ============================================================================
# Flag Image Loader (async)
# ============================================================================

class FlagDownloadWorker(QThread):
    """Background thread for downloading a single flag image."""
    flag_downloaded = pyqtSignal(str, QPixmap)  # url, pixmap (empty if failed)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            resp = requests.get(self.url, timeout=10)
            if resp.status_code == 200:
                filename = self.url.split("/")[-1]
                local_path = os.path.join(FLAG_CACHE_DIR, filename)
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                pixmap = QPixmap()
                pixmap.loadFromData(resp.content)
                self.flag_downloaded.emit(self.url, pixmap)
                return
        except Exception:
            pass
        self.flag_downloaded.emit(self.url, QPixmap())


class FlagLoader(QObject):
    """Loads and caches flag images. Downloads asynchronously."""

    flag_ready = pyqtSignal(str)  # emits URL when a flag becomes available

    def __init__(self):
        super().__init__()
        os.makedirs(FLAG_CACHE_DIR, exist_ok=True)
        self._cache = {}
        self._workers = []

    def get_flag(self, url: str) -> Optional[QPixmap]:
        """Get flag pixmap. Returns None if not cached (will download async)."""
        if not url:
            return None

        if url in self._cache:
            return self._cache[url]

        # Check local file cache
        filename = url.split("/")[-1]
        local_path = os.path.join(FLAG_CACHE_DIR, filename)
        if os.path.exists(local_path):
            pixmap = QPixmap(local_path)
            if not pixmap.isNull():
                self._cache[url] = pixmap
                return pixmap

        # Start async download (only if not already downloading)
        urls_downloading = {w.url for w in self._workers if w.isRunning()}
        if url not in urls_downloading:
            worker = FlagDownloadWorker(url)
            worker.flag_downloaded.connect(self._on_flag_downloaded)
            worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
            self._workers.append(worker)
            worker.start()

        return None

    def _on_flag_downloaded(self, url: str, pixmap: QPixmap):
        if not pixmap.isNull():
            self._cache[url] = pixmap
            self.flag_ready.emit(url)

    def _cleanup_worker(self, worker: FlagDownloadWorker):
        if worker in self._workers:
            self._workers.remove(worker)


# ============================================================================
# Data Fetch Worker (background thread)
# ============================================================================

class FetchWorker(QThread):
    """Background thread for fetching match data without blocking UI."""
    data_ready = pyqtSignal(list, str, bool)  # matches, date_str, success

    def __init__(self, api: WorldCupAPI, date_str: str = None, parent=None):
        super().__init__(parent)
        self.api = api
        self.date_str = date_str

    def run(self):
        try:
            date = self.date_str or get_beijing_today()
            matches, has_data = self.api.fetch_matches_by_beijing_date(date)
            self.data_ready.emit(matches, date, has_data)
        except Exception:
            date = self.date_str or get_beijing_today()
            self.data_ready.emit([], date, False)


class BulkFetchWorker(QThread):
    """Background thread that pre-fetches a wide date range in ONE
    ESPN call. Populates the WorldCupOverlay._bulk_cache so subsequent
    per-date fetches return instantly without hitting ESPN.

    ESPN's scoreboard endpoint supports a `?dates=YYYYMMDD-YYYYMMDD`
    range parameter, so a 22-day window is a single HTTP call.
    """
    bulk_ready = pyqtSignal(dict)  # {bj_date_str: [Match, ...]}

    def __init__(self, api: WorldCupAPI, today_str: str,
                 past_days: int = 1, future_days: int = 20, parent=None):
        super().__init__(parent)
        self.api = api
        self.today_str = today_str
        self.past_days = past_days
        self.future_days = future_days

    def run(self):
        try:
            m, d, y = self.today_str.split("/")
            today = datetime(int(y), int(m), int(d))
            utc_start = (today - timedelta(days=self.past_days + 1)).strftime("%Y%m%d")
            utc_end = (today + timedelta(days=self.future_days + 1)).strftime("%Y%m%d")
            events = self.api._fetch_for_date_range(utc_start, utc_end)
            if not events:
                self.bulk_ready.emit({})
                return
            from collections import defaultdict
            by_date = defaultdict(list)
            for ev in events:
                m_obj = self.api._build_match(ev)
                if m_obj is None or not m_obj.local_date:
                    continue
                bj_date = m_obj.local_date.split(" ", 1)[0]
                by_date[bj_date].append(m_obj)
            self.bulk_ready.emit(dict(by_date))
        except Exception:
            self.bulk_ready.emit({})


class _PushTestWorker(QThread):
    """Background thread for the PushPlus test button.

    Doing this in a background thread is critical: the previous
    implementation called `requests.post` directly in a slot, which
    on macOS + PyQt5 would sometimes trigger a sip crash when the
    network call returned during a modal dialog's run loop.

    Signals:
    - finished_ok(success_count, total)
    - finished_fail(success_count, total, fail_detail)
    - finished()  — always emitted last, for cleanup
    """
    finished_ok = pyqtSignal()
    finished_fail = pyqtSignal(str)

    def __init__(self, token: str, dialog, parent=None):
        super().__init__(parent)
        self.token = token
        self.dialog = dialog

    def run(self):
        url = "http://www.pushplus.plus/send"
        # Build the test payload using the dialog's _format_test_push
        # helper. We do this here (not in the slot) to keep the slot
        # non-blocking.
        try:
            test_title, test_content = self.dialog._format_test_push()
        except Exception:
            self.finished_fail.emit("无法生成测试内容（API 失败）")
            self.finished.emit()
            return

        try:
            payload = {
                "token": self.token,
                "title": test_title,
                "content": test_content,
            }
            resp = requests.post(url, json=payload,
                                 headers={"Content-Type": "application/json"},
                                 timeout=10)
            if resp.status_code == 200:
                self.finished_ok.emit()
            else:
                self.finished_fail.emit(
                    f"推送服务返回 HTTP {resp.status_code}：{resp.text[:120]}"
                )
        except Exception as e:
            self.finished_fail.emit(f"网络错误：{e}")
        self.finished.emit()


# ============================================================================
# Settings Dialog
# ============================================================================

class SettingsDialog(QDialog):
    """Settings popup for refresh interval configuration."""

    def __init__(self, current_interval: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("刷新间隔设置")
        self.setFixedSize(340, 240)
        # No WindowStaysOnTopHint — user can switch away and come back
        self.setWindowFlags(Qt.Dialog)
        self.setStyleSheet("""
            QDialog {
                background-color: #2a2a38;
                border: 1px solid #4a4a5a;
                border-radius: 12px;
            }
            QLabel {
                color: #d0d0e0;
                font-size: 13px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel("⏱ 刷新间隔")
        title.setStyleSheet("font-size: 17px; font-weight: bold; color: #ffffff;")
        main_layout.addWidget(title)

        refresh_group = QLabel("选择自动刷新频率")
        refresh_group.setStyleSheet("font-size: 12px; color: #8080a0; font-weight: bold;")
        main_layout.addWidget(refresh_group)

        interval_layout = QHBoxLayout()
        interval_label = QLabel("自动刷新:")
        interval_label.setFixedWidth(80)
        interval_layout.addWidget(interval_label)

        self.interval_combo = QComboBox()
        intervals = ["30 秒", "60 秒", "2 分钟", "5 分钟"]
        values = [30, 60, 120, 300]
        self.interval_combo.addItems(intervals)
        current_idx = values.index(current_interval) if current_interval in values else 0
        self.interval_combo.setCurrentIndex(current_idx)
        self._initial_index = current_idx  # remember for "same value = no-op"
        self.interval_combo.setStyleSheet(self._combo_style())
        interval_layout.addWidget(self.interval_combo)
        interval_layout.addStretch()
        main_layout.addLayout(interval_layout)

        hint = QLabel("⚠ 建议 ≥ 30 秒，避免触发 ESPN 限流\n选择后自动保存并关闭")
        hint.setStyleSheet("font-size: 11px; color: #8080a0; margin-top: 4px;")
        main_layout.addWidget(hint)

        main_layout.addStretch()

        # Show a "关闭" button so the user can dismiss the dialog without
        # selecting anything (e.g. when the current value is already correct).
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a4a; color: #d0d0e0;
                border: 1px solid #5a5a6a; border-radius: 6px;
                padding: 8px 16px; font-size: 13px; min-width: 80px;
            }
            QPushButton:hover { background-color: #4a4a5a; }
            QPushButton:pressed { background-color: #5a5a6a; }
        """)
        close_btn.clicked.connect(self.reject)
        main_layout.addWidget(close_btn, alignment=Qt.AlignRight)

        main_layout.addStretch()

        self._ready = False
        self._intervals_map = {0: 30, 1: 60, 2: 120, 3: 300}
        self.interval_combo.currentIndexChanged.connect(self._on_interval_changed)
        self._ready = True

    def _on_interval_changed(self, idx: int):
        if not self._ready or idx < 0:
            return
        # Only auto-save & close when the user actually picks a different
        # value. Re-selecting the current value is treated as a no-op
        # (user is just confirming) — the dialog stays open and they can
        # click the 关闭 button to dismiss.
        if idx == self._initial_index:
            return
        # Use singleShot to avoid exiting while the user is still scrolling.
        QTimer.singleShot(150, self.accept)

    @staticmethod
    def _combo_style() -> str:
        return """
            QComboBox {
                background-color: #3a3a4a;
                color: #d0d0e0;
                border: 1px solid #5a5a6a;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
            }
            QComboBox:hover { border-color: #7a7a8a; }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox QAbstractItemView {
                background-color: #3a3a4a;
                color: #d0d0e0;
                selection-background-color: #5a5a7a;
            }
        """

    def get_interval(self) -> int:
        values = [10, 20, 30, 60, 120]
        return values[self.interval_combo.currentIndex()]


# ============================================================================
# Date Picker Dialog
# ============================================================================

class DatePickDialog(QDialog):
    """Date picker dialog for jumping to a specific date."""

    def __init__(self, current_date_str: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("跳转到日期")
        self.setFixedSize(340, 420)
        # No WindowStaysOnTopHint — user can switch away and come back
        self.setWindowFlags(Qt.Dialog)
        self.setStyleSheet("""
            QDialog { background-color: #2a2a38; border-radius: 12px; }
            QLabel { color: #d0d0e0; font-size: 13px; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("📅 跳转到日期")
        title.setStyleSheet("font-size: 17px; font-weight: bold; color: #ffffff;")
        layout.addWidget(title)

        hint = QLabel("世界杯比赛日期: 2026年6月11日 - 7月20日")
        hint.setStyleSheet("font-size: 11px; color: #8080a0;")
        layout.addWidget(hint)

        self.calendar = QCalendarWidget()
        self.calendar.setStyleSheet("""
            QCalendarWidget {
                background-color: #3a3a4a;
                color: #d0d0e0;
                border: 1px solid #5a5a6a;
                border-radius: 8px;
            }
            QCalendarWidget QAbstractItemView {
                background-color: #3a3a4a;
                color: #d0d0e0;
                selection-background-color: #5a5a7a;
                selection-color: #ffffff;
            }
            QCalendarWidget QToolButton {
                background-color: #3a3a4a;
                color: #d0d0e0;
                border: none;
            }
            QCalendarWidget QMenu {
                background-color: #3a3a4a;
                color: #d0d0e0;
            }
            QCalendarWidget QSpinBox {
                background-color: #3a3a4a;
                color: #d0d0e0;
            }
        """)
        self.calendar.setMinimumDate(QDate(2026, 6, 11))
        self.calendar.setMaximumDate(QDate(2026, 7, 20))

        try:
            month, day, year = current_date_str.split("/")
            self.calendar.setSelectedDate(QDate(int(year), int(month), int(day)))
        except Exception:
            pass

        layout.addWidget(self.calendar)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        ok_btn = QPushButton("确定")
        ok_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a6fa5; color: white; border: none;
                border-radius: 6px; padding: 8px 24px; font-size: 14px;
            }
            QPushButton:hover { background-color: #5a7fb5; }
        """)
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #4a4a5a; color: #d0d0e0; border: none;
                border-radius: 6px; padding: 8px 24px; font-size: 14px;
            }
            QPushButton:hover { background-color: #5a5a6a; }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

    def get_date_str(self) -> str:
        d = self.calendar.selectedDate()
        return f"{d.month():02d}/{d.day():02d}/{d.year()}"


# ============================================================================
# PushPlus Settings Dialog
# ============================================================================

class PushPlusDialog(QDialog):
    """Settings dialog for PushPlus (pushplus.plus) push notifications."""

    def __init__(self, token: str, on_start: bool, on_goal: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PushPlus 推送设置")
        self.setFixedSize(440, 380)
        # Remove WindowStaysOnTopHint so it doesn't steal focus / block switching
        # Settings only save on explicit "保存" button click (or close via X)
        self.setWindowFlags(Qt.Dialog)
        self.setStyleSheet("""
            QDialog {
                background-color: #2a2a38;
                border-radius: 12px;
            }
            QLabel { color: #d0d0e0; font-size: 13px; }
            QLabel.title { font-size: 17px; font-weight: bold; color: #ffffff; }
            QLabel.hint  { font-size: 11px; color: #8080a0; }
            QLineEdit {
                background-color: #1e1e2a;
                color: #d0d0e0;
                border: 1px solid #4a4a5a;
                border-radius: 6px;
                padding: 8px 10px;
                font-size: 13px;
                selection-background-color: #4a6cf7;
            }
            QLineEdit:focus { border-color: #6a8cf7; }
            QCheckBox { color: #d0d0e0; font-size: 13px; spacing: 8px; padding: 4px; }
            QCheckBox::indicator {
                width: 20px; height: 20px;
                border: 1px solid #5a5a6a; border-radius: 4px;
                background-color: #1e1e2a;
            }
            QCheckBox::indicator:hover {
                border-color: #7a7a8a;
            }
            QCheckBox::indicator:checked {
                background-color: #4a6cf7;
                border-color: #4a6cf7;
                image: url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiI+PHBhdGggZD0iTTYuNDEgMTEuMTlsLTQuMDctMy44N2ExIDEgMCAxIDEgMS4zMi0xLjQ5bDQuMDcgMy44N2wxMC4wOC05LjU0YTEgMSAwIDEgMSAxLjMyIDEuNDlaIiBmaWxsPSIjZmZmZmZmIi8+PC9zdmc+);
            }
            QPushButton {
                background-color: #3a3a4a; color: #d0d0e0;
                border: 1px solid #5a5a6a; border-radius: 6px;
                padding: 8px 16px; font-size: 13px; min-width: 80px;
            }
            QPushButton:hover { background-color: #4a4a5a; }
            QPushButton:pressed { background-color: #5a5a6a; }
            QPushButton.primary {
                background-color: #4a6cf7; color: #ffffff;
                border: 1px solid #4a6cf7;
            }
            QPushButton.primary:hover { background-color: #5a7cff; }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(24, 20, 24, 20)

        title = QLabel("📲 PushPlus 推送")
        title.setProperty("class", "title")
        title.setStyleSheet("font-size: 17px; font-weight: bold; color: #ffffff;")
        main_layout.addWidget(title)

        # Registration hint
        reg_row = QHBoxLayout()
        reg_row.setSpacing(6)
        reg_icon = QLabel("💡")
        reg_text = QLabel("还没注册？")
        reg_text.setStyleSheet("font-size: 12px; color: #8080a0;")
        reg_link = QLabel('<a href="https://www.pushplus.plus/" style="color: #6a8cf7; text-decoration: none;">👉 pushplus.plus 一键注册（微信扫码）</a>')
        reg_link.setOpenExternalLinks(True)
        reg_link.setStyleSheet("font-size: 12px;")
        reg_row.addWidget(reg_icon)
        reg_row.addWidget(reg_text)
        reg_row.addWidget(reg_link)
        reg_row.addStretch()
        main_layout.addLayout(reg_row)

        # Token input
        token_label = QLabel("Token (在 pushplus.plus 个人中心复制，多个用 / 分隔):")
        main_layout.addWidget(token_label)
        self.token_edit = QLineEdit()
        self.token_edit.setText(token)
        self.token_edit.setPlaceholderText("单个 token，或 token1/token2 推送给多人")
        self.token_edit.setEchoMode(QLineEdit.Normal)  # plain text, easier to copy/edit
        main_layout.addWidget(self.token_edit)

        # Event checkboxes
        self.start_check = QCheckBox("⚽ 比赛开始时推送")
        self.start_check.setChecked(on_start)
        main_layout.addWidget(self.start_check)

        self.goal_check = QCheckBox("🔄 比分变化时推送（进球 / VAR取消 / 修正）")
        self.goal_check.setChecked(on_goal)
        main_layout.addWidget(self.goal_check)

        # Buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(8)

        test_btn = QPushButton("🧪 测试推送")
        test_btn.clicked.connect(self._on_test)
        button_row.addWidget(test_btn)

        button_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)

        save_btn = QPushButton("保存")
        save_btn.setProperty("class", "primary")
        save_btn.setStyleSheet(
            "background-color: #4a6cf7; color: #ffffff; border: 1px solid #4a6cf7;"
            " border-radius: 6px; padding: 8px 16px; font-size: 13px; min-width: 80px;"
        )
        save_btn.clicked.connect(self._on_save)
        button_row.addWidget(save_btn)

        main_layout.addLayout(button_row)

    def _on_test(self):
        """Send a test push with the FIRST token only (even if not saved yet).
        Runs in a background thread so the dialog stays responsive while
        we're hitting ESPN + PushPlus.
        """
        token_raw = self.token_edit.text().strip()
        if not token_raw:
            QMessageBox.warning(
                self, "无法测试",
                "请先填写 Token。\n留空则不启用推送。"
            )
            return
        tokens = [t.strip() for t in token_raw.split("/") if t.strip()]
        if not tokens:
            QMessageBox.warning(self, "无法测试", "Token 格式不正确。")
            return
        # Test push only sends to the first token. Production (real
        # match-event) push still fans out to all configured tokens —
        # see _send_push_notification.
        primary_token = tokens[0]
        # Disable the test button while sending to prevent double-clicks
        sender = self.sender()
        if isinstance(sender, QPushButton):
            sender.setEnabled(False)
            sender.setText("发送中…")
        # Run the actual send in a background thread so the UI doesn't
        # freeze (and we don't trigger the sip crash from running a
        # blocking network call inside a slot).
        worker = _PushTestWorker(primary_token, self)
        worker.finished_ok.connect(self._on_test_finished)
        worker.finished_fail.connect(self._on_test_failed)
        worker.finished.connect(lambda: self._on_test_done(sender))
        self._test_worker = worker  # keep alive
        worker.start()

    def _on_test_done(self, sender):
        if isinstance(sender, QPushButton):
            sender.setEnabled(True)
            sender.setText("🧪 测试推送")

    def _on_test_finished(self):
        QMessageBox.information(
            self, "已发送",
            "测试推送已发送，请到微信查收。\n\n"
            "测试内容：\n  · 昨日战报\n  · 今日比赛\n  · 明日赛程"
        )

    def _on_test_failed(self, fail_detail: str):
        QMessageBox.warning(
            self, "推送失败",
            f"{fail_detail}\n\n"
            f"请检查 Token 是否正确，或稍后重试。"
        )

    def _format_test_push(self) -> tuple:
        """Return (title, content) for a test push.
        Includes yesterday's results + today's matches + tomorrow's schedule."""
        title = "🏆 世界杯推送"
        today = get_beijing_today()
        today_dt = datetime.strptime(today, "%m/%d/%Y")
        yesterday_dt = today_dt - timedelta(days=1)
        yesterday = yesterday_dt.strftime("%m/%d/%Y")
        tomorrow_dt = today_dt + timedelta(days=1)
        tomorrow = tomorrow_dt.strftime("%m/%d/%Y")

        # Find the main window's API instance.
        # We try multiple paths to find it: self.parent(), then a
        # top-level widget lookup. This is important because when
        # _format_test_push() is called from a background thread,
        # self.parent() may return None or a stale wrapper.
        api = None
        try:
            from PyQt5.QtWidgets import QApplication
            parent = self.parent()
            api = getattr(parent, "api", None) if parent else None
            if api is None:
                for w in QApplication.topLevelWidgets():
                    cand = getattr(w, "api", None)
                    if cand is not None:
                        api = cand
                        break
        except Exception:
            api = None

        # CRITICAL: clear the 2-req/min cap on the main api BEFORE
        # doing anything else. The main window's auto-refresh has
        # likely used up the cap already, and we need to do 1+ API
        # call for the test push. The main refresh's next tick
        # (>30s away) will then see the freshly-empty cap and
        # count from there.
        if api is not None and hasattr(api, "force_allow"):
            try:
                api.force_allow()
            except Exception:
                pass

        # IMPORTANT: build a new WorldCupAPI instance per call when we
        # don't have a parent api — the worker's `self.api._fetch_for_date`
        # call hits a `_can_make_request` cap that persists across calls
        # on the main window's api. A fresh instance is independent.
        # In practice we ALWAYS have a parent api, so this is just a
        # safety net.
        must_make_fresh_api = (api is None)

        def fetch(date_str):
            if api is None:
                return [], False
            try:
                return api.fetch_matches_by_beijing_date(date_str)
            except Exception:
                return [], False

        # ESPN's `?dates=YYYYMMDD-YYYYMMDD` parameter accepts a date
        # RANGE and returns all events whose UTC date falls within it.
        # We use this for the test push: a SINGLE call returns all
        # 3 days of matches (yesterday + today + tomorrow). Saves
        # API quota and avoids the 2-req/min cap from killing us.
        def fetch_date_range(yyyymmdd_start: str, yyyymmdd_end: str):
            """Fetch all events in the inclusive date range. Returns
            (matches, success)."""
            if api is None:
                return [], False
            api.force_allow()
            try:
                events = api._fetch_for_date_range(yyyymmdd_start, yyyymmdd_end)
            except Exception:
                events = []
            if not events:
                return [], False
            matches = []
            for ev in events:
                try:
                    m = api._build_match(ev)
                except Exception:
                    continue
                if m:
                    matches.append(m)
            return matches, True

        def group_by_bj_date(matches: list) -> dict:
            """Group matches by their Beijing date (MM/DD/YYYY)."""
            from collections import defaultdict
            groups = defaultdict(list)
            for m in matches:
                if m.local_date and " " in m.local_date:
                    bj_date = m.local_date.split(" ", 1)[0]
                    groups[bj_date].append(m)
            return groups

        # Single API call covers all 3 days. We need yesterday + today
        # + tomorrow in Beijing time. ESPN's `?dates=YYYYMMDD-YYYYMMDD`
        # is keyed on *UTC* date, so a BJ-day's events can spill into
        # UTC yesterday (late BJ night) and UTC tomorrow (early BJ
        # morning). To reliably cover BJ yesterday/today/tomorrow we
        # fetch a 5-day UTC range (yesterday-1 .. tomorrow+1, in BJ
        # terms) and then filter in-memory by BJ date.
        #
        # Why 5 not 3: BJ is UTC+8, so the worst-case offset is 16h.
        # BJ-yesterday's late games (kickoff 23:00 BJ = 15:00Z same UTC
        # day) are fine, but BJ-yesterday's very-late games (kickoff
        # 02:00 BJ next day = 18:00Z prev UTC day) are tagged by ESPN
        # as the *previous* UTC day. So a BJ day can pull events from
        # its own UTC day AND the previous UTC day. Pulling BJ-yesterday
        # therefore needs UTC dates [BJ-yesterday-1 .. BJ-yesterday+1].
        # Same for BJ-tomorrow. Combined: [BJ-yesterday-1 .. BJ-tomorrow+1]
        # = 5 UTC days.
        range_start = (today_dt - timedelta(days=2)).strftime("%Y%m%d")
        range_end = (today_dt + timedelta(days=2)).strftime("%Y%m%d")
        all_matches, range_ok = fetch_date_range(range_start, range_end)

        groups = group_by_bj_date(all_matches) if all_matches else {}
        yesterday_matches = groups.get(yesterday, [])
        today_matches = groups.get(today, [])
        tomorrow_matches = groups.get(tomorrow, [])
        yesterday_ok = range_ok
        today_ok = range_ok
        tomorrow_ok = range_ok

        # Debug visibility: if every section came back empty despite the
        # API being responsive, surface that fact so we can debug from
        # the user's screenshot instead of always showing "API 失败".
        total = len(yesterday_matches) + len(today_matches) + len(tomorrow_matches)
        if total == 0 and api is not None:
            # The api exists but we got no data for any of the 3 days.
            # Most likely cause: the 2 req/min cap kicked in and the
            # last fetch returned []. Try a fresh api (clean counters)
            # and re-fetch today only, so the test still shows
            # something useful.
            try:
                fresh = WorldCupAPI()
                # Bypass the cap on the fresh instance (it has no
                # history, so it should always allow).
                evs = fresh._fetch_today()
                if evs:
                    rebuilt = []
                    for ev in evs:
                        m = fresh._build_match(ev)
                        if m:
                            rebuilt.append(m)
                    if rebuilt:
                        today_matches = rebuilt
                        today_ok = True
            except Exception:
                pass

        def status_emoji_and_tag(m) -> tuple:
            """Return (emoji, status_tag) for a match, where status_tag
            is a short Chinese description of the current match state.
            Guarantees exactly one of (点球/加时) shows, never both —
            a match is finished EITHER after extra time OR after a
            shootout, not both."""
            # status values: notstarted | 1h | ht | 2h | et | pen | finished | live
            if m.status == "finished":
                # priority: shootout > extra time > plain finish
                if m.has_penalties:
                    return "✅", "（点球结束）"
                if "加时" in m.status_text:
                    return "✅", "（加时结束）"
                return "✅", "（已结束）"
            if m.status == "notstarted":
                return "⏰", "（未开赛）"
            if m.status == "ht":
                return "⏸", "（中场）"
            if m.status == "1h":
                return "🔴", "（上半场）"
            if m.status == "2h":
                return "🔴", "（下半场）"
            if m.status == "et":
                return "⏱", "（加时）"
            if m.status == "pen":
                return "🎯", "（点球大战）"
            if m.status == "live":
                return "🔴", "（进行中）"
            return "•", ""

        def format_match_line(m, show_score: bool = True) -> str:
            """Format a single match line. Always includes status tag
            so the user can tell at a glance what stage the match is in."""
            emoji, status_tag = status_emoji_and_tag(m)
            time_str = m.local_date.split(" ", 1)[1] if m.local_date and " " in m.local_date else ""
            time_part = f" {time_str}" if time_str else ""
            if show_score:
                return f"  {emoji} {m.home_team} {m.home_score}-{m.away_score} {m.away_team} {time_part}{status_tag}"
            else:
                return f"  {emoji} {m.home_team} vs {m.away_team} {time_part}{status_tag}"

        lines = []
        lines.append(f"📅 {datetime.now(BEIJING_TZ).strftime('%Y年%m月%d日 %H:%M:%S')} 测试")
        lines.append("")

        # Yesterday section — show only FINISHED matches
        yesterday_disp = yesterday_dt.strftime("%m月%d日")
        lines.append(f"📊 昨日战报（{yesterday_disp}）")
        if not yesterday_ok:
            lines.append("  数据获取失败，请稍后重试")
        elif not yesterday_matches:
            lines.append("  昨日无比赛")
        else:
            # Filter to only finished matches for the "yesterday results" section
            finished_only = [m for m in yesterday_matches if m.status == "finished"]
            if not finished_only:
                lines.append("  昨日无已结束的比赛")
            else:
                for m in finished_only:
                    lines.append(format_match_line(m, show_score=True))
        lines.append("")

        # Today section — show all matches with status
        lines.append(f"⚽ 今日比赛（{today_dt.strftime('%m月%d日')}）")
        if not today_ok:
            lines.append("  数据获取失败，请稍后重试")
        elif not today_matches:
            lines.append("  今日无比赛")
        else:
            for m in today_matches:
                lines.append(format_match_line(m, show_score=True))
        lines.append("")

        # Tomorrow section — show upcoming matches with stage info
        tomorrow_disp = tomorrow_dt.strftime("%m月%d日")
        lines.append(f"📅 明日赛程（{tomorrow_disp}）")
        if not tomorrow_ok:
            lines.append("  数据获取失败，请稍后重试")
        elif not tomorrow_matches:
            lines.append("  明日无比赛")
        else:
            for m in tomorrow_matches:
                time_str = m.local_date.split(" ", 1)[1] if m.local_date and " " in m.local_date else ""
                time_part = f" {time_str}" if time_str else ""
                stage = STAGE_NAMES.get(m.stage, m.stage)
                lines.append(
                    f"  ⏰ {m.home_team} vs {m.away_team}{time_part}  · {stage}"
                )

        lines.append("")
        lines.append("🎉 推送配置成功！比赛开始 / 比分变动时会自动通知。")
        content = "\n".join(lines)
        return title, content

    def _on_save(self):
        if not self._validate():
            return
        self.accept()

    def _validate(self) -> bool:
        token = self.token_edit.text().strip()
        on_start = self.start_check.isChecked()
        on_goal = self.goal_check.isChecked()
        # If neither event checked, allow saving with empty token (just disabled)
        if not token and (on_start or on_goal):
            QMessageBox.warning(
                self, "无法保存",
                "勾选了推送事件但 Token 为空。\n请填写 Token 或取消勾选。"
            )
            return False
        return True

    def get_settings(self) -> tuple:
        return (
            self.token_edit.text().strip(),
            self.start_check.isChecked(),
            self.goal_check.isChecked(),
        )


# ============================================================================
# Match Card Widget
# ============================================================================

LIVE_STATUSES = {"1h", "2h", "ht", "et", "live", "pen"}

class MatchCard(QFrame):
    """Widget displaying a single match."""

    def __init__(self, match: Match, flag_loader: FlagLoader, parent=None):
        super().__init__(parent)
        self.match = match
        self.flag_loader = flag_loader
        # Persistent widget references — MUST be set BEFORE setup_ui() is called
        # and survive across update_match() so signal/slot connections stay valid.
        self.home_flag_label = None
        self.away_flag_label = None
        self.status_label = None
        self.home_name_label = None
        self.away_name_label = None
        self.home_score_label = None
        self.away_score_label = None
        self.stage_label = None
        self.time_label = None
        self.penalty_label = None
        self.penalty_row = None  # the layout for penalty info (may not exist)
        self.extra_time_label = None
        self.extra_time_row = None
        self.et_badge = None  # ⏱ 加时中 badge
        self.et_row = None
        self.setup_ui()
        # Listen for async flag downloads
        if hasattr(self.flag_loader, 'flag_ready'):
            self.flag_loader.flag_ready.connect(self._on_flag_ready)

    def setup_ui(self):
        """Build the match card UI. All updatable child widgets are stored
        as self.* attributes so update_match() can refresh their content
        in place — never destroy and recreate, which is the only way to
        avoid the macOS PyQt5 crash pattern where stale C++ widgets
        receive mouse events after deleteLater()."""
        self.setFixedHeight(105)

        # Brighter background for live matches
        if self.match.status in LIVE_STATUSES:
            bg = "rgba(58, 58, 78, 230)"
        else:
            bg = "rgba(42, 42, 56, 210)"

        self.setStyleSheet(f"""
            MatchCard {{
                background-color: {bg};
                border: 1px solid rgba(60, 60, 80, 100);
                border-radius: 12px;
            }}
        """)

        # Replace any old shadow effect (only safe on first build)
        old_effect = self.graphicsEffect()
        if old_effect is not None and not isinstance(old_effect, QGraphicsDropShadowEffect):
            self.setGraphicsEffect(None)
        if self.graphicsEffect() is None:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(15)
            shadow.setColor(QColor(0, 0, 0, 80))
            shadow.setOffset(0, 2)
            self.setGraphicsEffect(shadow)

        # If layout already exists (re-call), just clear child refs
        if self.layout() is not None:
            QWidget().setLayout(self.layout())  # detach old layout

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 10, 14, 10)
        main_layout.setSpacing(6)

        # --- Stage/Group & Status Row ---
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        stage_text = STAGE_NAMES.get(self.match.stage, self.match.stage.upper())
        if self.match.group and self.match.stage == "group":
            stage_text = f"Group {self.match.group}"
        elif self.match.group and self.match.stage != "group":
            stage_text = f"{stage_text} ({self.match.group})"

        self.stage_label = QLabel(stage_text)
        self.stage_label.setStyleSheet("""
            font-size: 11px; color: #9090b0; font-weight: 600;
            background-color: rgba(50,50,70,0.6);
            border-radius: 4px; padding: 2px 8px;
        """)
        top_row.addWidget(self.stage_label)

        if self.match.local_date:
            time_display = self.match.local_date
            if " " in time_display:
                time_display = time_display.split(" ", 1)[1]
            self.time_label = QLabel(f"🕐 {time_display}")
            self.time_label.setStyleSheet("font-size: 11px; color: #8080a0;")
            top_row.addWidget(self.time_label)

        top_row.addStretch()

        # Status badge
        status_text = self._get_display_status()
        self.status_label = QLabel(status_text)
        status_style = self._get_status_style()
        self.status_label.setStyleSheet(f"""
            font-size: 12px; color: {status_style['color']}; font-weight: bold;
            background-color: {status_style['bg']};
            border-radius: 6px; padding: 3px 10px;
        """)
        top_row.addWidget(self.status_label)
        main_layout.addLayout(top_row)

        # --- Teams & Score Row ---
        teams_row = QHBoxLayout()
        teams_row.setSpacing(6)

        # Home team
        home_layout = QHBoxLayout()
        home_layout.setSpacing(6)
        self.home_flag_label = QLabel()
        home_flag = self.flag_loader.get_flag(self.match.home_flag)
        if home_flag:
            self.home_flag_label.setPixmap(home_flag.scaled(28, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.home_flag_label.setText("🏳")
            self.home_flag_label.setStyleSheet("font-size: 18px;")
        self.home_flag_label.setFixedSize(36, 24)
        home_layout.addWidget(self.home_flag_label)

        self.home_name_label = QLabel(self.match.home_team)
        self.home_name_label.setStyleSheet("font-size: 14px; color: #a0c8ff; font-weight: bold;")
        self.home_name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        home_layout.addWidget(self.home_name_label)
        teams_row.addLayout(home_layout, 3)

        # Score
        score_layout = QHBoxLayout()
        score_layout.setSpacing(0)

        self.home_score_label = QLabel(f" {self.match.home_score} ")
        self.home_score_label.setAlignment(Qt.AlignCenter)
        self.home_score_label.setStyleSheet("""
            font-size: 22px; color: #ffffff; font-weight: 900;
            background-color: rgba(50,50,65,0.8);
            border-radius: 8px; padding: 3px 8px;
        """)
        score_layout.addWidget(self.home_score_label)

        colon = QLabel(":")
        colon.setAlignment(Qt.AlignCenter)
        colon.setStyleSheet("font-size: 18px; color: #8080a0; font-weight: bold;")
        colon.setFixedWidth(16)
        score_layout.addWidget(colon)

        self.away_score_label = QLabel(f" {self.match.away_score} ")
        self.away_score_label.setAlignment(Qt.AlignCenter)
        self.away_score_label.setStyleSheet("""
            font-size: 22px; color: #ffffff; font-weight: 900;
            background-color: rgba(50,50,65,0.8);
            border-radius: 8px; padding: 3px 8px;
        """)
        score_layout.addWidget(self.away_score_label)

        teams_row.addLayout(score_layout, 1)

        # Away team
        away_layout = QHBoxLayout()
        away_layout.setSpacing(6)

        self.away_name_label = QLabel(self.match.away_team)
        self.away_name_label.setStyleSheet("font-size: 14px; color: #ffb0a0; font-weight: bold;")
        self.away_name_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.away_name_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        away_layout.addWidget(self.away_name_label)

        self.away_flag_label = QLabel()
        away_flag = self.flag_loader.get_flag(self.match.away_flag)
        if away_flag:
            self.away_flag_label.setPixmap(away_flag.scaled(28, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.away_flag_label.setText("🏳")
            self.away_flag_label.setStyleSheet("font-size: 18px;")
        self.away_flag_label.setFixedSize(36, 24)
        away_layout.addWidget(self.away_flag_label)
        teams_row.addLayout(away_layout, 3)

        main_layout.addLayout(teams_row)

        # --- Penalty score (if applicable) ---
        if self.match.has_penalties:
            self.penalty_row = QHBoxLayout()
            self.penalty_row.addStretch(2)
            self.penalty_label = QLabel(f"点球 ({self.match.home_penalty} : {self.match.away_penalty})")
            self.penalty_label.setAlignment(Qt.AlignCenter)
            self.penalty_label.setStyleSheet("""
                font-size: 11px; color: #ffcc00; font-weight: bold;
                background-color: rgba(255, 200, 0, 0.1);
                border-radius: 4px; padding: 2px 10px;
            """)
            self.penalty_row.addWidget(self.penalty_label)
            self.penalty_row.addStretch(2)
            main_layout.addLayout(self.penalty_row)

        # --- Extra time score (if applicable) ---
        # Mirrors the penalty row above. We show "加时 (X : Y)" for
        # matches that went to ET (has_extra_time). We always render
        # the row when has_extra_time is true, even if both counts
        # are 0 (0-0 in ET still happened — though uncommon).
        if self.match.has_extra_time:
            self.extra_time_row = QHBoxLayout()
            self.extra_time_row.addStretch(2)
            et_text = f"加时 ({self.match.home_extra_time} : {self.match.away_extra_time})"
            self.extra_time_label = QLabel(et_text)
            self.extra_time_label.setAlignment(Qt.AlignCenter)
            self.extra_time_label.setStyleSheet("""
                font-size: 11px; color: #ff9966; font-weight: bold;
                background-color: rgba(255, 150, 100, 0.12);
                border-radius: 4px; padding: 2px 10px;
            """)
            self.extra_time_row.addWidget(self.extra_time_label)
            self.extra_time_row.addStretch(2)
            main_layout.addLayout(self.extra_time_row)

    def _get_display_status(self) -> str:
        """Get status text, with countdown for upcoming matches."""
        if self.match.status != "notstarted":
            return self.match.status_text
        countdown = self._get_countdown()
        return countdown if countdown else self.match.status_text

    def _get_countdown(self) -> str:
        """Calculate countdown text for upcoming matches."""
        if not self.match.local_date:
            return ""
        try:
            parts = self.match.local_date.split(" ")
            if len(parts) < 2:
                return ""
            date_part = parts[0]
            time_part = parts[1]
            month, day, year = date_part.split("/")
            hour, minute = time_part.split(":")
            match_dt = datetime(int(year), int(month), int(day),
                                int(hour), int(minute), tzinfo=BEIJING_TZ)
            now = datetime.now(BEIJING_TZ)
            diff = match_dt - now
            if diff.total_seconds() <= 0:
                return ""
            hours = int(diff.total_seconds() // 3600)
            minutes = int((diff.total_seconds() % 3600) // 60)
            if hours > 23:
                days = hours // 24
                return f"{days}天后"
            elif hours > 0:
                return f"{hours}小时{minutes}分后"
            elif minutes > 0:
                return f"{minutes}分钟后"
            else:
                return "即将开始"
        except Exception:
            return ""

    def update_countdown(self):
        """Update countdown text on the status label (called by timer)."""
        if self.match.status == "notstarted" and self.status_label:
            new_text = self._get_display_status()
            if self.status_label.text() != new_text:
                self.status_label.setText(new_text)

    def _on_flag_ready(self, url: str):
        """Called when a flag finishes downloading."""
        # Guard: card may be hidden/awaiting async deletion — ignore stale signals
        if self.isHidden():
            return
        if url == self.match.home_flag and self.home_flag_label:
            pixmap = self.flag_loader.get_flag(url)
            if pixmap:
                self.home_flag_label.setPixmap(pixmap.scaled(28, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.home_flag_label.setStyleSheet("")
        if url == self.match.away_flag and self.away_flag_label:
            pixmap = self.flag_loader.get_flag(url)
            if pixmap:
                self.away_flag_label.setPixmap(pixmap.scaled(28, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.away_flag_label.setStyleSheet("")

    def _get_status_style(self) -> dict:
        styles = {
            "1h": {"color": "#ff6b6b", "bg": "rgba(255,59,48,0.15)"},
            "2h": {"color": "#ff6b6b", "bg": "rgba(255,59,48,0.15)"},
            "ht": {"color": "#ffa500", "bg": "rgba(255,165,0,0.15)"},
            "et": {"color": "#ff6b6b", "bg": "rgba(255,59,48,0.15)"},
            "pen": {"color": "#ff4444", "bg": "rgba(255,0,0,0.15)"},
            "notstarted": {"color": "#64b5f6", "bg": "rgba(0,122,255,0.15)"},
            "finished": {"color": "#9090a0", "bg": "rgba(120,120,140,0.15)"},
        }
        return styles.get(self.match.status, styles["notstarted"])

    def _ensure_et_badge(self):
        """Create / show a '⏱ 加时中' badge under the score, like the
        penalty row shows for shootout results. Mirrors the existing
        penalty-label pattern (lazy-create on first appearance, then
        just update text on subsequent updates)."""
        if self.et_badge is not None:
            # Update text in case minute changed
            self.et_badge.setText("⏱ 加时中")
            return
        if self.layout() is None:
            return
        # Insert just above the penalty row (or at the end if none)
        # We add it as a new row.
        self.et_row = QHBoxLayout()
        self.et_row.addStretch(2)
        self.et_badge = QLabel("⏱ 加时中")
        self.et_badge.setAlignment(Qt.AlignCenter)
        self.et_badge.setStyleSheet("""
            font-size: 11px; color: #ff9966; font-weight: bold;
            background-color: rgba(255, 150, 100, 0.15);
            border-radius: 4px; padding: 2px 10px;
        """)
        self.et_row.addWidget(self.et_badge)
        self.et_row.addStretch(2)
        # Insert before penalty_row if it exists, else just append
        if self.penalty_row is not None and self.penalty_row in self.layout().children():
            self.layout().insertLayout(self.layout().indexOf(self.penalty_row), self.et_row)
        else:
            self.layout().addLayout(self.et_row)

    def _hide_et_badge(self):
        """Hide / remove the ET badge (when match leaves ET state)."""
        if self.et_badge is None:
            return
        try:
            self.et_badge.hide()
        except Exception:
            pass

    def update_match(self, match: Match):
        """Update the card in-place with new match data.
        Updates text/pixmap of existing widgets — NEVER destroys child widgets.
        This is the key fix for the macOS PyQt5 crash that occurred when
        update_match() called _clear_layout() + setup_ui() repeatedly.
        """
        self.match = match

        # Background tint: live vs normal
        if match.status in LIVE_STATUSES:
            bg = "rgba(58, 58, 78, 230)"
        else:
            bg = "rgba(42, 42, 56, 210)"
        self.setStyleSheet(f"""
            MatchCard {{
                background-color: {bg};
                border: 1px solid rgba(60, 60, 80, 100);
                border-radius: 12px;
            }}
        """)

        # Stage / time
        if self.stage_label:
            stage_text = STAGE_NAMES.get(match.stage, match.stage.upper())
            if match.group and match.stage == "group":
                stage_text = f"Group {match.group}"
            elif match.group and match.stage != "group":
                stage_text = f"{stage_text} ({match.group})"
            self.stage_label.setText(stage_text)
        if self.time_label and match.local_date:
            time_display = match.local_date
            if " " in time_display:
                time_display = time_display.split(" ", 1)[1]
            self.time_label.setText(f"🕐 {time_display}")

        # Status badge
        if self.status_label:
            status_text = self._get_display_status()
            status_style = self._get_status_style()
            self.status_label.setText(status_text)
            self.status_label.setStyleSheet(f"""
                font-size: 12px; color: {status_style['color']}; font-weight: bold;
                background-color: {status_style['bg']};
                border-radius: 6px; padding: 3px 10px;
            """)

        # Team names
        if self.home_name_label:
            self.home_name_label.setText(match.home_team)
        if self.away_name_label:
            self.away_name_label.setText(match.away_team)

        # Scores
        if self.home_score_label:
            self.home_score_label.setText(f" {match.home_score} ")
        if self.away_score_label:
            self.away_score_label.setText(f" {match.away_score} ")

        # Flags
        self._refresh_flag(self.home_flag_label, match.home_flag)
        self._refresh_flag(self.away_flag_label, match.away_flag)

        # Penalty row — show/hide based on has_penalties.
        # For ET (extra time) matches, show a "⏱ 加时" hint label
        # below the score so the user knows the match is in extra time.
        if match.has_penalties:
            if self.penalty_label:
                self.penalty_label.setText(f"点球 ({match.home_penalty} : {match.away_penalty})")
            elif self.layout() is not None:
                # Create the row on first appearance (rare; only if state flipped)
                main_layout = self.layout()
                self.penalty_row = QHBoxLayout()
                self.penalty_row.addStretch(2)
                self.penalty_label = QLabel(f"点球 ({match.home_penalty} : {match.away_penalty})")
                self.penalty_label.setAlignment(Qt.AlignCenter)
                self.penalty_label.setStyleSheet("""
                    font-size: 11px; color: #ffcc00; font-weight: bold;
                    background-color: rgba(255, 200, 0, 0.1);
                    border-radius: 4px; padding: 2px 10px;
                """)
                self.penalty_row.addWidget(self.penalty_label)
                self.penalty_row.addStretch(2)
                main_layout.addLayout(self.penalty_row)
        # Extra time indicator — show "⏱ 加时中" badge when in ET
        if match.status == "et":
            self._ensure_et_badge()
        else:
            self._hide_et_badge()

        # Extra time score row (post-match "加时 (X : Y)" footer).
        # Mirrors the penalty row's show/hide pattern.
        if getattr(match, "has_extra_time", False):
            if self.extra_time_label:
                self.extra_time_label.setText(
                    f"加时 ({match.home_extra_time} : {match.away_extra_time})"
                )
            else:
                # Card was created without the row but now needs it
                # (state flipped to AET after a live update). We
                # create it on the fly.
                main_layout = self.layout()
                if main_layout is not None:
                    self.extra_time_row = QHBoxLayout()
                    self.extra_time_row.addStretch(2)
                    self.extra_time_label = QLabel(
                        f"加时 ({match.home_extra_time} : {match.away_extra_time})"
                    )
                    self.extra_time_label.setAlignment(Qt.AlignCenter)
                    self.extra_time_label.setStyleSheet("""
                        font-size: 11px; color: #ff9966; font-weight: bold;
                        background-color: rgba(255, 150, 100, 0.12);
                        border-radius: 4px; padding: 2px 10px;
                    """)
                    self.extra_time_row.addWidget(self.extra_time_label)
                    self.extra_time_row.addStretch(2)
                    main_layout.addLayout(self.extra_time_row)

    def _refresh_flag(self, label: QLabel, flag_url: str):
        """Update a flag label pixmap if the URL changed or flag is now cached."""
        if label is None:
            return
        pixmap = self.flag_loader.get_flag(flag_url)
        if pixmap:
            label.setPixmap(pixmap.scaled(28, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            label.setText("")
            label.setStyleSheet("")
        else:
            # If we previously had a flag for a different team, reset to placeholder
            if label.text() == "":
                label.setText("🏳")
                label.setStyleSheet("font-size: 18px;")


# ============================================================================
# Main Window
# ============================================================================

class WorldCupOverlay(QWidget):
    """Main floating overlay window."""

    def __init__(self):
        super().__init__()
        self.flag_loader = FlagLoader()
        self.refresh_interval = DEFAULT_REFRESH
        self._is_pinned = True
        self._live_only = False
        self.current_date = get_beijing_today()
        self.matches = []
        self.card_widgets = []
        self._fetch_worker = None
        self._pending_refresh = False
        self._is_refreshing = False  # True while a background fetch is in progress
        self._last_refresh_time = 0  # debounce rapid manual refresh clicks
        self._has_ever_loaded = False
        self._retry_count = 0
        self._max_retries = 3
        self._last_known_today = get_beijing_today()
        self._prev_matches_state = {}  # match_id -> (status, home_score, away_score)
        # Per-match goal log for goal-detail pushes:
        # match_id -> { "scored_count": <int>, "last_goal": <dict> | None }
        # last_goal fields: scorer, team, method, minute, clock_display, cumulative
        self._match_goal_state = {}
        self._last_data_change_time = time.time()  # when scores/statuses last actually changed
        self._saved_window_pos = None
        # Bulk-fetched window cache. We pre-load yesterday + today +
        # ~20 days of future matches ONCE on startup, then answer
        # fetch_matches_by_beijing_date() from this in-memory map
        # without hitting ESPN. The 20-day window keeps ESPN's 2
        # req/min cap untouched and lets the user freely flip
        # between dates without triggering new HTTP calls.
        # _bulk_cache: bj_date_str -> (matches, fetched_at, success)
        # _bulk_loaded: True once the initial fetch completed
        # _bulk_fetcher: thread running the bulk fetch
        self._bulk_cache = {}
        self._bulk_loaded = False
        self._bulk_fetcher = None
        self._bulk_window_days = 20  # past 1 day + today + future 20 days
        # PushPlus push settings (loaded by _load_settings)
        self._pushplus_token = ""
        self._pushplus_on_start = False
        self._pushplus_on_goal = False

        self._load_settings()
        self.api = WorldCupAPI()

        self._setup_window()
        self._setup_ui()
        self._setup_timer()

        # Initial fetch — kick off the bulk pre-load + today's refresh.
        # The bulk fetcher populates self._bulk_cache with ~22 days
        # of data; the initial UI fetch then serves today's view
        # from that cache without burning extra API quota.
        self._start_bulk_fetch()
        QTimer.singleShot(300, self._refresh_data)

    # ---- Settings persistence ----

    def _load_settings(self):
        """Load settings from config file."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        try:
            if os.path.exists(SETTINGS_PATH):
                with open(SETTINGS_PATH, "r") as f:
                    data = json.load(f)
                    self.refresh_interval = data.get("refresh_interval", DEFAULT_REFRESH)
                    self._live_only = data.get("live_only", False)
                    self._is_pinned = data.get("is_pinned", True)
                    x = data.get("window_x")
                    y = data.get("window_y")
                    if x is not None and y is not None:
                        self._saved_window_pos = (x, y)
                    # PushPlus settings
                    self._pushplus_token = data.get("pushplus_token", "")
                    self._pushplus_on_start = data.get("pushplus_on_start", False)
                    self._pushplus_on_goal = data.get("pushplus_on_goal", False)
        except Exception:
            pass

    def _save_settings(self):
        """Save all settings to config file."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        try:
            with open(SETTINGS_PATH, "w") as f:
                json.dump({
                    "refresh_interval": self.refresh_interval,
                    "live_only": self._live_only,
                    "is_pinned": self._is_pinned,
                    "window_x": self.x(),
                    "window_y": self.y(),
                    "pushplus_token": getattr(self, "_pushplus_token", ""),
                    "pushplus_on_start": getattr(self, "_pushplus_on_start", False),
                    "pushplus_on_goal": getattr(self, "_pushplus_on_goal", False),
                }, f, indent=2)
        except Exception:
            pass

    # ---- Window setup ----

    def _setup_window(self):
        self.setWindowTitle("🏆 2026世界杯")
        self.setFixedWidth(WINDOW_WIDTH)
        self.resize(WINDOW_WIDTH, 500)

        # Build the window flags. Use a plain frameless window (NOT Qt.Tool)
        # so that macOS treats it like a normal window — otherwise clicking
        # another app and coming back can leave the overlay hidden behind it.
        # Pin behavior is controlled solely by WindowStaysOnTopHint.
        base_flags = Qt.FramelessWindowHint | Qt.Window
        if self._is_pinned:
            base_flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(base_flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Periodic auto-refresh rebuilds card widgets. Without this flag,
        # those rebuilds can briefly steal focus from the user's chat /
        # input window on macOS. WA_ShowWithoutActivating means the
        # overlay updates visually but never activates itself.
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        # Restore position or default to top-right
        if self._saved_window_pos:
            self.move(self._saved_window_pos[0], self._saved_window_pos[1])
        else:
            screen = QApplication.primaryScreen()
            if screen:
                screen_geo = screen.availableGeometry()
                x = screen_geo.right() - WINDOW_WIDTH - 20
                y = screen_geo.top() + 40
                self.move(x, y)

        self._drag_pos = None

    def _setup_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)

        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setStyleSheet("""
            #container {
                background-color: rgba(22, 22, 28, 230);
                border: 1px solid rgba(60, 60, 80, 60);
                border-radius: 16px;
            }
        """)
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        self._build_title_bar(container_layout)

        # Scroll Area
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollArea > QWidget > QWidget { background: transparent; }
            QScrollBar:vertical {
                background: rgba(40,40,56,0.5); width: 6px;
                border-radius: 3px; margin: 4px 2px;
            }
            QScrollBar::handle:vertical {
                background: rgba(100,100,130,0.6);
                border-radius: 3px; min-height: 30px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)

        self.matches_container = QWidget()
        self.matches_container.setStyleSheet("background: transparent;")
        self.matches_layout = QVBoxLayout(self.matches_container)
        self.matches_layout.setContentsMargins(14, 10, 14, 10)
        self.matches_layout.setSpacing(10)
        self.matches_layout.addStretch()

        self.scroll_area.setWidget(self.matches_container)
        container_layout.addWidget(self.scroll_area)

        # Status Bar
        self.status_bar = QLabel("正在加载比赛当前数据...")
        self.status_bar.setAlignment(Qt.AlignCenter)
        self.status_bar.setStyleSheet("""
            font-size: 11px; color: #606080;
            background-color: rgba(30,30,40,0.6);
            padding: 6px;
            border-bottom-left-radius: 16px;
            border-bottom-right-radius: 16px;
        """)
        container_layout.addWidget(self.status_bar)

        outer_layout.addWidget(self.container)

        # Install a top-level event filter so the user can drag the window
        # from anywhere — title bar, blank space, even between cards.
        # QPushButton/QCheckBox etc. still receive their own clicks
        # because the filter only acts on the top-level overlay widget.
        from PyQt5.QtCore import QObject
        self._drag_filter = QObject()
        self._drag_filter.eventFilter = self._drag_event_filter
        self.installEventFilter(self._drag_filter)
        self._maybe_drag = False
        self._drag_press_pos = None

        # Also install a QApplication-level event filter so arrow-key / T
        # shortcuts work even when the overlay doesn't have keyboard
        # focus. This is a no-op if the user is typing into a text field
        # (we check focusWidget() in the handler).
        if QApplication.instance() and not getattr(QApplication.instance(),
                                                    "_wc_shortcut_filter", False):
            app = QApplication.instance()
            self._app_filter = QObject()
            self._app_filter.eventFilter = self._app_event_filter
            app.installEventFilter(self._app_filter)
            app._wc_shortcut_filter = True

    def _app_event_filter(self, watched, event):
        """Application-level key filter: arrow keys / T work even when
        the overlay window doesn't have keyboard focus (e.g. user is in
        another app, didn't click the overlay first)."""
        from PyQt5.QtCore import QEvent
        if event.type() != QEvent.KeyPress:
            return False
        key = event.key()
        # Don't hijack typing in any text field anywhere in the app
        focus = QApplication.focusWidget()
        if focus is not None and isinstance(focus, QLineEdit):
            return False
        if key == Qt.Key_Left:
            self._shift_date(-1)
            event.accept()
            return True
        if key == Qt.Key_Right:
            self._shift_date(1)
            event.accept()
            return True
        if key == Qt.Key_T and not event.modifiers():
            self._go_today()
            event.accept()
            return True
        return False

    def _drag_event_filter(self, watched, event):
        """Top-level event filter: drag the window on left-button move."""
        from PyQt5.QtCore import QEvent
        if watched is not self:
            return False
        et = event.type()
        if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            # Only initiate a drag if the user is NOT clicking on an
            # interactive child (button, checkbox, combo, etc.).
            # We rely on children having already received & possibly
            # accepted the event — the filter sees events that bubble up.
            # To be safe we always record; the move handler is gated by
            # distance so a quick click on the title bar won't move.
            self._drag_press_pos = event.globalPos()
            self._maybe_drag = True
        elif et == QEvent.MouseMove and self._maybe_drag:
            if self._drag_press_pos is None:
                self._maybe_drag = False
            else:
                dx = event.globalPos().x() - self._drag_press_pos.x()
                dy = event.globalPos().y() - self._drag_press_pos.y()
                if abs(dx) + abs(dy) > 4:
                    try:
                        self.move(self.x() + dx, self.y() + dy)
                        self._drag_press_pos = event.globalPos()
                        event.accept()
                    except Exception:
                        pass
        elif et == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self._maybe_drag:
                self._maybe_drag = False
                self._drag_press_pos = None
                self._save_settings()
        elif et == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Left, Qt.Key_Right):
                focus = QApplication.focusWidget()
                if focus is not None and isinstance(focus, QLineEdit):
                    return False
                if key == Qt.Key_Left:
                    self._shift_date(-1)
                else:
                    self._shift_date(1)
                event.accept()
                return True
            elif key == Qt.Key_T and not event.modifiers():
                focus = QApplication.focusWidget()
                if focus is not None and isinstance(focus, QLineEdit):
                    return False
                self._go_today()
                event.accept()
                return True
        return False

    def _build_title_bar(self, parent_layout):
        title_widget = QFrame()
        title_widget.setFixedHeight(44)
        title_widget.setStyleSheet("""
            background-color: rgba(30, 30, 40, 200);
            border-top-left-radius: 16px;
            border-top-right-radius: 16px;
        """)

        layout = QHBoxLayout(title_widget)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(4)

        title_label = QLabel("🏆")
        title_label.setStyleSheet("font-size: 15px;")
        layout.addWidget(title_label)

        quick_style = """
            QPushButton {
                background-color: rgba(0, 122, 255, 0.15);
                color: #64b5f6;
                border: 1px solid rgba(0, 122, 255, 0.25);
                border-radius: 6px; font-size: 11px; font-weight: bold; padding: 0 8px;
            }
            QPushButton:hover { background-color: rgba(0, 122, 255, 0.3); }
            QPushButton:pressed { background-color: rgba(0, 122, 255, 0.4); }
        """

        self.yesterday_btn = QPushButton("昨天")
        self.yesterday_btn.setFixedHeight(28)
        self.yesterday_btn.setToolTip("查看昨天的比赛")
        self.yesterday_btn.setStyleSheet(quick_style)
        self.yesterday_btn.clicked.connect(lambda: self._shift_date(-1))
        layout.addWidget(self.yesterday_btn)

        self.today_btn = QPushButton("今天")
        self.today_btn.setFixedHeight(28)
        self.today_btn.setToolTip("回到今天")
        self.today_btn.setStyleSheet(quick_style)
        self.today_btn.clicked.connect(self._go_today)
        layout.addWidget(self.today_btn)

        self.tomorrow_btn = QPushButton("明天")
        self.tomorrow_btn.setFixedHeight(28)
        self.tomorrow_btn.setToolTip("查看明天的比赛")
        self.tomorrow_btn.setStyleSheet(quick_style)
        self.tomorrow_btn.clicked.connect(lambda: self._shift_date(1))
        layout.addWidget(self.tomorrow_btn)

        self.prev_btn = QPushButton("◀")
        self.prev_btn.setFixedSize(28, 28)
        self.prev_btn.setToolTip("前一天")
        self.prev_btn.setStyleSheet(self._nav_btn_style())
        self.prev_btn.clicked.connect(lambda: self._shift_date(-1))
        layout.addWidget(self.prev_btn)

        self.date_label = QLabel(self._date_display())
        self.date_label.setStyleSheet("font-size: 13px; color: #e0e0f0; font-weight: bold; padding: 0 6px;")
        self.date_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.date_label)

        self.next_btn = QPushButton("▶")
        self.next_btn.setFixedSize(28, 28)
        self.next_btn.setToolTip("后一天")
        self.next_btn.setStyleSheet(self._nav_btn_style())
        self.next_btn.clicked.connect(lambda: self._shift_date(1))
        layout.addWidget(self.next_btn)
        layout.addStretch()

        # Pin button
        self.pin_btn = QPushButton("📌" if self._is_pinned else "📍")
        self.pin_btn.setFixedSize(32, 32)
        self.pin_btn.setToolTip("置顶中（点击取消）" if self._is_pinned else "未置顶（点击置顶）")
        self.pin_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent; border: none;
                border-radius: 8px; font-size: 15px;
            }
            QPushButton:hover { background-color: rgba(60,60,80,0.5); }
            QPushButton:pressed { background-color: rgba(80,80,100,0.5); }
        """)
        self.pin_btn.clicked.connect(self._toggle_pin)
        layout.addWidget(self.pin_btn)

        # Minimize button removed — use macOS dock "Hide" instead, which
        # is the only reliable hide/restore path on macOS.

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(32, 32)
        close_btn.setToolTip("关闭")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent; border: none;
                border-radius: 8px; font-size: 16px; color: #c0c0d0;
            }
            QPushButton:hover { background-color: rgba(255,60,60,0.4); color: #ffffff; }
            QPushButton:pressed { background-color: rgba(255,60,60,0.6); }
        """)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        parent_layout.addWidget(title_widget)

    # ---- Timer ----

    def _setup_timer(self):
        """Setup auto-refresh timer + countdown timer."""
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._on_timer_tick)
        self.refresh_timer.start(self.refresh_interval * 1000)

        # Countdown timer: updates upcoming match countdowns every 60s
        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self._update_countdowns)
        self.countdown_timer.start(60000)

    def _on_timer_tick(self):
        """Called every refresh interval.

        Refresh policy (tuned to avoid wasting API calls):
        - Viewing today → full refresh at user's chosen interval
          BUT skip refresh if all today's matches are finished — nothing
          changes, and the API has nothing new to return.
        - Viewing a past date → skip (matches are finished, nothing changes)
        - Viewing a future date → serve from bulk cache (already loaded
          on startup), no API call needed; the local 60s countdown
          timer still updates the displayed kickoff countdown.

        Smart skip: if the EARLIEST upcoming match on today's date is
        more than 30 minutes away AND there are no live matches, we
        skip the refresh too. ESPN doesn't update scheduled matches
        between kickoff minus ~10 min, so polling for them is wasted
        quota. The countdown timer (60s) still updates the local clock.

        Note: when today's match is in progress, we DO want to refresh
        every user-chosen interval (30s/60s/...) so the score updates
        show up. The previous behaviour of "future dates refresh every
        1 minute" is now superseded by the bulk cache.
        """
        today = get_beijing_today()
        cmp = compare_beijing_dates(self.current_date, today)

        # Detect day rollover: switch to new today
        if self._last_known_today != today:
            self._last_known_today = today
            # If we were viewing yesterday/older, jump back to today on rollover
            if self.current_date != today:
                self.current_date = today
                self.date_label.setText(self._date_display())
            # Reset interval to user setting in case we slowed it down
            self.refresh_timer.setInterval(self.refresh_interval * 1000)
            self.api._cache_fetched = False
            self._refresh_data()
            return

        # Past date: no need to refresh, matches are over
        if cmp < 0:
            if self.refresh_timer.interval() != self.refresh_interval * 1000:
                self.refresh_timer.setInterval(self.refresh_interval * 1000)
            return

        # Viewing today: skip refresh if all today's matches are finished
        if cmp == 0 and self._all_matches_finished():
            if self.refresh_timer.interval() != self.refresh_interval * 1000:
                self.refresh_timer.setInterval(self.refresh_interval * 1000)
            return

        # SMART SKIP: if no live match is in progress AND the next match
        # is more than 30 minutes away, don't bother hitting ESPN.
        # ESPN doesn't push any updates for matches until ~10 min before
        # kickoff, so polling for them is pure wasted quota. The local
        # 60s countdown timer still updates the displayed "X 分钟后"
        # text, so the user still sees a real-time countdown without
        # the API being hammered.
        if cmp == 0 and not self._has_live_match():
            next_min = self._next_match_minutes_away()
            if next_min > 30:
                # Stretch interval to 5 minutes — local countdown still
                # ticks every minute. As kickoff approaches (< 30 min)
                # we automatically go back to user-chosen interval.
                if self.refresh_timer.interval() != 300_000:
                    self.refresh_timer.setInterval(300_000)
                # Keep countdown text fresh even though we skipped the
                # network call.
                self._update_countdowns()
                return
            # Within 30 min of kickoff: full normal refresh at user
            # setting, so when the match actually starts we're fresh.
            if self.refresh_timer.interval() != self.refresh_interval * 1000:
                self.refresh_timer.setInterval(self.refresh_interval * 1000)

        # Need to actually refresh — reset the API cache so the worker
        # fetches fresh data (not the cached "all matches" list).

        # Future date: serve from the bulk cache. The bulk fetch
        # happened once on startup and covers 22 days, so we
        # never need to call the API for a future date. The
        # local 60s countdown timer still updates the displayed
        # "X 分钟后" text so the user sees a real-time kickoff
        # countdown without burning API quota.
        if cmp > 0:
            if self.refresh_timer.interval() != 60_000:
                self.refresh_timer.setInterval(60_000)
            # If the cache covers this date, render from cache.
            # If not (shouldn't happen with 22-day window), fall
            # through to the network-fetch path below.
            if self.current_date in self._bulk_cache:
                self._refresh_data()
                return
            # No cache coverage — fall through to network fetch.
            # (This branch only triggers if the user navigates
            # beyond 7/20 via the date picker.)

        # Viewing today with live or upcoming matches: normal refresh at user setting
        if self.refresh_timer.interval() != self.refresh_interval * 1000:
            self.refresh_timer.setInterval(self.refresh_interval * 1000)
        self.api._cache_fetched = False
        self._refresh_data()

    def _all_matches_finished(self) -> bool:
        """True if there are matches loaded for the current date and all are finished.
        Empty match list (no data yet / API failure) returns False so we still try
        to fetch and recover."""
        if not self.matches:
            return False
        return all(m.status == "finished" for m in self.matches)

    def _next_match_minutes_away(self) -> int:
        """Return minutes until the next notstarted match kicks off.
        Returns a very large number if there's no upcoming match today.
        Used by the smart-skip logic to decide whether to bother hitting
        the API on a tick."""
        import time as _time
        from datetime import datetime as _dt
        now = _dt.now(BEIJING_TZ)
        soonest = None
        for m in self.matches:
            if m.status != "notstarted" or not m.local_date:
                continue
            # m.local_date looks like "07/04/2026 09:30" — Beijing time
            try:
                parts = m.local_date.split(" ", 1)
                if len(parts) != 2:
                    continue
                date_part, time_part = parts
                mm, dd, yyyy = date_part.split("/")
                hh, mn = time_part.split(":")
                kickoff = _dt(int(yyyy), int(mm), int(dd), int(hh), int(mn),
                               tzinfo=BEIJING_TZ)
                delta_min = (kickoff - now).total_seconds() / 60.0
                if soonest is None or delta_min < soonest:
                    soonest = delta_min
            except Exception:
                continue
        return soonest if soonest is not None else 9999

    def _has_live_match(self) -> bool:
        return any(m.status in LIVE_STATUSES for m in self.matches)

    def _update_countdowns(self):
        """Update countdown text on all visible cards."""
        for card in self.card_widgets:
            if hasattr(card, 'update_countdown'):
                card.update_countdown()

    # ---- Date helpers ----

    @staticmethod
    def _nav_btn_style():
        return """
            QPushButton {
                background-color: transparent; border: none;
                border-radius: 6px; color: #a0a0c0; font-size: 12px;
            }
            QPushButton:hover { background-color: rgba(60,60,80,0.5); color: #e0e0f0; }
            QPushButton:pressed { background-color: rgba(80,80,100,0.5); }
        """

    def _date_display(self):
        today = get_beijing_today()
        try:
            month, day, year = self.current_date.split("/")
            date_obj = datetime(int(year), int(month), int(day))
            weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
            wd = weekday_names[date_obj.weekday()]
            display = f"{int(month)}月{int(day)}日 周{wd}"
            if self.current_date == today:
                display += " (今天)"
            return display
        except Exception:
            return self.current_date

    def _shift_date(self, days: int):
        try:
            month, day, year = self.current_date.split("/")
            dt = datetime(int(year), int(month), int(day)) + timedelta(days=days)
            # Clamp to the World Cup window: 2026-06-11 (start) ..
            # 2026-07-19 (last scheduled match in UTC = 7/20 BJ
            # bracket final). Beyond 7/20 ESPN returns an empty
            # events list, so we hard-stop the arrow buttons.
            wc_min = datetime(2026, 6, 11)
            wc_max = datetime(2026, 7, 20)
            if dt < wc_min:
                dt = wc_min
            if dt > wc_max:
                dt = wc_max
            self.current_date = dt.strftime("%m/%d/%Y")
            self.date_label.setText(self._date_display())
            self._refresh_data()
        except Exception:
            pass

    def _go_today(self):
        self.current_date = get_beijing_today()
        self.date_label.setText(self._date_display())
        self._refresh_data()

    def _show_date_picker(self):
        """Show date picker dialog."""
        dialog = DatePickDialog(self.current_date, self)
        # Keep dialog above main window
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        if dialog.exec_() == QDialog.Accepted:
            self.current_date = dialog.get_date_str()
            self.date_label.setText(self._date_display())
            self._refresh_data()

    # ---- Drag handling ----

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
                event.accept()
        except RuntimeError:
            # Underlying C++ object already gone (e.g. async deletion race)
            pass

    def mouseMoveEvent(self, event):
        try:
            if self.buttons() == Qt.LeftButton and self._drag_pos is not None:
                self.move(event.globalPos() - self._drag_pos)
                event.accept()
        except (RuntimeError, AttributeError, TypeError):
            # sipQWidget/move failure on stale Python wrapper during async
            # deleteLater() — silently ignore to avoid SIGABRT.
            pass

    def mouseReleaseEvent(self, event):
        try:
            if self._drag_pos is not None:
                self._save_settings()
        except Exception:
            pass
        self._drag_pos = None

    # ---- Data Refresh ----

    def _refresh_data(self):
        """Fetch latest match data in background thread (with re-entry guard & debounce).

        Debounce policy: only ignore rapid clicks that come within 2s of the
        last *successful* refresh trigger. If a click is debounced, we
        schedule a delayed retry so fast date switching still works.
        """
        # Serve from the bulk cache when possible. This is the
        # primary optimization: we already pulled a 22-day window
        # on startup, so flipping between dates doesn't burn API
        # quota. We only fall back to a per-date network fetch if
        # the cache doesn't have this date yet.
        if self.current_date in self._bulk_cache:
            cached = self._bulk_cache[self.current_date]
            matches = cached[0] if isinstance(cached, tuple) else cached
            success = True
            self._on_data_ready(matches, self.current_date, success)
            return

        # If the bulk fetch is still running and we don't have this
        # date in cache yet, wait for it. Otherwise we'd race a
        # network fetch against the in-flight bulk and waste quota.
        if self._bulk_fetcher and self._bulk_fetcher.isRunning():
            self.status_bar.setText("正在预加载赛程数据...")
            return

        # The cache is fully populated and this date is NOT in it.
        # For a future date this means the World Cup has no matches
        # scheduled on this BJ day (e.g. 7/9 is a rest day). For a
        # past date it means the cache was populated and the day is
        # empty. Either way, the right move is to render an empty
        # list — don't burn API quota trying to re-fetch.
        # We treat "no entry in cache" as authoritative "no matches
        # scheduled" because the bulk fetch covered 22 days, which
        # exceeds the World Cup's playable window.
        today = get_beijing_today()
        if compare_beijing_dates(self.current_date, today) != 0 and self._bulk_loaded:
            # Empty matches list, but it's a definitive answer.
            self._on_data_ready([], self.current_date, True)
            return

        now = time.time()
        if now - self._last_refresh_time < 2:
            # Debounced — but don't swallow the request. Schedule a retry
            # so fast date switching doesn't lose updates.
            remaining_ms = int((2 - (now - self._last_refresh_time)) * 1000) + 50
            QTimer.singleShot(max(remaining_ms, 50), self._refresh_data)
            return
        self._last_refresh_time = now

        # Prevent concurrent requests — if a worker is still running,
        # mark pending and fetch after it finishes (but don't cascade)
        if self._fetch_worker and self._fetch_worker.isRunning():
            self._pending_refresh = True
            return
        self._pending_refresh = False
        self._retry_count = 0
        self._is_refreshing = True
        self.status_bar.setText("正在加载比赛当前数据...")
        self._fetch_worker = FetchWorker(self.api, self.current_date)
        self._fetch_worker.data_ready.connect(self._on_data_ready)
        self._fetch_worker.finished.connect(self._on_fetch_finished)
        self._fetch_worker.start()

    def _start_bulk_fetch(self):
        """Kick off the bulk pre-load (yesterday + today + 20 future
        days) in a background thread. The result is delivered via
        `_on_bulk_ready` and stored in self._bulk_cache.
        """
        if self._bulk_fetcher and self._bulk_fetcher.isRunning():
            return
        self._bulk_fetcher = BulkFetchWorker(
            self.api, get_beijing_today(),
            past_days=1, future_days=self._bulk_window_days,
        )
        self._bulk_fetcher.bulk_ready.connect(self._on_bulk_ready)
        self._bulk_fetcher.finished.connect(self._on_bulk_finished)
        self._bulk_fetcher.start()

    def _on_bulk_ready(self, by_date: dict):
        """Bulk cache populated. If the user is still viewing today,
        the in-flight _refresh_data() will already have hit the
        empty cache and triggered a fallback fetch — that one will
        just be replaced with the cache contents via the next
        refresh tick."""
        for bj_date, matches in by_date.items():
            # Enrich ET scores for AET matches (same as the
            # per-date path does)
            self._enrich_matches_with_et_scores(matches)
            self._bulk_cache[bj_date] = (matches, time.time(), True)
        self._bulk_loaded = True
        # If the user is currently viewing a date that the cache
        # now covers, refresh the UI from cache.
        if self.current_date in by_date:
            self._refresh_data()

    def _on_bulk_finished(self):
        # Bulk thread done; nothing else to do — the cache is the
        # only output.
        pass

    def _on_data_ready(self, matches: list, date_str: str, success: bool):
        if date_str != self.current_date:
            return

        if success:
            self._has_ever_loaded = True
            self._retry_count = 0

        if matches:
            # Check for notifications (only on today's auto-refresh, not first load)
            if date_str == get_beijing_today() and self._prev_matches_state:
                self._check_notifications(matches)

            # Enrich matches with ET (extra time) goal counts by
            # hitting the summary endpoint for any AET match. This
            # is what makes the "加时 (X : Y)" card row appear, like
            # the "点球 (X : Y)" row for shootouts.
            self._enrich_matches_with_et_scores(matches)

            self.matches = matches
            # Stash the matches in the bulk cache so subsequent
            # date-flips (back to this date) are instant.
            self._bulk_cache[date_str] = (matches, time.time(), True)
            self._rebuild_match_cards()
            self._update_status()
            # Force complete UI refresh — fixes blank cards after auto-refresh
            self.scroll_area.setVisible(True)
            self.scroll_area.viewport().update()
            self.scroll_area.repaint()
            self.repaint()
        elif success:
            # API succeeded but no matches for this date — clear old data
            self.matches = []
            self._rebuild_match_cards()
            self._update_status()
            self.update()
        elif not success and self._retry_count < self._max_retries:
            self._retry_count += 1
            self.status_bar.setText(f"加载失败，5秒后第{self._retry_count}次重试...")
            QTimer.singleShot(5000, self._retry_fetch)
        elif not success:
            self.status_bar.setText("加载失败，请检查网络后重试")

    def _retry_fetch(self):
        self.api._cache_fetched = False
        self._fetch_worker = None  # clear to allow new fetch
        self._retry_count = 0
        self._refresh_data()

    def _on_fetch_finished(self):
        """Called when FetchWorker thread finishes."""
        self._is_refreshing = False
        self._fetch_worker = None
        if self._pending_refresh and not self._is_refreshing:
            self._pending_refresh = False
            # Use QTimer.singleShot to avoid re-entering during signal handling
            QTimer.singleShot(200, self._refresh_data)

    # ---- Notifications ----

    def _check_notifications(self, new_matches: list):
        """Detect match starts and score changes, send macOS + PushPlus notifications."""
        live_now = {"1h", "2h", "ht", "et", "pen", "live"}
        for m in new_matches:
            prev = self._prev_matches_state.get(m.match_id)
            if prev is None:
                continue
            prev_status, prev_home, prev_away = prev

            # Match just started
            if prev_status == "notstarted" and m.status in live_now:
                self._send_notification(
                    f"⚽ {m.home_team} vs {m.away_team}",
                    "比赛开始！"
                )
                if self._pushplus_on_start:
                    title, content = self._format_match_push(m, "start")
                    self._send_pushplus(title, content)

            # Score change: covers goals, VAR confirmations, score corrections,
            # and goal cancellations. We report whatever the new score is,
            # so the user gets accurate updates either way.
            score_changed = (m.home_score != prev_home or m.away_score != prev_away)
            if score_changed:
                home_delta = m.home_score - prev_home
                away_delta = m.away_score - prev_away
                # Build a descriptive title based on delta direction
                if home_delta > 0 and away_delta > 0:
                    title = "⚽ 比分变化"
                    delta_hint = f"（+{home_delta} / +{away_delta}）"
                elif home_delta > 0:
                    title = "⚽ 比分变化"
                    delta_hint = f"（{m.home_team} +{home_delta}）"
                elif away_delta > 0:
                    title = "⚽ 比分变化"
                    delta_hint = f"（{m.away_team} +{away_delta}）"
                elif home_delta < 0 or away_delta < 0:
                    title = "🔄 比分修正"
                    parts = []
                    if home_delta < 0:
                        parts.append(f"{m.home_team} {home_delta:+d}")
                    if away_delta < 0:
                        parts.append(f"{m.away_team} {away_delta:+d}")
                    delta_hint = f"（{' / '.join(parts)}）"
                else:
                    # Shouldn't happen, but just in case
                    title = "⚽ 比分变化"
                    delta_hint = ""

                self._send_notification(
                    title,
                    f"{m.home_team} {m.home_score}-{m.away_score} {m.away_team} {delta_hint}".strip()
                )
                if self._pushplus_on_goal:
                    # For goal pushes we want the title to BE the action:
                    # "X 进球 (Y分钟) 1-0" — no separate body. We use the
                    # summary endpoint to get scorer / method (头球/点球/...).
                    # We only do this for "real" goals (delta > 0). For
                    # corrections (delta < 0) we fall back to the
                    # descriptive title.
                    if home_delta > 0 or away_delta > 0:
                        title, content = self._format_goal_push(m, home_delta, away_delta)
                    else:
                        title, content = self._format_match_push(
                            m, "score_change", home_delta, away_delta
                        )
                    self._send_pushplus(title, content)

        # Detect whether the data actually changed vs the previous fetch.
        # Used by the status bar to flag "stale data" when a live match's
        # score hasn't moved for a while (e.g. football-data.org free tier
        # doesn't always push live updates).
        data_changed = False
        for m in new_matches:
            prev = self._prev_matches_state.get(m.match_id)
            if prev is None:
                continue
            prev_status, prev_home, prev_away = prev
            if prev_status != m.status or prev_home != m.home_score or prev_away != m.away_score:
                data_changed = True
                break
        # Also detect "first time we see this match today" as a change
        if not data_changed and self._prev_matches_state:
            for m in new_matches:
                if m.match_id not in self._prev_matches_state:
                    data_changed = True
                    break
        if data_changed:
            self._last_data_change_time = time.time()

        # Update state
        self._prev_matches_state = {
            m.match_id: (m.status, m.home_score, m.away_score)
            for m in new_matches
        }

    @staticmethod
    def _send_notification(title: str, body: str):
        """Send a macOS system notification."""
        try:
            safe_body = body.replace('"', '\\"')
            safe_title = title.replace('"', '\\"')
            subprocess.Popen([
                "osascript", "-e",
                f'display notification "{safe_body}" with title "{safe_title}" sound name "Glass"'
            ])
        except Exception:
            pass

    def _send_pushplus(self, title: str, content: str):
        """Send a push notification via PushPlus (pushplus.plus).
        Supports multiple tokens separated by '/' — each token gets its own push.
        Silently no-ops if no valid tokens or all requests fail."""
        raw = getattr(self, "_pushplus_token", "")
        if not raw:
            return
        # Split on '/' and strip whitespace; ignore empty fragments
        tokens = [t.strip() for t in raw.split("/") if t.strip()]
        if not tokens:
            return
        url = "http://www.pushplus.plus/send"
        headers = {"Content-Type": "application/json"}
        for token in tokens:
            try:
                payload = {
                    "token": token,
                    "title": title,
                    "content": content,
                }
                requests.post(url, json=payload, headers=headers, timeout=5)
            except Exception:
                # One bad token shouldn't stop the others from receiving the push
                continue

    def _format_match_push(self, m, kind: str, home_delta: int = 0, away_delta: int = 0) -> tuple:
        """Return (title, content) for a PushPlus push.
        kind: 'start' or 'score_change'.
        For 'score_change', home_delta / away_delta are the score change
        (e.g. +1 = goal, -1 = VAR cancellation, 0 = unchanged for that side)."""
        # Build a human-readable stage tag for score_change pushes
        stage_tag = ""
        if kind == "score_change":
            stage_map = {
                "1h": "上半场",
                "2h": "下半场",
                "ht": "中场休息",
                "et": "加时赛",
                "pen": "点球大战",
                "live": "进行中",
                "finished": "已结束",
                "notstarted": "未开始",
            }
            stage_cn = stage_map.get(m.status)
            if stage_cn:
                stage_tag = f" · {stage_cn}"

        if kind == "start":
            title = f"⚽ 比赛开始啦 {m.home_team} VS {m.away_team}"
            content = (
                f"{m.home_team} vs {m.away_team}\n"
                f"当前比分：{m.home_score} - {m.away_score}\n"
                f"开球啦，快来围观 🏟"
            )
        else:  # score_change
            # Title reflects whether it's a goal, a correction, or both
            has_goal = home_delta > 0 or away_delta > 0
            has_correction = home_delta < 0 or away_delta < 0
            if has_goal and has_correction:
                title = "🔄 比分有调整"
            elif has_correction:
                title = "🔄 比分修正（进球取消）"
            else:
                title = "⚽ 进球啦！"

            # Build a delta hint line
            delta_parts = []
            if home_delta != 0:
                delta_parts.append(f"{m.home_team} {home_delta:+d}")
            if away_delta != 0:
                delta_parts.append(f"{m.away_team} {away_delta:+d}")
            delta_line = "（" + " / ".join(delta_parts) + "）" if delta_parts else ""

            content = (
                f"{m.home_team} vs {m.away_team}\n"
                f"当前比分：{m.home_score} - {m.away_score}{stage_tag}\n"
                f"{delta_line}".rstrip()
            )
        return title, content

    def _format_goal_push(self, m, home_delta: int = 0, away_delta: int = 0) -> tuple:
        """Format a goal push.

        TITLE (single line, primary info):
            ⚽ 14' 霍恩·阿里亚斯（哥伦比亚）进球  1-0
            = minute + scorer (CN) + team (CN) + method + cumulative score

        CONTENT (multi-line, full goal log for THIS match):
            哥伦比亚 1-0 加纳  进球明细：
              14' 霍恩·阿里亚斯（哥伦比亚）进球  1-0

        (Multiple goals: each on its own line.)

        Falls back to a generic title if the summary endpoint is
        unreachable. content may be "" only if BOTH the summary
        call failed AND no goals are known.
        """
        scorer = "未知球员"
        scorer_en = ""
        method = "进球"
        minute = ""
        score_str = f"{m.home_score}-{m.away_score}"
        team_cn = ""
        all_goals = []  # list of dicts (chronological)

        try:
            summary = self.api.fetch_match_summary(m.match_id)
            info = self.api.extract_last_goal_from_summary(summary, m)
            if info:
                scorer = info.get("scorer") or scorer
                scorer_en = info.get("scorer_en") or ""
                method = info.get("method") or method
                minute = info.get("minute") or ""
                score_str = info.get("score") or score_str
                team_cn = info.get("team_cn") or team_cn
            all_goals = self.api.extract_all_goals_from_summary(summary, m)
        except Exception:
            pass

        # --- Title: last goal, one line ---
        if team_cn:
            head_team = f"（{team_cn}）"
        else:
            head_team = ""

        if minute and minute not in ("0'", "0''"):
            title = f"⚽ {minute} {scorer}{head_team} {method}  {score_str}"
        else:
            title = f"⚽ {scorer}{head_team} {method}  {score_str}"

        # --- Content: full goal log for this match ---
        # Always show the current score header so the user can see
        # "the final" + "all goals" at a glance.
        if all_goals:
            lines = [f"{m.home_team} {m.home_score}-{m.away_score} {m.away_team}  进球明细："]
            for g in all_goals:
                g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                g_minute = g.get('minute', '')
                lines.append(
                    f"  {g_minute} {g['scorer']}{g_team} {g['method']}  {g['score']}"
                )
            content = "\n".join(lines)
        else:
            content = ""

        return title, content

    def _enrich_matches_with_et_scores(self, matches: list):
        """For each match that's in ET or finished-after-ET, fetch the
        summary endpoint and set match.home_extra_time /
        match.away_extra_time so the card can render
        "加时 (X : Y)".

        The scoreboard endpoint doesn't include keyEvents, so we have
        to hit the summary endpoint to count goals from period 3/4.
        We do this synchronously here because the card renderer
        depends on these values being populated before the UI rebuilds.
        For today + 3 days that's at most a handful of HTTP calls,
        which is acceptable on the worker thread (we're already
        in _on_data_ready on the main thread, but each summary call
        is cached for 30s and most of the time we have 0-1 AET
        matches per fetch).
        """
        for m in matches:
            if not getattr(m, "has_extra_time", False):
                continue
            try:
                summary = self.api.fetch_match_summary(m.match_id)
                h, a, ok = self.api.extract_et_scores_from_summary(summary)
                if ok:
                    m.home_extra_time = h
                    m.away_extra_time = a
            except Exception:
                pass

    # ---- Card management (safe rebuild) ----

    def _rebuild_match_cards(self):
        """Rebuild match card widgets safely — no orphaned widgets or visual ghosts."""
        # Snapshot current cards for reuse analysis, then clear the list immediately.
        # This prevents _update_countdowns timer from accessing cards that are
        # scheduled for async deletion (deleteLater) but not yet destroyed.
        old_cards_snapshot = list(self.card_widgets)
        self.card_widgets = []

        if not self.matches:
            # Clear everything first (prevent ghost artifacts)
            self._clear_matches_layout()
            if not self._has_ever_loaded:
                empty_text = "⏳\n正在加载比赛当前数据..."
            else:
                empty_text = "🏟\n该日期没有比赛"
            self._show_empty_card(empty_text)
            return

        # Filter: live-only mode
        display_matches = self.matches
        if self._live_only:
            live_statuses = {"1h", "2h", "ht", "et", "pen"}
            display_matches = [m for m in self.matches if m.status in live_statuses]

        if not display_matches:
            self._clear_matches_layout()
            if self._live_only:
                total = len(self.matches)
                finished = sum(1 for m in self.matches if m.status == "finished")
                upcoming = sum(1 for m in self.matches if m.status == "notstarted")
                parts = []
                if finished:
                    parts.append(f"{finished}场已结束")
                if upcoming:
                    parts.append(f"{upcoming}场未开始")
                detail = "、".join(parts) if parts else f"共{total}场比赛"
                empty_text = f"🏟\n暂无进行中的比赛\n({detail})"
            else:
                empty_text = "🏟\n该日期没有比赛"
            self._show_empty_card(empty_text)
            return

        # Sort: live first, then upcoming, then finished
        def sort_key(m):
            order = {"1h": 0, "2h": 0, "ht": 0, "et": 0, "pen": 0,
                     "notstarted": 1, "finished": 2}
            return (order.get(m.status, 9), m.local_date)

        display_matches.sort(key=sort_key)

        self.scroll_area.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        # Safe rebuild: determine what to reuse vs create/delete BEFORE touching the layout
        old_card_map = {card.match.match_id: card for card in old_cards_snapshot}
        new_match_ids = {m.match_id for m in display_matches}

        # Identify cards to keep vs delete
        cards_to_keep = set()
        cards_to_delete = []
        for mid, card in old_card_map.items():
            if mid in new_match_ids:
                cards_to_keep.add(mid)
            else:
                cards_to_delete.append(card)

        # Delete unwanted cards: hide + disable FIRST, then async delete
        # (deleteLater is async; without hide() the widget can still receive
        #  mouse events before the C++ object is actually destroyed → crash)
        for card in cards_to_delete:
            card.hide()
            card.setDisabled(True)
            card.blockSignals(True)
            card.setParent(None)
            card.deleteLater()

        # Temporarily detach cards we want to reuse (prevents ghosts during transition)
        kept_cards = {}
        for mid in cards_to_keep:
            card = old_card_map[mid]
            card.setParent(None)   # detach from scroll area
            kept_cards[mid] = card

        # NOW clear the layout (all widgets already detached, only layout items remain)
        self._clear_matches_layout()

        # Build new card list in sorted order
        self.card_widgets = []
        for match in display_matches:
            if match.match_id in kept_cards:
                card = kept_cards[match.match_id]
                card.update_match(match)
            else:
                card = MatchCard(match, self.flag_loader)
            # Explicitly show() — critical on macOS: after setParent(None)→addWidget(),
            # widgets may remain hidden without this, causing blank card frames.
            card.show()
            self.card_widgets.append(card)
            self.matches_layout.addWidget(card)

        self.matches_layout.addStretch()

        # Force scroll area viewport to repaint (fixes blank screen after refresh)
        self.scroll_area.viewport().update()

        # Process pending layout/paint events immediately so cards render
        # without this, macOS may defer rendering and show blank frames
        QApplication.processEvents()

        # Calculate height
        card_count = len(display_matches)
        total_height = 44 + 24 + card_count * 115 + 10 + 28
        if total_height > WINDOW_MAX_HEIGHT:
            total_height = WINDOW_MAX_HEIGHT
        elif total_height < WINDOW_MIN_HEIGHT:
            total_height = WINDOW_MIN_HEIGHT
        self._update_height(total_height)

    def _clear_matches_layout(self):
        """Safely clear all items from matches_layout.
        Any widgets still in the layout get hide→disable→detach→delete.
        """
        while self.matches_layout.count():
            item = self.matches_layout.takeAt(0)
            widget = item.widget()
            if widget:
                # Must hide + disable BEFORE detach/deleteLater.
                # deleteLater is async; without hide() Qt can still deliver
                # mouse events to the alive-but-orphaned C++ widget.
                widget.hide()
                widget.setDisabled(True)
                widget.blockSignals(True)
                widget.setParent(None)
                widget.deleteLater()

    def _show_empty_card(self, text: str):
        """Show a centered empty-state card."""
        self.card_widgets = []
        self.matches_layout.addStretch(1)

        card = QFrame()
        card.setFixedHeight(105)
        card.setStyleSheet("""
            QFrame {
                background-color: rgba(42, 42, 56, 210);
                border: 1px solid rgba(60, 60, 80, 100);
                border-radius: 12px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setAlignment(Qt.AlignCenter)
        empty_label = QLabel(text)
        empty_label.setAlignment(Qt.AlignCenter)
        empty_label.setStyleSheet("font-size: 16px; color: #606080; border: none;")
        card_layout.addWidget(empty_label)
        self.matches_layout.addWidget(card)

        self.matches_layout.addStretch()
        self.scroll_area.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._update_height(44 + 24 + 115 + 10 + 28)

    def _update_height(self, height: int):
        self.resize(WINDOW_WIDTH, height)

    def _update_status(self):
        now_str = datetime.now(BEIJING_TZ).strftime("%Y年%m月%d日 %H:%M:%S")
        match_count = len(self.matches)

        # Show rate limit warning
        if self.api.is_rate_limited():
            now = datetime.now(BEIJING_TZ).strftime("%H:%M:%S")
            self.status_bar.setText(f"⚠ API限流中，稍后自动恢复 · {now}")
            return

        today = get_beijing_today()
        cmp = compare_beijing_dates(self.current_date, today)
        all_done_today = (cmp == 0 and self._all_matches_finished())

        if match_count == 0:
            if cmp < 0:
                # Past date — no auto refresh, no need to show interval
                self.status_bar.setText(f"📅 {self._date_display()} · 已结束 · 当前数据 {now_str}")
            else:
                self.status_bar.setText(f"📅 {self._date_display()} · 暂无比赛 · 当前数据 {now_str}")
        else:
            live_count = sum(1 for m in self.matches if m.status in ("1h", "2h", "ht", "et", "pen"))
            finished_count = sum(1 for m in self.matches if m.status == "finished")
            filter_tag = "🟢 进行中 · " if self._live_only else ""

            # If there are live matches and we haven't seen a score change
            # in a while, mark the data as potentially stale. football-data.org
            # v4 free tier doesn't always update live scores promptly.
            stale_tag = ""
            if live_count > 0 and cmp == 0 and self._last_data_change_time:
                seconds_since_change = time.time() - self._last_data_change_time
                if seconds_since_change > 120:
                    mins = int(seconds_since_change // 60)
                    stale_tag = f" ⚠ 数据可能滞后{mins}分钟"

            if cmp < 0:
                # Past date — show real data timestamp, no interval
                base = f"{filter_tag}✅ {match_count}场已结束 · 当前数据 {now_str}"
            elif live_count > 0:
                base = f"{filter_tag}{live_count}场进行中 · 共{match_count}场 · 当前数据 {now_str}"
            elif finished_count == match_count:
                base = f"{filter_tag}✅ {match_count}场已结束 · 当前数据 {now_str}"
            else:
                base = f"{filter_tag}📋 共{match_count}场比赛 · 当前数据 {now_str}"
            # Show "间隔 X 秒" ONLY when there's at least one live match —
            # that's the only situation where the API actually has
            # fresh data to return on each tick. For everything else
            # (no live yet, all finished, viewing past/future) we just
            # show the data timestamp — no need to advertise a refresh
            # interval that doesn't produce new data.
            if live_count > 0:
                base += f" · 间隔{self.refresh_interval}秒"
            self.status_bar.setText(base)

    # ---- Settings & toggles ----

    def _toggle_live_filter(self):
        self._live_only = not self._live_only
        self._rebuild_match_cards()
        self._update_status()
        self._save_settings()
        self._save_settings()

    def _minimize_to_tray(self):
        """Hide the overlay window via the macOS-native 'Hide' command,
        which is the same as right-clicking the dock icon → Hide. The
        user can then click the dock icon to bring it back.

        On macOS this is the ONLY reliable way to hide-and-restore a
        window. Qt's hide()+show() sometimes doesn't actually surface
        the window on subsequent show, and NSEvent / activateIgnoring
        approaches are flaky across macOS versions. Going through
        NSApp.hide_ uses the exact same code path as the system
        Hide menu item.

        Falls back to self.hide() if PyObjC isn't available — in
        that case the user can still use right-click dock → Hide +
        dock click to restore, but our button's restore is best-effort.
        """
        try:
            self._save_settings()
            # macOS-native hide: goes through NSApplication so that
            # the subsequent dock-click restore works.
            try:
                if sys.platform == "darwin":
                    import objc  # type: ignore
                    from AppKit import NSApplication  # type: ignore
                    NSApplication.sharedApplication().hide_(None)
                else:
                    self.hide()
            except Exception:
                # No PyObjC — fall back to Qt hide. The user can still
                # right-click the dock icon and choose Hide, which uses
                # the same NSApp path.
                self.hide()
            # Build the menu bar icon (as a secondary affordance)
            if not hasattr(self, "_tray_icon") or self._tray_icon is None:
                self._tray_icon = self._create_tray_icon()
            # In-app shortcut for ⌘⇧W (works if the user is in this app)
            self._register_global_hotkey()
        except Exception:
            pass

    def _nswindow_order_out(self):
        """Order the underlying NSWindow out so the next orderFront
        is a true restore, not a no-op."""
        try:
            if sys.platform != "darwin":
                return
            import objc  # type: ignore
            from AppKit import NSApplication  # type: ignore
            win_id = int(self.winId())
            app = NSApplication.sharedApplication()
            # Find the NSWindow for this Qt widget
            for w in app.windows():
                if int(w.windowNumber()) == win_id or True:
                    # orderOut_ on every window is overkill but safe;
                    # we only have one anyway.
                    try:
                        w.orderOut_(None)
                    except Exception:
                        pass
                    break
        except Exception:
            pass

    def _nswindow_order_front(self):
        """Force the underlying NSWindow to the front via PyObjC. This
        is what dock-icon click does internally."""
        try:
            if sys.platform != "darwin":
                return
            import objc  # type: ignore
            from AppKit import NSApplication  # type: ignore
            app = NSApplication.sharedApplication()
            for w in app.windows():
                try:
                    w.makeKeyAndOrderFront_(None)
                except Exception:
                    pass
            app.activateIgnoringOtherApps_(True)
        except Exception:
            pass

    def _register_dock_click_handler(self):
        """Register a handler that brings the window back when the user
        clicks the dock icon. PyQt5's QApplication doesn't expose this
        directly, so we use NSApplication's applicationShouldHandleReopen_
        delegate via a custom NSApplicationDelegate."""
        try:
            if sys.platform != "darwin":
                return
            if getattr(self, "_dock_handler_installed", False):
                return
            import objc  # type: ignore
            from AppKit import (  # type: ignore
                NSApplication, NSObject, NSApplicationActivateIgnoringOtherApps,
            )

            # Create a delegate class that calls our restore method
            # when macOS asks the app to reopen (e.g. dock click).
            overlay_ref = self

            class _Delegate(NSObject):
                def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, flag):
                    try:
                        overlay_ref._restore_from_tray()
                    except Exception:
                        pass
                    return True

            delegate = _Delegate.alloc().init()
            app = NSApplication.sharedApplication()
            app.setDelegate_(delegate)
            self._dock_handler_installed = True
            self._dock_delegate = delegate
        except Exception:
            pass

    def _register_global_hotkey(self):
        """Register an in-app QShortcut for ⌘⇧W. This works as long
        as the user is focused on the overlay or any other window
        owned by the same app process."""
        try:
            if getattr(self, "_global_hotkey", None) is not None:
                return
            from PyQt5.QtWidgets import QShortcut
            from PyQt5.QtGui import QKeySequence
            seq = QKeySequence("Ctrl+Shift+W")
            shortcut = QShortcut(seq, self)
            shortcut.setShortcutContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(self._restore_from_tray)
            self._global_hotkey = shortcut
        except Exception:
            pass

    def _install_global_key_monitor(self):
        """Install a macOS global NSEvent monitor for ⌘⇧W that fires
        even when the user is focused on another app. Requires PyObjC.
        Falls back to a no-op otherwise."""
        if sys.platform != "darwin":
            return
        try:
            import objc  # type: ignore
            from AppKit import NSEvent  # type: ignore
        except Exception:
            return
        try:
            if getattr(self, "_key_monitor", None) is not None:
                return

            overlay_ref = self

            def _handler(event):
                try:
                    mods = event.modifierFlags()
                    # cmd = 1<<20, shift = 1<<17
                    if (mods & 0x100000) and (mods & 0x20000):
                        ch = event.charactersIgnoringModifiers()
                        if ch and ch.lower() == "w":
                            from PyQt5.QtCore import QMetaObject, Qt as _Qt
                            QMetaObject.invokeMethod(
                                overlay_ref, "_restore_from_tray",
                                _Qt.QueuedConnection,
                            )
                except Exception:
                    pass

            monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSEventMaskKeyDown, _handler
            )
            self._key_monitor = monitor
        except Exception:
            pass

    def _create_tray_icon(self):
        """Create a menu bar icon for restoring the hidden window.

        On macOS the menu bar icon lives in the top-right. We must give
        it a real pixmap icon — an empty QIcon won't show at all. The
        pixmap is generated at runtime so we don't have to bundle an
        asset file."""
        from PyQt5.QtWidgets import QSystemTrayIcon
        from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QFont

        # Build a 22x22 trophy emoji pixmap. macOS's system menu bar
        # needs an actual rendered image — an empty QIcon won't show.
        pix = QPixmap(22, 22)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        try:
            f = QFont("Apple Color Emoji", 16)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255))
            p.drawText(pix.rect(), Qt.AlignCenter, "🏆")
        finally:
            p.end()

        icon = QIcon(pix)
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("🏆 世界杯悬浮窗（已隐藏） — 单击或 ⌘⇧W 唤回")

        # Build the context menu. On macOS, the OS draws its own version
        # of this menu using the actions.
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #2a2a38;
                border: 1px solid #4a4a5a;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 24px;
                color: #d0d0e0;
                border-radius: 4px;
                font-size: 14px;
            }
            QMenu::item:selected { background-color: #3a3a5a; }
            QMenu::separator { height: 1px; background: #4a4a5a; margin: 4px 8px; }
        """)

        show_action = QAction("🏆 显示窗口 (⌘⇧W)", menu)
        show_action.triggered.connect(self._restore_from_tray)
        menu.addAction(show_action)

        refresh_action = QAction("🔄 立即刷新", menu)
        refresh_action.triggered.connect(self._refresh_data)
        menu.addAction(refresh_action)

        menu.addSeparator()

        quit_action = QAction("❌ 退出", menu)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        # On macOS the menu bar icon is hard to right-click reliably,
        # so we make SINGLE CLICK restore the window. The OS still draws
        # the context menu for users who long-press / right-click.
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        # Show a one-time system notification so the user knows where
        # the icon went. Also surfaces the location in macOS
        # Notification Center.
        try:
            tray.showMessage(
                "🏆 已最小化",
                "单击菜单栏图标或按 ⌘⇧W 唤回窗口",
                QSystemTrayIcon.Information,
                2000,
            )
        except Exception:
            pass
        return tray

    def _on_tray_activated(self, reason):
        """Handle menu bar icon clicks. Both single and double click
        restore the window — on macOS the menu bar icon doesn't
        reliably surface right-clicks, so left-click is the primary
        action."""
        from PyQt5.QtWidgets import QSystemTrayIcon
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self):
        """Show the overlay window again. This is the most reliable
        path we have — the macOS-native one used by dock-icon click."""
        from PyQt5.QtCore import QTimer
        # Drop the no-activate flag so raise_/activateWindow work
        try:
            self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        except Exception:
            pass
        # Force a Qt-level show
        try:
            self.show()
        except Exception:
            pass
        try:
            self.raise_()
            self.activateWindow()
        except Exception:
            pass
        # Use the macOS-native restore path: makeKeyAndOrderFront_ on
        # every NSWindow we own + activateIgnoringOtherApps_(True) on
        # the shared app. This is exactly what dock-icon click does
        # internally.
        self._nswindow_order_front()
        # Restore the no-activate flag 100ms later so the next
        # auto-refresh still doesn't grab focus from the user's other
        # apps.
        try:
            QTimer.singleShot(
                100,
                lambda: self.setAttribute(Qt.WA_ShowWithoutActivating, True),
            )
        except Exception:
            pass

    def _toggle_pin(self):
        self._is_pinned = not self._is_pinned
        if self._is_pinned:
            self.pin_btn.setText("📌")
            self.pin_btn.setToolTip("置顶中（点击取消）")
        else:
            self.pin_btn.setText("📍")
            self.pin_btn.setToolTip("未置顶（点击置顶）")
        # On macOS, setWindowFlag requires hide()+show() to take effect.
        # Use raise_()+activateWindow() so the overlay stays visible after
        # the user clicks another app and switches back.
        self.hide()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self._is_pinned)
        self.show()
        self.raise_()
        self.activateWindow()
        self._save_settings()

    def _show_settings(self):
        dialog = SettingsDialog(self.refresh_interval, self)
        # Keep dialog above main window
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        if dialog.exec_() == QDialog.Accepted:
            new_interval = dialog.get_interval()
            self.refresh_interval = new_interval
            self.refresh_timer.setInterval(new_interval * 1000)
            self._save_settings()
            self._update_status()

    def _show_pushplus(self):
        """Show PushPlus push notification settings dialog."""
        dialog = PushPlusDialog(
            self._pushplus_token,
            self._pushplus_on_start,
            self._pushplus_on_goal,
            self,
        )
        # Keep dialog above main window
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        if dialog.exec_() == QDialog.Accepted:
            token, on_start, on_goal = dialog.get_settings()
            self._pushplus_token = token
            self._pushplus_on_start = on_start
            self._pushplus_on_goal = on_goal
            self._save_settings()

    # ---- Mouse Events ----

    def mouseDoubleClickEvent(self, event):
        self._toggle_live_filter()

    # ---- Context Menu ----

    def contextMenuEvent(self, event):
        menu = self.create_context_menu()
        menu.exec_(event.globalPos())

    def create_context_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2a2a38;
                border: 1px solid #4a4a5a;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 24px;
                color: #d0d0e0;
                border-radius: 4px;
                font-size: 15px;
            }
            QMenu::item:selected { background-color: #3a3a5a; }
            QMenu::separator { height: 1px; background: #4a4a5a; margin: 4px 8px; }
        """)

        refresh_action = QAction("🔄 立即刷新", menu)
        refresh_action.triggered.connect(self._refresh_data)
        refresh_action.setEnabled(not self._is_refreshing)
        menu.addAction(refresh_action)

        date_action = QAction("📅 跳转到日期", menu)
        date_action.triggered.connect(self._show_date_picker)
        menu.addAction(date_action)

        pushplus_action = QAction("📲 推送设置", menu)
        pushplus_action.triggered.connect(self._show_pushplus)
        menu.addAction(pushplus_action)

        settings_action = QAction("⏱ 刷新间隔", menu)
        settings_action.triggered.connect(self._show_settings)
        menu.addAction(settings_action)

        menu.addSeparator()

        # Minimize action removed — use macOS dock "Hide" instead.

        quit_action = QAction("❌ 退出", menu)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

        return menu

    # ---- Keyboard shortcuts ----

    def keyPressEvent(self, event):
        # Don't react to arrow keys if a text input is focused (e.g. token
        # field in the PushPlus dialog — but that dialog has its own
        # keyPressEvent, so by the time we're here, focus is on the main
        # window and we can safely intercept).
        if event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_R and event.modifiers() == Qt.ControlModifier:
            if not self._is_refreshing:
                self._refresh_data()
        elif event.key() == Qt.Key_Left:
            self._shift_date(-1)
        elif event.key() == Qt.Key_Right:
            self._shift_date(1)
        elif event.key() == Qt.Key_T:
            self._go_today()

    # ---- Close event ----

    def closeEvent(self, event):
        self._save_settings()
        event.accept()

    # ---- App switch handling ----

    def changeEvent(self, event):
        # When the user clicks another app and comes back, macOS sometimes
        # leaves the overlay hidden. We DO NOT call raise_() automatically
        # here — periodic refreshes that rebuild card widgets can briefly
        # trigger ActivationChange, and force-raising at that moment would
        # steal focus from the user's chat / input window. The overlay
        # remains visible thanks to WindowStaysOnTopHint; if the user
        # needs the overlay to come to the very front, they can click
        # anywhere on it.
        try:
            from PyQt5.QtCore import QEvent
            # Intentionally a no-op for ActivationChange. Kept as a hook
            # in case we need to add behavior later.
            _ = event
        except Exception:
            pass
        super().changeEvent(event)


# ============================================================================
# Entry Point
# ============================================================================

def main():
    os.makedirs(CONFIG_DIR, exist_ok=True)

    # --- Single instance lock ---
    lock_fd = None
    try:
        import fcntl
        lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_WRONLY)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            # Another instance is running
            subprocess.Popen([
                "osascript", "-e",
                'display notification "应用已在运行中" with title "🏆 2026世界杯"'
            ])
            sys.exit(0)
    except ImportError:
        pass  # fcntl not available (non-Unix), skip lock

    app = QApplication(sys.argv)
    app.setApplicationName("WorldCup 2026 Overlay")
    app.setOrganizationName("worldcup-overlay")

    font = QFont("Helvetica Neue", 12)
    font.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(font)

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(30, 30, 40))
    palette.setColor(QPalette.WindowText, QColor(240, 240, 255))
    app.setPalette(palette)

    overlay = WorldCupOverlay()
    overlay.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
