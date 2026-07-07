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
import traceback
import requests
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
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
# Persistent on-disk cache of the bulk 22-day window. When the user
# reopens the app within the same day, we can serve today's view
# from this snapshot before the next bulk fetch returns — no
# "no matches" flash, no waiting for ESPN.
BULK_CACHE_PATH = os.path.join(CONFIG_DIR, "bulk_cache.json")
# How long a stored bulk cache is considered fresh enough to use
# as a "first paint" source. After this many seconds we still
# load it (to avoid the empty flash) but the next tick will
# re-fetch from the network. 4 hours is a reasonable window:
# long enough to cover the user closing the app for lunch and
# reopening, short enough that a stale snapshot is unlikely to
# be misleading.
BULK_CACHE_TTL_SECS = 4 * 60 * 60

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
    # Argentina (expanded)
    "Lionel Messi": "里奥·梅西",
    "Lisandro Martínez": "利桑德罗·马丁内斯",
    "Alexis Mac Allister": "亚历克西斯·麦卡利斯特",
    "Julián Álvarez": "朱利安·阿尔瓦雷斯",
    "Lautaro Martínez": "劳塔罗·马丁内斯",
    "Ángel Di María": "安赫尔·迪马利亚",
    "Emiliano Martínez": "埃米利亚诺·马丁内斯",
    "Cristian Romero": "克里斯蒂安·罗梅罗",
    "Cuti Romero": "克里斯蒂安·罗梅罗",
    "Enzo Fernández": "恩佐·费尔南德斯",
    "Nicolás Otamendi": "尼古拉斯·奥塔门迪",
    "Rodrigo De Paul": "罗德里戈·德保罗",
    "Giovani Lo Celso": "乔瓦尼·洛塞尔索",
    "Paulo Dybala": "保罗·迪巴拉",
    "Leandro Paredes": "莱昂德罗·帕雷德斯",
    "Nahuel Molina": "纳韦尔·莫利纳",
    "Marcos Acuña": "马科斯·阿库尼亚",
    "Nicolás Tagliafico": "尼古拉斯·塔利亚菲科",
    "Germán Pezzella": "赫尔曼·佩泽拉",
    "Exequiel Palacios": "埃塞基埃尔·帕拉西奥斯",
    "Thiago Almada": "蒂亚戈·阿尔马达",
    "Giovani Simeone": "乔瓦尼·西蒙尼",
    "Nicolás González": "尼古拉斯·冈萨雷斯",
    "Ángel Correa": "安赫尔·科雷亚",
    "Papu Gómez": "帕普·戈麦斯",
    "Alejandro Gómez": "帕普·戈麦斯",
    "Juan Foyth": "胡安·福伊特",
    "Lucas Ocampos": "卢卡斯·奥坎波斯",
    "Nicolás Paz": "尼古拉斯·帕斯",
    "Valentín Carboni": "瓦伦丁·卡尔博尼",
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
    # Belgium
    "Charles De Ketelaere": "查尔斯·德凯特拉雷",
    "Hans Vanaken": "汉斯·瓦纳肯",
    "Romelu Lukaku": "罗梅卢·卢卡库",
    "Kevin De Bruyne": "凯文·德布劳内",
    "Youri Tielemans": "尤里·蒂勒曼斯",
    "Jérémy Doku": "杰雷米·多库",
    "Leandro Trossard": "莱昂德罗·特罗萨德",
    "Amadou Onana": "阿马杜·奥纳纳",
    "Axel Witsel": "阿克塞尔·维特塞尔",
    "Thomas Meunier": "托马斯·默尼耶",
    "Loïs Openda": "洛伊·奥彭达",
    "Wout Faes": "沃特·法斯",
    "Jan Vertonghen": "扬·维尔通亨",
    # USA
    "Malik Tillman": "马利克·蒂尔曼",
    "Christian Pulisic": "克里斯蒂安·普利西奇",
    "Weston McKennie": "韦斯顿·麦肯尼",
    "Tyler Adams": "泰勒·亚当斯",
    "Gio Reyna": "吉奥·雷纳",
    "Tim Weah": "蒂姆·维阿",
    "Folarin Balogun": "弗拉林·巴洛贡",
    "Antonee Robinson": "安东尼·罗宾逊",
    "Sergiño Dest": "塞尔吉尼奥·德斯特",
    "Yunus Musah": "尤努斯·穆萨",
    "Ricardo Pepi": "里卡多·佩皮",
    # Egypt
    "Yasser Ibrahim": "亚瑟·易卜拉欣",
    "Mostafa Zico": "穆斯塔法·齐科",
    "Mohamed Elneny": "穆罕默德·埃尔内尼",
    "Omar Marmoush": "奥马尔·马尔穆什",
    "Ahmed Hegazi": "艾哈迈德·赫加齐",
    "Trezeguet": "特雷泽盖",
    "Mahmoud Trezeguet": "特雷泽盖",
    "Emam Ashour": "伊马姆·阿舒尔",
    "Marwan Attia": "马万·阿提亚",
    # Paraguay
    "Miguel Almirón": "米格尔·阿尔米隆",
    "Antonio Sanabria": "安东尼奥·萨纳布里亚",
    # Japan
    "Kaoru Mitoma": "三笘薫",
    "Takefusa Kubo": "久保建英",
    "Wataru Endo": "远藤航",
    "Ritsu Doan": "堂安律",
    "Takehiro Tomiyasu": "富安健洋",
    # Uruguay
    "Federico Valverde": "费德里科·巴尔韦德",
    "Darwin Núñez": "达尔文·努涅斯",
    "Ronald Araújo": "罗纳德·阿劳霍",
    # Senegal
    "Sadio Mané": "萨迪奥·马内",
    "Kalidou Koulibaly": "卡利杜·库利巴利",
    # Iran
    "Mehdi Taremi": "迈赫迪·塔雷米",
    "Sardar Azmoun": "萨尔达尔·阿兹蒙",
    # Tunisia
    "Hannibal Mejbri": "汉尼拔·梅布里",
    "Wahbi Khazri": "瓦赫比·哈兹里",
    # Iraq
    "Aymen Hussein": "艾门·侯赛因",
    # New Zealand
    "Chris Wood": "克里斯·伍德",
    # Sweden
    "Alexander Isak": "亚历山大·伊萨克",
    "Dejan Kulusevski": "德扬·库卢塞夫斯基",
    # Panama
    "Aníbal Godoy": "阿尼瓦尔·戈多伊",
    # Uzbekistan
    "Eldor Shomurodov": "埃尔多尔·肖穆罗多夫",
    # Spain (expanded)
    "Gavi": "加维",
    "Pedri": "佩德里",
    "Rodri": "罗德里",
    "Sergio Busquets": "塞尔吉奥·布斯克茨",
    "Marco Asensio": "马尔科·阿森西奥",
    "Mikel Oyarzabal": "米克尔·奥亚萨瓦尔",
    "Ferran Torres": "费兰·托雷斯",
    "Pablo Sarabia": "巴勃罗·萨拉维亚",
    "Aymeric Laporte": "艾梅里克·拉波尔特",
    "Pau Torres": "保·托雷斯",
    "Dani Carvajal": "达尼·卡瓦哈尔",
    "Jordi Alba": "霍尔迪·阿尔巴",
    "Iago Aspas": "伊亚戈·阿斯帕斯",
    "Bryan Gil": "布莱恩·吉尔",
    "Mikel Merino": "米克尔·梅里诺",
    "Marc Cucurella": "马克·库库雷利亚",
    "Robin Le Normand": "罗班·勒诺尔芒",
    "Álex Baena": "亚历克斯·巴埃纳",
    "Fermin López": "费尔明·洛佩斯",
    # Portugal (expanded)
    "João Félix": "若昂·菲利克斯",
    "Rúben Dias": "鲁本·迪亚斯",
    "Pepe": "佩佩",
    "Raphaël Guerreiro": "拉斐尔·格雷罗",
    "João Cancelo": "若昂·坎塞洛",
    "Nuno Mendes": "努诺·门德斯",
    "Vitinha": "维蒂尼亚",
    "João Palhinha": "若昂·帕利尼亚",
    "André Silva": "安德烈·席尔瓦",
    "Diogo Jota": "迪奥戈·若塔",
    "Ricardo Horta": "里卡多·奥尔塔",
    "Francisco Conceição": "弗朗西斯科·孔塞桑",
    "Pedro Neto": "佩德罗·内托",
    "Pedro Gonçalves": "佩德罗·贡萨尔维斯",
    "António Silva": "安东尼奥·席尔瓦",
    # France (expanded)
    "Antoine Griezmann": "安托万·格里兹曼",
    "Ousmane Dembélé": "奥斯曼·登贝莱",
    "Aurélien Tchouaméni": "奥雷连·丘阿梅尼",
    "Olivier Giroud": "奥利维耶·吉鲁",
    "Antoine Griezmann": "安托万·格里兹曼",
    "Eduardo Camavinga": "爱德华多·卡马文加",
    "Adrien Rabiot": "阿德里安·拉比奥",
    "Theo Hernández": "特奥·埃尔南德斯",
    "Dayot Upamecano": "达约特·于帕梅卡诺",
    "Jules Koundé": "朱尔·孔德",
    "Ibrahima Konaté": "易卜拉希马·科纳特",
    "Lucas Hernández": "卢卡斯·埃尔南德斯",
    "Benjamin Pavard": "邦雅曼·帕瓦尔",
    "Marcus Thuram": "马库斯·图拉姆",
    "Randal Kolo Muani": "兰达尔·科洛·穆阿尼",
    "Bradley Barcola": "布拉德利·巴尔科拉",
    "Warren Zaïre-Emery": "沃伦·扎伊尔-埃梅里",
    "Michael Olise": "迈克尔·奥利塞",
    # Brazil (expanded)
    "Vinícius Júnior": "维尼修斯·儒尼奥尔",
    "Rodrygo": "罗德里戈",
    "Raphinha": "拉菲尼亚",
    "Neymar": "内马尔",
    "Neymar Jr": "内马尔",
    "Casemiro": "卡塞米罗",
    "Marquinhos": "马尔基尼奥斯",
    "Éder Militão": "埃德尔·米利唐",
    "Gabriel Magalhães": "加布里埃尔·马加良斯",
    "Danilo": "达尼洛",
    "Alex Sandro": "阿莱克斯·桑德罗",
    "Alisson": "阿利松",
    "Ederson": "埃德森",
    "Richarlison": "里沙利松",
    "Antony": "安东尼",
    "Gabriel Jesus": "加布里埃尔·热苏斯",
    "Bruno Guimarães": "布鲁诺·吉马良斯",
    "Lucas Paquetá": "卢卡斯·帕凯塔",
    "Éder Barboza": "埃德尔·巴博萨",
    "Savinho": "萨维尼奥",
    "Endrick": "恩德里克",
    "João Pedro": "若昂·佩德罗",
    # England (expanded)
    "Jude Bellingham": "裘德·贝林厄姆",
    "Harry Kane": "哈里·凯恩",
    "Phil Foden": "菲尔·福登",
    "Bukayo Saka": "布卡约·萨卡",
    "Marcus Rashford": "马库斯·拉什福德",
    "Raheem Sterling": "拉希姆·斯特林",
    "Jack Grealish": "杰克·格里利什",
    "Declan Rice": "德克兰·赖斯",
    "Jadon Sancho": "杰登·桑乔",
    "John Stones": "约翰·斯通斯",
    "Harry Maguire": "哈里·马奎尔",
    "Kyle Walker": "凯尔·沃克",
    "Luke Shaw": "卢克·肖",
    "Trent Alexander-Arnold": "特伦特·亚历山大-阿诺德",
    "Reece James": "里斯·詹姆斯",
    "Ben Chilwell": "本·奇尔韦尔",
    "Jordan Pickford": "乔丹·皮克福德",
    "Mason Mount": "梅森·芒特",
    "Cole Palmer": "科尔·帕尔默",
    "Eberechi Eze": "埃贝雷奇·埃泽",
    "Ollie Watkins": "奥利·沃特金斯",
    "Ivan Toney": "伊万·托尼",
    "Jarrod Bowen": "贾罗德·鲍文",
    "Anthony Gordon": "安东尼·戈登",
    "Kobbie Mainoo": "科比·梅努",
    # Germany
    "Joshua Kimmich": "约书亚·基米希",
    "Ilkay Gündoğan": "伊尔凯·京多安",
    "Kai Havertz": "凯·哈弗茨",
    "Leroy Sané": "莱罗·萨内",
    "Jamal Musiala": "贾马尔·穆西亚拉",
    "Thomas Müller": "托马斯·穆勒",
    "Antonio Rüdiger": "安东尼奥·吕迪格",
    "Niklas Süle": "尼克拉斯·聚勒",
    "Florian Wirtz": "弗洛里安·维尔茨",
    "Jonathan Tah": "乔纳森·塔",
    "David Raum": "大卫·劳姆",
    "Benjamin Henrichs": "本杰明·亨里克斯",
    "Marc-André ter Stegen": "马克-安德烈·特尔施特根",
    "Niclas Füllkrug": "尼古拉斯·菲尔克鲁格",
    "Maximilian Beier": "马克西米利安·拜尔",
    "Deniz Undav": "德尼兹·翁达夫",
    # Netherlands
    "Virgil van Dijk": "维尔吉尔·范迪克",
    "Frenkie de Jong": "弗伦基·德容",
    "Memphis Depay": "孟菲斯·德佩",
    "Cody Gakpo": "科迪·加克波",
    "Denzel Dumfries": "邓泽尔·邓弗里斯",
    "Matthijs de Ligt": "马泰斯·德里赫特",
    "Nathan Aké": "内森·阿克",
    "Tyrell Malacia": "泰雷尔·马拉西亚",
    "Stefan de Vrij": "斯特凡·德弗里",
    "Davy Klaassen": "戴维·克拉森",
    "Wout Weghorst": "沃特·韦格霍斯特",
    "Steven Berghuis": "斯蒂芬·伯格休斯",
    "Xavi Simons": "哈维·西蒙斯",
    "Tijjani Reijnders": "蒂贾尼·莱因德斯",
    "Joey Veerman": "乔伊·费尔曼",
    "Brian Brobbey": "布莱恩·布罗比",
    "Jeremie Frimpong": "杰里米·弗里蓬",
    "Quinten Timber": "昆滕·廷伯",
    # Italy
    "Federico Chiesa": "费德里科·基耶萨",
    "Nicolò Barella": "尼科洛·巴雷拉",
    "Jorginho": "若日尼奥",
    "Leonardo Bonucci": "莱昂纳多·博努奇",
    "Giorgio Chiellini": "乔治·基耶利尼",
    "Gianluigi Donnarumma": "吉安路易吉·多纳鲁马",
    "Ciro Immobile": "奇罗·因莫比莱",
    "Lorenzo Insigne": "洛伦佐·因西涅",
    "Manuel Locatelli": "曼努埃尔·洛卡特利",
    "Sandro Tonali": "桑德罗·托纳利",
    "Alessandro Bastoni": "亚历山德罗·巴斯托尼",
    "Giacomo Raspadori": "贾科莫·拉斯帕多里",
    "Wilfried Gnonto": "威尔弗里德·尼奥托",
    "Mateo Retegui": "马特奥·雷特吉",
    "Davide Frattesi": "达维德·弗拉泰西",
    # Colombia (expanded)
    "Jhon Arias": "霍恩·阿里亚斯",
    "Luis Díaz": "路易斯·迪亚斯",
    "James Rodríguez": "哈梅斯·罗德里格斯",
    "Rafael Santos Borré": "拉斐尔·桑托斯·博雷",
    "Miguel Borja": "米格尔·博尔哈",
    "Luis Suárez": "路易斯·苏亚雷斯",
    "Davinson Sánchez": "达文森·桑切斯",
    "Yerry Mina": "耶里·米纳",
    "Daniel Muñoz": "丹尼尔·穆尼奥斯",
    "Juan Cuadrado": "胡安·夸德拉多",
    "Juan Fernando Quintero": "胡安·费尔南多·金特罗",
    "Mateus Uribe": "马特乌斯·乌里韦",
    "Wilmar Barrios": "威尔马尔·巴里奥斯",
    "Richard Ríos": "理查德·里奥斯",
    "Jefferson Lerma": "杰斐逊·勒马",
    "Jhon Córdoba": "霍恩·科尔多瓦",
    "Santiago Arias": "圣地亚哥·阿里亚斯",
    "Carlos Cuesta": "卡洛斯·奎斯塔",
    # Egypt (expanded)
    "Mohamed Salah": "穆罕默德·萨拉赫",
    "Emam Ashour": "伊马姆·阿舒尔",
    "Karim Hafez": "卡里姆·哈菲兹",
    "Mohamed Hany": "穆罕默德·哈尼",
    "Mahmoud Saber": "马哈茂德·萨比尔",
    "Ramy Rabia": "拉米·拉比亚",
    "Hossam Abdelmaguid": "霍萨姆·阿卜杜勒马吉德",
    "Marwan Attia": "马万·阿提亚",
    "Yasser Ibrahim": "亚瑟·易卜拉欣",
    "Mostafa Zico": "穆斯塔法·齐科",
    "Mohamed Elneny": "穆罕默德·埃尔内尼",
    "Omar Marmoush": "奥马尔·马尔穆什",
    "Ahmed Hegazi": "艾哈迈德·赫加齐",
    "Trezeguet": "特雷泽盖",
    "Mahmoud Trezeguet": "特雷泽盖",
    "Ahmed Hassan": "艾哈迈德·哈桑",
    "Mohamed Sherif": "穆罕默德·谢里夫",
    "Mostafa Mohamed": "穆斯塔法·穆罕默德",
    "Imam Ashour": "伊马姆·阿舒尔",
    "Ahmed Sayed": "艾哈迈德·赛义德",
    "Zizo": "齐佐",
    "Emam Ashour": "伊马姆·阿舒尔",
    # USA (expanded)
    "Malik Tillman": "马利克·蒂尔曼",
    "Christian Pulisic": "克里斯蒂安·普利西奇",
    "Weston McKennie": "韦斯顿·麦肯尼",
    "Tyler Adams": "泰勒·亚当斯",
    "Gio Reyna": "吉奥·雷纳",
    "Tim Weah": "蒂姆·维阿",
    "Folarin Balogun": "弗拉林·巴洛贡",
    "Antonee Robinson": "安东尼·罗宾逊",
    "Sergiño Dest": "塞尔吉尼奥·德斯特",
    "Yunus Musah": "尤努斯·穆萨",
    "Ricardo Pepi": "里卡多·佩皮",
    "Matt Turner": "马特·特纳",
    "Walker Zimmerman": "沃克·齐默尔曼",
    "Sergiño Dest": "塞尔吉尼奥·德斯特",
    "Gio Reyna": "吉奥·雷纳",
    "Brenden Aaronson": "布伦登·阿伦森",
    "Josh Sargent": "乔什·萨金特",
    "Haji Wright": "哈吉·赖特",
    "Jesus Ferreira": "赫苏斯·费雷拉",
    "Joe Scally": "乔·斯卡利",
    "Chris Richards": "克里斯·理查兹",
    "Cameron Carter-Vickers": "卡梅隆·卡特-维克斯",
    "Austin Trusty": "奥斯汀·特拉斯蒂",
    "Marlon Fossey": "马龙·福西",
    "Kristoffer Lund": "克里斯托弗·伦德",
    "Mark McKenzie": "马克·麦肯齐",
    "Paxten Aaronson": "帕克斯滕·阿伦森",
    "Kevin Paredes": "凯文·帕雷德斯",
    # Japan (expanded)
    "Kaoru Mitoma": "三笘薫",
    "Takefusa Kubo": "久保建英",
    "Wataru Endo": "远藤航",
    "Ritsu Doan": "堂安律",
    "Takehiro Tomiyasu": "富安健洋",
    "Daichi Kamada": "镰田大地",
    "Junya Ito": "伊东纯也",
    "Hidemasa Morita": "守田英正",
    "Ko Itakura": "板仓滉",
    "Maya Yoshida": "吉田麻也",
    "Hiroki Ito": "伊藤洋辉",
    "Takehiro Tomiyasu": "富安健洋",
    "Ayase Ueda": "上田绮世",
    "Takumi Minamino": "南野拓实",
    "Hiroki Sakai": "酒井宏树",
    "Shuichi Gonda": "权田修一",
    "Ao Tanaka": "田中碧",
    "Reo Hatate": "旗手怜央",
    "Ryo Miyaichi": "宫市亮",
    "Yuki Soma": "相马勇纪",
    "Shogo Taniguchi": "谷口彰悟",
    "Wataru Endo": "远藤航",
    "Takuma Asano": "浅野拓磨",
    "Daizen Maeda": "前田大然",
    # Uruguay (expanded)
    "Federico Valverde": "费德里科·巴尔韦德",
    "Darwin Núñez": "达尔文·努涅斯",
    "Ronald Araújo": "罗纳德·阿劳霍",
    "Sergio Rochet": "塞尔吉奥·罗切特",
    "José María Giménez": "何塞·玛丽亚·希门尼斯",
    "Sebastián Coates": "塞巴斯蒂安·科阿特斯",
    "Mathías Olivera": "马蒂亚斯·奥利韦拉",
    "Matías Viña": "马蒂亚斯·维尼亚",
    "Manuel Ugarte": "曼努埃尔·乌加特",
    "Nicolás de la Cruz": "尼古拉斯·德拉克鲁斯",
    "Rodrigo Bentancur": "罗德里戈·本坦库尔",
    "Giorgian de Arrascaeta": "乔治安·德阿拉斯卡埃塔",
    "Facundo Pellistri": "法昆多·佩利斯特里",
    "Maxi Araújo": "马克西·阿劳霍",
    "Brian Rodríguez": "布莱恩·罗德里格斯",
    "Lucas de los Santos": "卢卡斯·德洛斯桑托斯",
    # Morocco (expanded)
    "Achraf Hakimi": "阿什拉夫·哈基米",
    "Hakim Ziyech": "哈基姆·齐耶赫",
    "Youssef En-Nesyri": "优素福·恩内斯里",
    "Romain Saïss": "罗曼·赛斯",
    "Nayef Aguerd": "纳耶夫·阿格尔德",
    "Achraf Dari": "阿什拉夫·达里",
    "Jawad El Yamiq": "贾瓦德·亚米克",
    "Sofyan Amrabat": "索菲扬·阿姆拉巴特",
    "Azzedine Ounahi": "阿泽丁·奥纳希",
    "Selim Amallah": "塞利姆·阿马拉",
    "Sofiane Boufal": "索菲安·布法尔",
    "Zakaria Aboukhlal": "扎卡里亚·阿布赫拉勒",
    "Walid Cheddira": "瓦利德·切迪拉",
    "Brahim Díaz": "卜拉欣·迪亚斯",
    "Abdelhamid Sabiri": "阿卜杜勒哈米德·萨比里",
    "Yahya Jabrane": "叶海亚·贾布拉内",
    "Munir Mohamedi": "穆尼尔·穆罕默迪",
    # Mexico (expanded)
    "Hirving Lozano": "欧文·洛萨诺",
    "Santiago Giménez": "圣地亚哥·希门尼斯",
    "Guillermo Ochoa": "吉列尔莫·奥乔亚",
    "Jorge Sánchez": "豪尔赫·桑切斯",
    "César Montes": "塞萨尔·蒙特斯",
    "Johan Vásquez": "约翰·巴斯克斯",
    "Edson Álvarez": "埃德森·阿尔瓦雷斯",
    "Luis Chávez": "路易斯·查韦斯",
    "Carlos Rodríguez": "卡洛斯·罗德里格斯",
    "Orbelín Pineda": "奥韦林·皮内达",
    "Alexis Vega": "亚历克西斯·维加",
    "Raúl Jiménez": "劳尔·希门尼斯",
    "Henry Martín": "亨利·马丁",
    "Roberto Alvarado": "罗伯托·阿尔瓦拉多",
    "Uriel Antuna": "乌列尔·安图尼亚",
    "Érick Sánchez": "埃里克·桑切斯",
    "Jesús Gallardo": "赫苏斯·加利亚多",
    # Croatia (expanded)
    "Luka Modrić": "卢卡·莫德里奇",
    "Ivan Perišić": "伊万·佩里西奇",
    "Joško Gvardiol": "约斯科·格瓦迪奥尔",
    "Marcelo Brozović": "马塞洛·布罗佐维奇",
    "Mateo Kovačić": "马特奥·科瓦契奇",
    "Andrej Kramarić": "安德烈·克拉马里奇",
    "Mario Pašalić": "马里奥·帕沙利奇",
    "Borna Sosa": "博尔纳·索萨",
    "Domagoj Vida": "多马戈伊·维达",
    "Dejan Lovren": "德扬·洛夫伦",
    "Josip Brekalo": "约西普·布雷卡洛",
    "Bruno Petković": "布鲁诺·佩特科维奇",
    "Ante Budimir": "安特·布迪米尔",
    "Marko Livaja": "马尔科·利瓦亚",
    "Ivan Rakitić": "伊万·拉基蒂奇",
    "Lovro Majer": "洛夫罗·马耶尔",
    "Martin Erlić": "马丁·埃尔利奇",
    "Josip Stanišić": "约西普·斯塔尼希奇",
    # Switzerland (expanded)
    "Breel Embolo": "布雷尔·恩博洛",
    "Xherdan Shaqiri": "哲尔丹·沙奇里",
    "Granit Xhaka": "格拉尼特·扎卡",
    "Manuel Akanji": "曼努埃尔·阿坎吉",
    "Ricardo Rodríguez": "里卡多·罗德里格斯",
    "Fabian Schär": "法比安·舍尔",
    "Nico Elvedi": "尼科·埃尔韦迪",
    "Remo Freuler": "雷莫·弗罗伊勒",
    "Denis Zakaria": "丹尼斯·扎卡里亚",
    "Steven Zuber": "史蒂文·祖贝尔",
    "Renato Steffen": "雷纳托·斯特芬",
    "Haris Seferović": "哈里斯·塞费罗维奇",
    "Noah Okafor": "诺亚·奥卡福",
    "Ruben Vargas": "鲁本·巴尔加斯",
    "Silvan Widmer": "西尔万·威德默",
    "Jordan Lotomba": "乔丹·洛通巴",
    "Gregor Kobel": "格雷戈尔·科贝尔",
    "Zeki Amdouni": "泽基·阿姆杜尼",
    "Dan Ndoye": "丹·恩多耶",
    # Austria (expanded)
    "Marko Arnautović": "马尔科·阿瑙托维奇",
    "David Alaba": "大卫·阿拉巴",
    "Marcel Sabitzer": "马塞尔·萨比策",
    "Konrad Laimer": "康拉德·莱默",
    "Nicolas Seiwald": "尼古拉斯·赛瓦尔德",
    "Florian Grillitsch": "弗洛里安·格里利奇",
    "Stefan Posch": "斯特凡·波施",
    "Philipp Lienhart": "菲利普·林哈特",
    "Kevin Danso": "凯文·丹索",
    "Maximilian Wöber": "马克西米利安·韦伯",
    "Christoph Baumgartner": "克里斯托夫·鲍姆加特纳",
    "Patrick Wimmer": "帕特里克·维默",
    "Michael Gregoritsch": "迈克尔·格雷戈里奇",
    "Romano Schmid": "罗马诺·施密德",
    "Andreas Weimann": "安德烈亚斯·魏曼",
    "Sasa Kalajdzic": "萨沙·卡拉季奇",
    "Karim Onisiwo": "卡里姆·奥尼西沃",
    # Senegal (expanded)
    "Sadio Mané": "萨迪奥·马内",
    "Kalidou Koulibaly": "卡利杜·库利巴利",
    "Édouard Mendy": "爱德华·门迪",
    "Idrissa Gueye": "伊德里萨·盖耶",
    "Cheikhou Kouyaté": "谢胡·库亚特",
    "Boulaye Dia": "布莱·迪亚",
    "Ismaila Sarr": "伊斯梅拉·萨尔",
    "Habib Diallo": "哈比卜·迪亚洛",
    "Krépin Diatta": "克雷潘·迪亚塔",
    "Pape Matar Sarr": "帕普·马塔尔·萨尔",
    "Pape Gueye": "帕普·盖耶",
    "Nampalys Mendy": "南帕利斯·门迪",
    "Youssouf Sabaly": "优素福·萨巴利",
    "Fodé Ballo-Touré": "福代·巴洛-图雷",
    "Abdou Diallo": "阿卜杜·迪亚洛",
    "Moussa Niakhaté": "穆萨·尼亚卡特",
    "Bamba Dieng": "班巴·迪昂",
    "Iliman Ndiaye": "伊利曼·恩迪亚耶",
    "Nicolas Jackson": "尼古拉斯·杰克逊",
    "Pape Sakho": "帕普·萨科",
    # Australia (expanded)
    "Harry Souttar": "哈里·索塔尔",
    "Jackson Irvine": "杰克逊·欧文",
    "Awer Mabil": "阿维尔·马比尔",
    "Lucas Herrington": "卢卡斯·赫灵顿",
    "Craig Goodwin": "克雷格·古德温",
    "Mathew Leckie": "马修·莱基",
    "Aaron Mooy": "亚伦·穆伊",
    "Mathew Ryan": "马修·瑞安",
    "Aziz Behich": "阿齐兹·贝希奇",
    "Milos Degenek": "米洛斯·德格内克",
    "Kye Rowles": "凯·罗尔斯",
    "Thomas Deng": "托马斯·邓",
    "Riley McGree": "莱利·麦格里",
    "Ajdin Hrustic": "阿伊丁·赫鲁斯蒂奇",
    "Mitchell Duke": "米切尔·杜克",
    "Jamie Maclaren": "杰米·麦克拉伦",
    "Daniel Arzani": "丹尼尔·阿尔扎尼",
    "Garang Kuol": "加朗·库奥尔",
    "Marco Tilio": "马尔科·蒂利奥",
    "Nathaniel Atkinson": "纳撒尼尔·阿特金森",
    "Jordan Bos": "乔丹·博斯",
    "Alessandro Circati": "亚历山德罗·奇尔卡蒂",
    # Ghana (expanded)
    "Marvin Senaya": "马尔文·塞纳亚",
    "Alidu Seidu": "阿里杜·塞杜",
    "Mohammed Kudus": "穆罕默德·库杜斯",
    "André Ayew": "安德烈·阿尤",
    "Jordan Ayew": "乔丹·阿尤",
    "Thomas Partey": "托马斯·帕尔特伊",
    "Mohammed Salisu": "穆罕默德·萨利苏",
    "Alexander Djiku": "亚历山大·吉库",
    "Daniel Amartey": "丹尼尔·阿马泰",
    "Gideon Mensah": "吉迪恩·门萨",
    "Tariq Lamptey": "塔里克·兰普泰",
    "Baba Rahman": "巴巴·拉赫曼",
    "Elisha Owusu": "埃利沙·奥乌苏",
    "Mohammed Kudus": "穆罕默德·库杜斯",
    "Kamaldeen Sulemana": "卡马尔德恩·苏莱马纳",
    "Antoine Semenyo": "安托万·塞梅尼奥",
    "Inaki Williams": "伊纳基·威廉姆斯",
    "Ernest Nuamah": "欧内斯特·努阿马",
    "Jordan Ayew": "乔丹·阿尤",
    "Lawrence Ati-Zigi": "劳伦斯·阿蒂-齐吉",
    "Joseph Wollacott": "约瑟夫·沃拉科特",
    "Abdul Fatawu Issahaku": "阿卜杜勒·法塔武·伊萨哈库",
    # Belgium (expanded)
    "Charles De Ketelaere": "查尔斯·德凯特拉雷",
    "Hans Vanaken": "汉斯·瓦纳肯",
    "Romelu Lukaku": "罗梅卢·卢卡库",
    "Kevin De Bruyne": "凯文·德布劳内",
    "Youri Tielemans": "尤里·蒂勒曼斯",
    "Jérémy Doku": "杰雷米·多库",
    "Leandro Trossard": "莱昂德罗·特罗萨德",
    "Amadou Onana": "阿马杜·奥纳纳",
    "Axel Witsel": "阿克塞尔·维特塞尔",
    "Thomas Meunier": "托马斯·默尼耶",
    "Loïs Openda": "洛伊·奥彭达",
    "Wout Faes": "沃特·法斯",
    "Jan Vertonghen": "扬·维尔通亨",
    "Thibaut Courtois": "蒂博·库尔图瓦",
    "Koen Casteels": "科恩·卡斯特尔斯",
    "Zeno Debast": "泽诺·德巴斯特",
    "Arthur Vermeeren": "亚瑟·维尔梅伦",
    "Orel Mangala": "奥雷尔·曼加拉",
    "Aster Vranckx": "阿斯特·弗兰克斯",
    "Dodi Lukebakio": "多迪·卢克巴基奥",
    "Yannick Carrasco": "扬尼克·卡拉斯科",
    "Wout Weghorst": "沃特·韦格霍斯特",
    "Timothy Castagne": "蒂莫西·卡斯塔涅",
    "Thomas Kaminski": "托马斯·卡明斯基",
    "Matz Sels": "马茨·塞尔斯",
    "Maxim De Cuyper": "马克西姆·德凯珀",
    # Iran (expanded)
    "Mehdi Taremi": "迈赫迪·塔雷米",
    "Sardar Azmoun": "萨尔达尔·阿兹蒙",
    "Alireza Jahanbakhsh": "阿里雷扎·贾汉巴赫什",
    "Saeid Ezatolahi": "赛义德·埃扎托拉希",
    "Alireza Beiranvand": "阿里雷扎·贝兰万德",
    "Ehsan Hajsafi": "伊桑·哈吉萨菲",
    "Ramin Rezaeian": "拉明·雷扎伊安",
    "Majid Hosseini": "马吉德·侯赛尼",
    "Shojae Khalilzadeh": "舒贾埃·哈利勒扎德",
    "Sadegh Moharrami": "萨德格·莫哈拉米",
    "Milad Mohammadi": "米拉德·穆罕默迪",
    "Ahmad Nourollahi": "艾哈迈德·努罗拉希",
    "Karim Ansarifard": "卡里姆·安萨里法德",
    "Saman Ghoddos": "萨曼·古多斯",
    "Amir Abedzadeh": "阿米尔·阿贝扎德",
    "Payam Niazmand": "帕亚姆·尼亚兹曼德",
    "Omid Noorafkan": "奥米德·努拉夫坎",
    "Allahyar Sayyadmanesh": "阿拉亚尔·萨亚德马内什",
    "Mohammad Mohebi": "穆罕默德·莫赫比",
    "Reza Shekari": "雷扎·谢卡里",
    # Tunisia (expanded)
    "Hannibal Mejbri": "汉尼拔·梅布里",
    "Wahbi Khazri": "瓦赫比·哈兹里",
    "Aïssa Laïdouni": "艾萨·莱杜尼",
    "Ellyes Skhiri": "埃利耶斯·斯基里",
    "Hannibal Mejbri": "汉尼拔·梅布里",
    "Aymen Dahmen": "艾门·达门",
    "Dylan Bronn": "迪伦·布龙",
    "Montassar Talbi": "蒙塔萨尔·塔尔比",
    "Yassine Meriah": "亚辛·梅里亚",
    "Ali Abdi": "阿里·阿卜迪",
    "Wajdi Kechrida": "瓦吉迪·凯赫里达",
    "Hamza Rafia": "哈姆扎·拉菲亚",
    "Ghailene Chaalali": "盖莱内·沙拉利",
    "Anis Ben Slimane": "阿尼斯·本·斯利曼",
    "Naïm Sliti": "纳伊姆·斯利蒂",
    "Youssef Msakni": "优素福·姆萨克尼",
    "Seifeddine Jaziri": "赛义夫丁·贾齐里",
    "Issam Jebali": "伊萨姆·杰巴利",
    "Aymen Harzi": "艾门·哈尔齐",
    # South Korea
    "Son Heung-min": "孙兴慜",
    "Kim Min-jae": "金玟哉",
    "Hwang Hee-chan": "黄喜灿",
    "Lee Kang-in": "李刚仁",
    "Hwang In-beom": "黄仁范",
    "Kim Jin-su": "金珍洙",
    "Kim Young-gwon": "金英权",
    "Cho Gue-sung": "曹圭成",
    "Lee Jae-sung": "李在城",
    "Kang Sung-yoon": "姜成允",
    "Hwang Ui-jo": "黄义助",
    "Na Sang-ho": "罗相镐",
    "Paik Seung-ho": "白胜浩",
    "Hong Hyun-seok": "洪贤锡",
    "Jeong Woo-yeong": "郑优营",
    "Oh Hyun-gyu": "吴贤揆",
    "Lee Ki-je": "李基济",
    "Kim Tae-hwan": "金太焕",
    "Kim Seung-gyu": "金承奎",
    "Song Bum-keun": "宋范根",
    "Cho Young-wook": "曹永旭",
    "Yang Hyun-jun": "梁铉俊",
    "Bae Jun-ho": "裴俊浩",
    # Denmark
    "Christian Eriksen": "克里斯蒂安·埃里克森",
    "Pierre-Emile Højbjerg": "皮埃尔-埃米尔·霍伊别尔",
    "Kasper Schmeichel": "卡斯帕·舒梅切尔",
    "Andreas Christensen": "安德烈亚斯·克里斯滕森",
    "Joakim Mæhle": "约阿基姆·梅勒",
    "Joachim Andersen": "约阿希姆·安德森",
    "Mikkel Damsgaard": "米克尔·达姆斯高",
    "Andreas Skov Olsen": "安德烈亚斯·斯科夫·奥尔森",
    "Jesper Lindstrøm": "杰斯珀·林德斯特伦",
    "Kasper Dolberg": "卡斯帕·多尔贝格",
    "Jonas Wind": "约纳斯·温德",
    "Yussuf Poulsen": "尤素福·波尔森",
    "Mathias Jensen": "马蒂亚斯·延森",
    "Thomas Delaney": "托马斯·德莱尼",
    "Christian Nørgaard": "克里斯蒂安·诺尔高",
    "Victor Nelsson": "维克托·尼尔松",
    "Rasmus Kristensen": "拉斯穆斯·克里斯滕森",
    "Mads Mikkelsen": "马兹·米克尔森",
    "Mikkel Andersen": "米克尔·安德森",
    "Morten Hjulmand": "莫滕·尤尔曼德",
    # Poland
    "Robert Lewandowski": "罗伯特·莱万多夫斯基",
    "Piotr Zieliński": "皮奥特·齐林斯基",
    "Wojciech Szczęsny": "沃伊切赫·什琴斯尼",
    "Jan Bednarek": "扬·贝德纳雷克",
    "Kamil Glik": "卡米尔·格利克",
    "Jakub Kiwior": "雅库布·基维奥尔",
    "Nicola Zalewski": "尼古拉·扎莱夫斯基",
    "Matty Cash": "马蒂·卡什",
    "Grzegorz Krychowiak": "格热戈日·克雷霍维亚克",
    "Sebastian Szymański": "塞巴斯蒂安·希曼斯基",
    "Krzysztof Piątek": "克里斯托弗·皮亚特克",
    "Karol Świderski": "卡罗尔·希维德尔斯基",
    "Arkadiusz Milik": "阿尔卡迪乌什·米利克",
    "Przemysław Frankowski": "普热梅斯瓦夫·弗兰科夫斯基",
    "Szymon Żurkowski": "希蒙·茹尔科夫斯基",
    "Damian Szymański": "达米安·希曼斯基",
    "Krzysztof Krawczyk": "克里斯托弗·克拉夫奇克",
    # Ukraine
    "Andriy Yarmolenko": "安德烈·亚尔莫连科",
    "Roman Yaremchuk": "罗曼·亚列姆丘克",
    "Oleksandr Zinchenko": "亚历山大·津琴科",
    "Mykola Shaparenko": "尼古拉·沙帕连科",
    "Ruslan Malinovskyi": "鲁斯兰·马利诺夫斯基",
    "Andriy Lunin": "安德烈·卢宁",
    "Illia Zabarnyi": "伊利亚·扎巴尔尼",
    "Vitaliy Mykolenko": "维塔利·米科连科",
    "Oleksandr Karavaev": "亚历山大·卡拉瓦耶夫",
    "Mykola Matviyenko": "尼古拉·马特维延科",
    "Oleksandr Tymchyk": "亚历山大·季姆奇克",
    "Taras Stepanenko": "塔拉斯·斯捷潘年科",
    "Yukhym Konoplya": "尤希姆·科诺普利亚",
    "Viktor Tsyhankov": "维克托·齐汉科夫",
    "Mykhailo Mudryk": "米哈伊洛·穆德里克",
    "Dovbyk": "多夫比克",
    "Artem Dovbyk": "阿尔乔姆·多夫比克",
    "Heorhiy Sudakov": "格奥尔基·苏达科夫",
    "Oleksandr Zubkov": "亚历山大·祖布科夫",
    "Vladyslav Vanat": "弗拉迪斯拉夫·瓦纳特",
    # Serbia
    "Aleksandar Mitrović": "亚历山大·米特罗维奇",
    "Dušan Vlahović": "杜尚·弗拉霍维奇",
    "Aleksandar Mitrović": "亚历山大·米特罗维奇",
    "Dušan Tadić": "杜尚·塔迪奇",
    "Sergej Milinković-Savić": "谢尔盖·米林科维奇-萨维奇",
    "Nikola Milenković": "尼古拉·米伦科维奇",
    "Strahinja Pavlović": "斯特拉希尼亚·帕夫洛维奇",
    "Filip Kostić": "菲利普·科斯蒂奇",
    "Andrija Živković": "安德里亚·日夫科维奇",
    "Nemanja Gudelj": "内马尼亚·古德利",
    "Sasa Lukic": "萨沙·卢基奇",
    "Filip Mladenović": "菲利普·姆拉德诺维奇",
    "Milan Pavkov": "米兰·帕夫科夫",
    "Luka Jović": "卢卡·约维奇",
    "Filip Djuričić": "菲利普·朱里契奇",
    "Nemanja Radonjić": "内马尼亚·拉东吉奇",
    "Darko Lazović": "达尔科·拉佐维奇",
    "Strahinja Eraković": "斯特拉希尼亚·埃拉科维奇",
    "Predrag Rajković": "普雷德拉格·拉伊科维奇",
    # Costa Rica
    "Keylor Navas": "凯洛尔·纳瓦斯",
    "Joel Campbell": "乔尔·坎贝尔",
    "Celso Borges": "塞尔索·博尔赫斯",
    "Bryan Ruiz": "布莱恩·鲁伊斯",
    "Francisco Calvo": "弗朗西斯科·卡尔沃",
    "Yeltsin Tejeda": "耶尔钦·特赫达",
    "Óscar Duarte": "奥斯卡·杜阿尔特",
    "Kendall Waston": "肯德尔·沃斯顿",
    "Jewison Bennette": "朱维森·贝内特",
    "Anthony Contreras": "安东尼·孔特雷拉斯",
    "Gerson Torres": "赫森·托雷斯",
    "Juan Pablo Vargas": "胡安·巴勃罗·巴尔加斯",
    "Carlos Mora": "卡洛斯·莫拉",
    "Keysher Fuller": "凯谢尔·富勒",
    "Ronald Matarrita": "罗纳德·马塔里塔",
    "Leonel Moreira": "莱昂内尔·莫雷拉",
    "Esteban Alvarado": "埃斯特万·阿尔瓦拉多",
    "Patrick Sequeira": "帕特里克·塞凯拉",
    # Ecuador
    "Enner Valencia": "恩纳·瓦伦西亚",
    "Moisés Caicedo": "莫伊塞斯·凯塞多",
    "Pervis Estupiñán": "佩尔维斯·埃斯图皮尼安",
    "Piero Hincapié": "皮耶罗·辛卡皮耶",
    "Ángelo Preciado": "安杰洛·普雷西亚多",
    "Félix Torres": "费利克斯·托雷斯",
    "Jackson Porozo": "杰克逊·波罗佐",
    "Diego Palacios": "迭戈·帕拉西奥斯",
    "Gonzalo Plata": "贡萨洛·普拉塔",
    "Romario Ibarra": "罗马里奥·伊瓦拉",
    "Michael Estrada": "迈克尔·埃斯特拉达",
    "Ayrton Preciado": "艾尔顿·普雷西亚多",
    "José Cifuentes": "何塞·西富恩特斯",
    "Sebastián Méndez": "塞巴斯蒂安·门德斯",
    "Alan Franco": "阿兰·弗兰科",
    "Jeremy Sarmiento": "杰里米·萨尔米恩托",
    "Kendry Páez": "肯德里·帕埃斯",
    "Kevin Rodríguez": "凯文·罗德里格斯",
    "Leonardo Campana": "莱昂纳多·坎帕纳",
    "Hernán Galíndez": "埃尔南·加林德斯",
    # Cameroon
    "Vincent Aboubakar": "文森特·阿布巴卡尔",
    "Karl Toko Ekambi": "卡尔·托科·埃坎比",
    "André Onana": "安德烈·奥纳纳",
    "Eric Maxim Choupo-Moting": "埃里克·马克西姆·舒波-莫廷",
    "André-Frank Zambo Anguissa": "安德烈-弗兰克·赞博·安古伊萨",
    "Bryan Mbeumo": "布莱恩·姆贝乌莫",
    "Jean-Charles Castelletto": "让-查尔斯·卡斯特莱托",
    "Nouhou Tolo": "努胡·托洛",
    "Oumar Gonzalez": "乌马尔·冈萨雷斯",
    "Enzo Ebosse": "恩佐·埃博塞",
    "Christopher Wooh": "克里斯托弗·沃",
    "Pierre Kunde": "皮埃尔·孔德",
    "Martin Hongla": "马丁·洪格拉",
    "Olivier Kemen": "奥利维尔·凯门",
    "Samuel Gouet": "萨米尔·古埃",
    "Georges-Kévin Nkoudou": "乔治-凯文·恩库杜",
    "Karl Namnganda": "卡尔·南甘达",
    "Didier Lamkel Zé": "迪迪埃·拉姆克尔·泽",
    "Souaibou Marou": "苏瓦伊布·马鲁",
    "Davis Epassy": "戴维斯·埃帕西",
    # Saudi Arabia
    "Salem Al-Dawsari": "萨勒姆·达瓦萨里",
    "Saud Abdulhamid": "萨乌德·阿卜杜勒哈米德",
    "Salman Al-Faraj": "萨尔曼·法拉杰",
    "Yasser Al-Shahrani": "亚塞尔·沙赫拉尼",
    "Ali Al-Bulaihi": "阿里·布莱希",
    "Mohamed Kanno": "穆罕默德·坎诺",
    "Abdullah Otayf": "阿卜杜拉·奥泰夫",
    "Nawaf Al-Aqidi": "纳瓦夫·阿基迪",
    "Mohammed Al-Owais": "穆罕默德·奥韦斯",
    "Sultan Al-Ghannam": "苏丹·加南",
    "Hassan Al-Tambakti": "哈桑·坦巴克蒂",
    "Abdullah Madu": "阿卜杜拉·马杜",
    "Saud Abdulhamid": "萨乌德·阿卜杜勒哈米德",
    "Nasser Al-Dawsari": "纳赛尔·达瓦萨里",
    "Abdulrahman Al-Jassim": "阿卜杜勒拉赫曼·贾西姆",
    "Firas Al-Buraikan": "菲拉斯·布里坎",
    "Saleh Al-Shehri": "萨利赫·谢赫里",
    "Haitham Asiri": "海瑟姆·阿西里",
    "Abdullah Al-Hamdan": "阿卜杜拉·哈姆丹",
    "Turki Al-Ammar": "图尔基·阿马尔",
    # Chile
    "Alexis Sánchez": "亚历克西斯·桑切斯",
    "Arturo Vidal": "阿图罗·比达尔",
    "Gary Medel": "加里·梅德尔",
    "Eduardo Vargas": "爱德华多·巴尔加斯",
    "Claudio Bravo": "克劳迪奥·布拉沃",
    "Charles Aránguiz": "查尔斯·阿朗吉斯",
    "Erick Pulgar": "埃里克·普尔加",
    "Francisco Sierralta": "弗朗西斯科·西拉尔塔",
    "Guillermo Maripán": "吉列尔莫·马里潘",
    "Mauricio Isla": "毛里西奥·伊斯拉",
    "Gabriel Suazo": "加布里埃尔·苏阿索",
    "Ben Brereton Díaz": "本·布雷雷顿·迪亚斯",
    "Víctor Dávila": "维克托·达维拉",
    "Diego Valdés": "迭戈·巴尔德斯",
    "Marcelino Núñez": "马塞利诺·努涅斯",
    "Rodrigo Echeverría": "罗德里戈·埃切韦里亚",
    "Felipe Mora": "费利佩·莫拉",
    "Vicente Pizarro": "维森特·皮萨罗",
    "Thiago Bravo": "蒂亚戈·布拉沃",
    "Cortés": "科尔特斯",
    # Nigeria
    "Victor Osimhen": "维克托·奥斯梅恩",
    "Ademola Lookman": "阿德莫拉·卢克曼",
    "Wilfred Ndidi": "威尔弗雷德·恩迪迪",
    "Alex Iwobi": "亚历克斯·伊沃比",
    "Samuel Chukwueze": "萨穆埃尔·丘库埃泽",
    "Taiwo Awoniyi": "塔伊沃·阿沃尼伊",
    "Kelechi Iheanacho": "凯莱奇·伊希纳乔",
    "Frank Onyeka": "弗兰克·奥涅卡",
    "Moses Simon": "摩西·西蒙",
    "Joe Aribo": "乔·阿里博",
    "Terem Moffi": "特雷姆·莫菲",
    "Bright Osayi-Samuel": "布莱特·奥赛-萨缪尔",
    "Calvin Bassey": "卡尔文·巴锡",
    "William Troost-Ekong": "威廉·特罗斯特-埃孔",
    "Semi Ajayi": "塞米·阿贾伊",
    "Zaidu Sanusi": "扎伊杜·萨努西",
    "Ola Aina": "奥拉·艾纳",
    "Kenneth Omeruo": "肯尼斯·奥梅罗",
    "Francis Uzoho": "弗朗西斯·乌佐霍",
    "Raphael Onyedika": "拉斐尔·奥涅迪卡",
    "Joshua Maja": "约书亚·马贾",
    "Cyril Dessers": "西里尔·德塞斯",
    # Ivory Coast
    "Sébastien Haller": "塞巴斯蒂安·阿莱",
    "Nicolas Pépé": "尼古拉斯·佩佩",
    "Franck Kessié": "弗兰克·凯西",
    "Serge Aurier": "塞尔日·奥里耶",
    "Wilfried Zaha": "威尔弗里德·扎哈",
    "Ibrahim Sangaré": "易卜拉欣·桑加雷",
    "Simon Adingra": "西蒙·阿丁格拉",
    "Hamed Traorè": "哈梅德·特拉奥雷",
    "Seko Fofana": "塞科·福法纳",
    "Ibrahim Diakité": "易卜拉欣·迪亚基特",
    "Odilon Kossounou": "奥迪隆·科苏努",
    "Willy Boly": "威利·博利",
    "Evan Ndicka": "埃文·恩迪卡",
    "Ghislain Konan": "吉斯兰·科南",
    "Fofana": "福法纳",
    "Eloi Petit": "埃洛伊·佩蒂特",
    "Christian Kouamé": "克里斯蒂安·夸梅",
    "Maxwel Cornet": "马克斯韦尔·科内特",
    "Yahia Fofana": "叶海亚·福法纳",
    "Moussa Diarra": "穆萨·迪亚拉",
    # Turkey
    "Hakan Çalhanoğlu": "哈坎·恰尔汗奥卢",
    "Cengiz Ünder": "琴吉兹·云代尔",
    "Merih Demiral": "梅里赫·德米拉尔",
    "Çağlar Söyüncü": "恰拉尔·瑟因居",
    "Arda Güler": "阿尔达·居莱尔",
    "Kenan Yıldız": "凯南·伊尔迪兹",
    "Burak Yılmaz": "布拉克·伊尔马兹",
    "Yusuf Yazıcı": "优素福·亚泽哲",
    "Zeki Çelik": "泽基·切利克",
    "Mert Günok": "梅尔特·居诺克",
    "Uğurcan Çakır": "乌古尔坎·恰克尔",
    "Mert Müldür": "梅尔特·米尔迪尔",
    "Abdülkerim Bardakcı": "阿卜杜勒克里姆·巴尔达克奇",
    "Samet Akaydın": "萨梅特·阿卡伊丁",
    "Ferdi Kadıoğlu": "费尔迪·卡德奥卢",
    "İsmail Yüksek": "伊斯梅尔·于克塞克",
    "Orkun Kökçü": "奥尔昆·克克曲",
    "Salih Özcan": "萨利赫·厄兹詹",
    "Kerem Aktürkoğlu": "凯雷姆·阿克蒂尔科奥卢",
    "Barış Alper Yılmaz": "巴里斯·阿尔佩尔·伊尔马兹",
    "Yunus Akgün": "尤努斯·阿克金",
    "Enes Ünal": "埃内斯·于纳尔",
    # Wales
    "Gareth Bale": "加雷斯·贝尔",
    "Aaron Ramsey": "阿隆·拉姆塞",
    "Daniel James": "丹尼尔·詹姆斯",
    "Joe Allen": "乔·阿伦",
    "Harry Wilson": "哈里·威尔逊",
    "Ben Davies": "本·戴维斯",
    "Joe Rodon": "乔·罗登",
    "Neco Williams": "内科·威廉姆斯",
    "Connor Roberts": "康纳·罗伯茨",
    "Wayne Hennessey": "韦恩·亨尼西",
    "Kieffer Moore": "基弗·穆尔",
    "Brennan Johnson": "布伦南·约翰逊",
    "Ethan Ampadu": "伊森·阿姆帕杜",
    "Sorba Thomas": "索尔巴·托马斯",
    "Tyler Roberts": "泰勒·罗伯茨",
    "Mark Harris": "马克·哈里斯",
    "Matthew Smith": "马修·史密斯",
    "Jonny Williams": "乔尼·威廉姆斯",
    "Chris Gunter": "克里斯·冈特",
    "Dylan Levitt": "迪伦·莱维特",
    # Scotland
    "Andrew Robertson": "安德鲁·罗伯逊",
    "Scott McTominay": "斯科特·麦克托米奈",
    "John McGinn": "约翰·麦金",
    "Kieran Tierney": "基兰·蒂尔尼",
    "Che Adams": "切·亚当斯",
    "Lyndon Dykes": "林登·戴克斯",
    "Ryan Fraser": "瑞恩·弗雷泽",
    "Stuart Armstrong": "斯图尔特·阿姆斯特朗",
    "Callum McGregor": "卡勒姆·麦格雷戈",
    "Jack Hendry": "杰克·亨德利",
    "Grant Hanley": "格兰特·汉利",
    "Liam Cooper": "利亚姆·库珀",
    "Aaron Hickey": "阿伦·希基",
    "Nathan Patterson": "内森·帕特森",
    "Stephen O'Donnell": "斯蒂芬·奥唐奈",
    "Billy Gilmour": "比利·吉尔莫",
    "Lewis Ferguson": "刘易斯·弗格森",
    "Ryan Christie": "瑞恩·克里斯蒂",
    "David Turnbull": "大卫·特恩布尔",
    "Craig Gordon": "克雷格·戈登",
    "Angus Gunn": "安格斯·冈恩",
    "Zander Clark": "赞德·克拉克",
    # Peru
    "Paolo Guerrero": "保罗·格雷罗",
    "Andre Carrillo": "安德雷·卡里略",
    "Christian Cueva": "克里斯蒂安·奎瓦",
    "Gianluca Lapadula": "詹卢卡·拉帕杜拉",
    "Yoshimar Yotún": "约西马尔·约图恩",
    "Renato Tapia": "雷纳托·塔皮亚",
    "Pedro Gallese": "佩德罗·加莱塞",
    "Luis Advíncula": "路易斯·阿德温库拉",
    "Miguel Araujo": "米格尔·阿劳霍",
    "Alexander Callens": "亚历山大·卡伦斯",
    "Carlos Zambrano": "卡洛斯·桑布拉诺",
    "Marcos López": "马科斯·洛佩斯",
    "Aldo Corzo": "阿尔多·科尔索",
    "Sergio Peña": "塞尔吉奥·佩尼亚",
    "Wilder Cartagena": "威尔德·卡塔赫纳",
    "Christofer Gonzales": "克里斯托弗·冈萨雷斯",
    "Matías Lazo": "马蒂亚斯·拉索",
    "Alex Valera": "亚历克斯·瓦莱拉",
    "Santiago Ormeño": "圣地亚哥·奥尔梅尼奥",
    "Raúl Ruidíaz": "劳尔·鲁伊迪亚斯",
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
    home_red: int = 0
    home_yellow: int = 0
    away_red: int = 0
    away_yellow: int = 0


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
        # Summary cache: match_id -> (summary_dict, timestamp)
        self._summary_cache: dict = {}
        self._summary_cache_ttl: float = 30.0  # 30s TTL

    # ---- Quota management (lightweight, ESPN allows many reqs) ----

    def _can_make_request(self) -> bool:
        now = time.time()
        if now < self._rate_limited_until:
            return False
        self._request_times = [t for t in self._request_times if now - t < 60]
        # Conservative cap: ESPN rate-limits aggressively; we keep 4
        # req/min per IP as the soft cap, well under ESPN's threshold.
        # One refresh today pulls (today + 1 day window) = 1 call
        # (single range request), plus 1 call every 60s for live
        # updates. For past/future dates there's 1 call per refresh.
        # The previous cap of 2/min was too tight — once the bulk
        # startup fetch consumed 1, the user couldn't even refresh
        # for 60s. 4/min gives us the same protection with headroom.
        return len(self._request_times) < 4

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
                # See _fetch_for_date_range for why this flag matters.
                self._cache_fetched = True
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
                # Mark the cache as valid — same role as _fetch_today
                # plays. Without this, fetch_matches_by_beijing_date
                # (which is the path used by today's live refresh) would
                # report api_success=False even when the request
                # returned a perfectly good 200 response, which made
                # the overlay display "加载失败" while matches were
                # actually loaded fine.
                self._cache_fetched = True
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
        
        # 比赛阶段中文映射
        period_cn_map = {
            1: "上半场",
            2: "下半场",
            3: "加时上半场",
            4: "加时下半场",
            5: "点球大战",
        }
        period_cn = period_cn_map.get(period_num, "未知时段")
        
        if tp_id == "137" or "header" in tp_type:
            method = "头球"
        elif tp_id == "138" or "penalty" in tp_type:
            method = "点球"
        elif tp_id == "97" or "own" in tp_type:
            method = "乌龙球"
        elif tp_id == "140" or "free-kick" in tp_type or "free kick" in tp_type:
            method = "任意球"
        elif "volley" in tp_type or "volley" in (tp.get("text") or "").lower():
            method = "凌空抽射"
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
        # the "TeamName N, TeamName M" pattern in ESPN goal text)
        cumulative = ""
        goal_text = last.get("text") or ""
        # e.g. "Goal! Mexico 0, England 1. Jude Bellingham ..."
        import re as _re
        m_score = _re.search(r"(\w[\w\s]*?\w)\s+(\d+),\s*(\w[\w\s]*?\w)\s+(\d+)", goal_text)
        if m_score:
            cumulative = f"{m_score.group(2)}-{m_score.group(4)}"
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
            "period_cn": period_cn,  # 新增：比赛阶段
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
            
            # 比赛阶段中文映射
            period_cn_map = {
                1: "上半场",
                2: "下半场",
                3: "加时上半场",
                4: "加时下半场",
                5: "点球大战",
            }
            period_cn = period_cn_map.get(period_num, "未知时段")
            if tp_id == "137" or "header" in tp_type:
                method = "头球"
            elif tp_id == "138" or "penalty" in tp_type:
                method = "点球"
            elif tp_id == "97" or "own" in tp_type:
                method = "乌龙球"
            elif tp_id == "140" or "free-kick" in tp_type or "free kick" in tp_type:
                method = "任意球"
            elif "volley" in tp_type or "volley" in (tp.get("text") or "").lower():
                method = "凌空抽射"
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
            m_score = _re.search(r"(\w[\w\s]*?\w)\s+(\d+),\s*(\w[\w\s]*?\w)\s+(\d+)", goal_text)
            if m_score:
                cumulative = f"{m_score.group(2)}-{m_score.group(4)}"
            else:
                cumulative = f"{m.home_score}-{m.away_score}"
            out.append({
                "scorer": scorer,
                "scorer_en": scorer_en,
                "team_cn": team_cn or team_name,
                "method": method,
                "minute": minute,
                "score": cumulative,
                "period_cn": period_cn,  # 新增：比赛阶段
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

        # Cards (Red/Yellow) — parse from details if available
        home_red = 0
        home_yellow = 0
        away_red = 0
        away_yellow = 0

        details = comp.get("details", [])
        if details:
            home_id = str(home.get("id", ""))
            away_id = str(away.get("id", ""))
            for d in details:
                tp_text = d.get("type", {}).get("text", "")
                if "Card" not in tp_text:
                    continue
                team_id = str((d.get("team") or {}).get("id", ""))
                is_red = "Red" in tp_text
                is_yellow = "Yellow" in tp_text
                if team_id == home_id:
                    if is_red:
                        home_red += 1
                    elif is_yellow:
                        home_yellow += 1
                elif team_id == away_id:
                    if is_red:
                        away_red += 1
                    elif is_yellow:
                        away_yellow += 1

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
            home_red=home_red,
            home_yellow=home_yellow,
            away_red=away_red,
            away_yellow=away_yellow,
        )

    def _fetch_for_bj_date(self, date_str_mmddyyyy: str) -> list:
        """Fetch events whose Beijing local date matches the given date.

        ESPN stores dates in EDT/UTC; a Beijing date can span up to
        TWO adjacent EDT days (BJ is UTC+8, EDT is UTC-4, so the
        offset is 12 hours — a match at 20:00 EDT lands at 08:00 BJ
        next day). We pull a 5-day EDT range (BJ ± 2 days) to be
        safe, then filter IN-MEMORY by the Beijing local date.

        This is the ONLY correct way: never trust ESPN's date grouping.
        Always convert each match's UTC startDate to Beijing time, then
        bucket by that Beijing date.
        """
        try:
            m, d, y = date_str_mmddyyyy.split("/")
            bj_target = f"{int(m):02d}/{int(d):02d}/{int(y):04d}"
        except Exception:
            return []

        # Compute the Beijing date's UTC equivalents.
        # BJ = UTC+8, so BJ 00:00 = UTC 16:00 previous day.
        # BJ 23:59 = UTC 15:59 same day.
        # A BJ day can span UTC days [bj_utc_start, bj_utc_end].
        # To be safe, fetch ESPN dates from (BJ date - 2 days)
        # to (BJ date + 2 days) in ESPN's date system.
        # ESPN dates are in EDT (UTC-4), so we need to cover
        # the full range. Be conservative: fetch 5 days.
        bj_dt = datetime(int(y), int(m), int(d))
        # Fetch ESPN dates from (BJ - 2) to (BJ + 2) to cover
        # all possible EDT/UTC dates that could contain a match
        # whose Beijing date is bj_target.
        utc_start = (bj_dt - timedelta(days=2)).strftime("%Y%m%d")
        utc_end   = (bj_dt + timedelta(days=2)).strftime("%Y%m%d")

        events = self._fetch_for_date_range(utc_start, utc_end)
        if not events:
            return []

        # Filter to events whose Beijing local date == bj_target.
        # We convert startDate (UTC ISO) to Beijing time, then compare.
        out = []
        for ev in events:
            comp = (ev.get("competitions") or [{}])[0]
            date_src = comp.get("startDate") or ev.get("date") or ""
            if not date_src:
                continue
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
        try:
            if not pixmap.isNull():
                self._cache[url] = pixmap
                self.flag_ready.emit(url)
        except Exception:
            pass

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
            # Fetch a wider EDT window: BJ date ± 2 days.
            # BJ = UTC+8, EDT = UTC-4 → 12h offset.
            # A BJ day's matches can land on (EDT = BJ - 12h),
            # so we need EDT dates from (BJ - 2) to (BJ + future + 2).
            utc_start = (today - timedelta(days=2)).strftime("%Y%m%d")
            utc_end   = (today + timedelta(days=self.future_days + 2)).strftime("%Y%m%d")
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
        url = "https://www.pushplus.plus/api/send"
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
        # Don't fix height — let content (penalty/ET rows) dictate size
        self.setMinimumHeight(120)

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
            font-size: 34px; color: #ffffff; font-weight: 900;
            background-color: rgba(50,50,65,0.8);
            border-radius: 8px; padding: 0px 12px;
            margin-top: -8px;
        """)
        score_layout.addWidget(self.home_score_label)

        colon = QLabel(":")
        colon.setAlignment(Qt.AlignCenter)
        colon.setStyleSheet("font-size: 28px; color: #8080a0; font-weight: bold; margin-top: -6px;")
        colon.setFixedWidth(16)
        score_layout.addWidget(colon)

        self.away_score_label = QLabel(f" {self.match.away_score} ")
        self.away_score_label.setAlignment(Qt.AlignCenter)
        self.away_score_label.setStyleSheet("""
            font-size: 34px; color: #ffffff; font-weight: 900;
            background-color: rgba(50,50,65,0.8);
            border-radius: 8px; padding: 0px 12px;
            margin-top: -8px;
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

        # --- Cards (Red/Yellow) Row ---
        # Fixed widths on all 3 sections so every card aligns vertically
        # across all matches. Always shown, even when zero.
        self.cards_row = QHBoxLayout()
        self.cards_row.setSpacing(0)

        # Home team cards — fixed width, right-aligned text inside
        self.home_cards_label = QLabel()
        self.home_cards_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.home_cards_label.setFixedWidth(110)
        self.home_cards_label.setStyleSheet("font-size: 20px; color: #c8c8d8;")
        self.cards_row.addWidget(self.home_cards_label)

        # VS — strictly centred, narrow fixed width
        cards_vs = QLabel("vs")
        cards_vs.setAlignment(Qt.AlignCenter)
        cards_vs.setFixedWidth(36)
        cards_vs.setStyleSheet(
            "font-size: 16px; color: #707088; font-weight: bold;"
        )
        self.cards_row.addWidget(cards_vs)

        # Away team cards — fixed width, left-aligned text inside
        self.away_cards_label = QLabel()
        self.away_cards_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.away_cards_label.setFixedWidth(110)
        self.away_cards_label.setStyleSheet("font-size: 20px; color: #c8c8d8;")
        self.cards_row.addWidget(self.away_cards_label)

        main_layout.addLayout(self.cards_row)
        self._update_cards_display()

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
        try:
            self._on_flag_ready_impl(url)
        except Exception:
            pass  # flag display errors are non-critical, silently ignore

    def _on_flag_ready_impl(self, url: str):
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

    def _update_cards_display(self):
        """Update red/yellow card labels.
        Always visible (shows 0 when none). Plain text only (no HTML)
        to avoid Qt rich-text crashes on macOS.
        Format: "1🔴 2🟡" — number first, then dot.
        Fixed-width parent labels ensure vertical alignment across all cards.
        """
        def _card_text(red, yellow):
            return f"{red}🔴 {yellow}🟡"

        home_txt = _card_text(self.match.home_red, self.match.home_yellow)
        away_txt = _card_text(self.match.away_red, self.match.away_yellow)

        if hasattr(self, 'home_cards_label') and self.home_cards_label:
            self.home_cards_label.setText(home_txt)
            self.home_cards_label.setVisible(True)
        if hasattr(self, 'away_cards_label') and self.away_cards_label:
            self.away_cards_label.setText(away_txt)
            self.away_cards_label.setVisible(True)

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

        # Cards (Red/Yellow)
        self._update_cards_display()

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
        # Extra time indicator — hidden to save space (加时 row already shown below)
        if match.status == "et":
            self._hide_et_badge()

        # Extra time score row (post-match "加时 (X : Y)" footer).
        # Mirrors the penalty row's show/hide pattern.
        # Hide during penalty phase to save space.
        if getattr(match, "has_extra_time", False) and match.status not in ("pen",):
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
        elif hasattr(self, 'extra_time_row') and self.extra_time_row:
            # Hide extra time row during penalty phase or if not needed
            for i in range(self.extra_time_row.count()):
                w = self.extra_time_row.itemAt(i).widget()
                if w:
                    w.hide()
            self.extra_time_row.setEnabled(False)

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
        self._is_hidden = False   # Track macOS hide (⌘H) to skip UI updates
        self._live_only = False
        self.current_date = get_beijing_today()
        self.matches = []
        self.card_widgets = []
        self._fetch_worker = None
        self._pending_refresh = False
        self._is_refreshing = False  # True while a background fetch is in progress
        self._last_refresh_time = 0  # debounce rapid manual refresh clicks
        self._last_force_refresh_time = 0  # cooldown for force refresh (Ctrl+R or right-click)
        self._force_refresh_cooldown = 10  # seconds, minimum interval for force refresh
        self._cooldown_timer = None  # timer for cooldown countdown
        self._cooldown_remaining = 0  # remaining seconds in cooldown
        self._has_ever_loaded = False
        self._retry_count = 0
        self._max_retries = 3
        self._retry_countdown_timer = None
        self._retry_countdown_remaining = 0
        self._last_known_today = get_beijing_today()
        self._prev_matches_state = {}  # match_id -> (status, home_score, away_score)
        # Matches that have already had their "比赛开始" notification sent.
        # Session-only: _end_pushed prevents duplicate "match end" notifications
        # within the same app session.
        self._end_pushed = set()  # set of match_id strings (match end notifications)
        self._ht_pushed = set()  # 半场结束已推送
        self._reg_end_pushed = set()  # 常规时间结束（进入加时前）已推送
        self._et_end_pushed = set()  # 加时赛结束（进入点球前）已推送
        self._last_pushed_score = {}  # match_id -> [home_score, away_score]
        self._user_navigated = False  # True if user manually changed date
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
        #
        # First, try to load a previously-persisted bulk cache
        # from disk. If one exists and is fresh (<4h), populate
        # self._bulk_cache BEFORE the network call, so the first
        # 300ms _refresh_data() can serve today's view from cache
        # instantly. Without this, every app launch shows a
        # "no matches" flash for ~5-10s while waiting for ESPN.
        cache_loaded = self._load_bulk_cache()
        if cache_loaded:
            self._bulk_loaded = True
            # Render today's view from cache immediately (sub-second
            # first paint), but FORCE a live network fetch right after
            # so we never show stale "即将开始" data after restart.
            # Use _force_refresh() for initial load (skips cooldown check).
            self._force_refresh()
            # Remove today's cache entry so the next _refresh_data()
            # tick does a real network fetch instead of trusting stale disk cache.
            today = get_beijing_today()
            if today in self._bulk_cache:
                del self._bulk_cache[today]
        self._start_bulk_fetch()
        # Always do a live fetch 300ms after startup (not using cache)
        # Use _force_refresh() for initial load (skips cooldown check).
        QTimer.singleShot(300, self._force_refresh)

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
                    # Load end_pushed set (match end notifications)
                    end_pushed = data.get("end_pushed_ids", [])
                    self._end_pushed = set(end_pushed)
                    # Load half-time / phase transition pushed sets
                    self._ht_pushed = set(data.get("ht_pushed_ids", []))
                    self._reg_end_pushed = set(data.get("reg_end_pushed_ids", []))
                    self._et_end_pushed = set(data.get("et_end_pushed_ids", []))
                    # Load last_pushed_score: match_id -> [home, away]
                    # 如果数据太旧（超过 2 小时），清空（避免推送过期数据）
                    lps = data.get("last_pushed_score", {})
                    import time
                    saved_at = data.get("saved_at", 0)
                    if time.time() - saved_at > 7200:  # 2 小时
                        self._last_pushed_score = {}
                    else:
                        self._last_pushed_score = {k: (v + [0, 0])[:4] if isinstance(v, list) else v for k, v in lps.items()}
                    # Debug log
                    import datetime
                    with open("/tmp/worldcup_notify.log", "a") as f:
                        f.write(f"[{datetime.datetime.now()}] _load_settings: loaded last_pushed_score={self._last_pushed_score}\n")
        except Exception:
            pass

    def _load_bulk_cache(self):
        """Load the bulk 22-day window from disk so a fresh app
        start can show the user's last-seen data instantly, before
        the network fetch returns. Returns True if a usable cache
        was loaded.

        Each entry is stored as a list of plain dicts (Match
        fields). We rehydrate them into Match objects on load.
        We only honor the on-disk cache if it's younger than
        BULK_CACHE_TTL_SECS — older snapshots get discarded and
        we wait for the network.
        """
        try:
            if not os.path.exists(BULK_CACHE_PATH):
                return False
            with open(BULK_CACHE_PATH, "r") as f:
                payload = json.load(f)
            saved_at = float(payload.get("saved_at", 0))
            age = time.time() - saved_at
            if age > BULK_CACHE_TTL_SECS:
                return False
            by_date = payload.get("by_date", {})
            if not by_date:
                return False
            # Rehydrate Match objects. We use the existing
            # Match dataclass directly — every field it stores
            # is JSON-serializable (str / int / bool).
            today = get_beijing_today()
            for bj_date, match_dicts in by_date.items():
                # Skip today's cache — always fetch fresh for today.
                if bj_date == today:
                    continue
                matches = []
                for d in match_dicts:
                    try:
                        matches.append(Match(**d))
                    except Exception:
                        # Field shape changed between versions —
                        # skip the broken entry but keep the rest.
                        continue
                if matches:
                    self._bulk_cache[bj_date] = (matches, saved_at, True)
            return bool(self._bulk_cache)
        except Exception:
            return False

    def _save_bulk_cache(self):
        """Persist the current bulk cache to disk. Called after
        every successful bulk fetch so the next app start has
        fresh data to show immediately."""
        try:
            by_date = {}
            for bj_date, entry in self._bulk_cache.items():
                matches = entry[0] if isinstance(entry, tuple) else entry
                by_date[bj_date] = [asdict(m) for m in matches]
            payload = {
                "saved_at": time.time(),
                "by_date": by_date,
            }
            # Atomic write: write to .tmp, then os.replace. Prevents
            # the file from being half-written if the process is
            # killed mid-save.
            tmp_path = BULK_CACHE_PATH + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp_path, BULK_CACHE_PATH)
        except Exception:
            pass

    def _save_settings(self):
        """Save all settings to config file."""
        os.makedirs(CONFIG_DIR, exist_ok=True)
        try:
            import time
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
                    "end_pushed_ids": list(self._end_pushed),
                    "ht_pushed_ids": list(self._ht_pushed),
                    "reg_end_pushed_ids": list(self._reg_end_pushed),
                    "et_end_pushed_ids": list(self._et_end_pushed),
                    "last_pushed_score": self._last_pushed_score,
                    "saved_at": time.time(),  # 用于判断数据是否过期
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

        # macOS-only: this attribute prevents the window from being
        # auto-promoted to the front when it would otherwise be obscured.
        # Without it, every refresh tick (which calls update()/repaint()
        # on the viewport) can briefly pop the window to the front,
        # contradicting the user's "hide from dock" intent. NSWindow's
        # `level` is not affected — pinning still works.
        if sys.platform == "darwin":
            self.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)

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

        self.date_label = QPushButton(self._date_display())
        self.date_label.setStyleSheet("""
            QPushButton {
                font-size: 13px; color: #e0e0f0; font-weight: bold;
                padding: 0 6px; background: transparent; border: none;
                border-radius: 4px;
            }
            QPushButton:hover { color: #64b5f6; background-color: rgba(100,181,246,0.1); }
            QPushButton:pressed { color: #90caf9; background-color: rgba(100,181,246,0.2); }
        """)
        self.date_label.setCursor(Qt.PointingHandCursor)
        self.date_label.clicked.connect(self._show_date_picker)
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

    def hideEvent(self, event):
        """Track when macOS hides the window (⌘H / dock-hide).
        We set _is_hidden so timer-driven UI updates are skipped,
        preventing the window from stealing focus / popping to front.
        """
        self._is_hidden = True
        super().hideEvent(event)

    def showEvent(self, event):
        """Track when the window becomes visible again (dock-click,
        tray-click, ⌘⇧W). Rebuild cards with latest data so the
        user always sees fresh scores.
        """
        self._is_hidden = False
        super().showEvent(event)
        # Rebuild cards with data already in memory (the timer kept
        # self.matches fresh even while the window was hidden).
        if self.matches:
            self._rebuild_match_cards()
            self._update_status()

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
        try:
            self._on_timer_tick_impl()
        except Exception:
            print(f"[WorldCupOverlay] Error in _on_timer_tick (prevented crash):\n{traceback.format_exc()}", file=sys.stderr)

    def _on_timer_tick_impl(self):
        today = get_beijing_today()
        cmp = compare_beijing_dates(self.current_date, today)

        # Detect day rollover: switch to new today
        if self._last_known_today != today:
            self._last_known_today = today
            # Invalidate the bulk-cache entry for the NEW today so the
            # first refresh after rollover is a real network fetch,
            # not a stale placeholder from yesterday.
            self._bulk_cache.pop(today, None)
            # Clear previous-match state so cross-day match starts are
            # detected. Without this, _prev_matches_state holds yesterday's
            # IDs; new today matches have prev=None → notification check
            # skips them → no "比赛开始" push.
            self._prev_matches_state = {}
            self._match_goal_state = {}
            # Clear push state for old matches to avoid stale
            # "比赛结束" pushes on app restart
            self._end_pushed.clear()
            self._ht_pushed.clear()
            self._reg_end_pushed.clear()
            self._et_end_pushed.clear()
            self._last_pushed_score.clear()
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
            # Within 30 min of kickoff: gradually shorten interval
            if next_min <= 1:
                # Within 1 min of kickoff: 15 s for near-instant start detection
                if self.refresh_timer.interval() != 15_000:
                    self.refresh_timer.setInterval(15_000)
            elif next_min <= 5:
                # Within 5 min: 30 s
                if self.refresh_timer.interval() != 30_000:
                    self.refresh_timer.setInterval(30_000)
            else:
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
        try:
            if self._is_hidden:
                return
            for card in self.card_widgets:
                if hasattr(card, 'update_countdown'):
                    card.update_countdown()
        except Exception:
            print(f"[WorldCupOverlay] Error in _update_countdowns (prevented crash):\n{traceback.format_exc()}", file=sys.stderr)

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
            self._user_navigated = True
            self._refresh_data()  # Use cache if available, don't force reload
        except Exception:
            pass

    def _go_today(self):
        self.current_date = get_beijing_today()
        self.date_label.setText(self._date_display())
        self._user_navigated = True
        self._refresh_data()  # Don't force reload; use cache if all finished

    def _show_date_picker(self):
        """Show date picker dialog."""
        dialog = DatePickDialog(self.current_date, self)
        # Keep dialog above main window
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        if dialog.exec_() == QDialog.Accepted:
            self.current_date = dialog.get_date_str()
            self.date_label.setText(self._date_display())
            self._user_navigated = True
            self._refresh_data()  # Don't force reload; use cache

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

    def _refresh_data(self, force_reload=False, skip_cooldown=False):
        """Fetch latest match data in background thread (with re-entry guard & debounce).

        Debounce policy: only ignore rapid clicks that come within 2s of the
        last *successful* refresh trigger. If a click is debounced, we
        schedule a delayed retry so fast date switching still works.

        Caching policy:
        - "Today" (BJ today) with LIVE/IN-PROGRESS matches → ALWAYS
          do a live network fetch (user wants live scores).
        - "Today" with ALL FINISHED matches → use cache, don't reload
          (matches are over, nothing changes).
        - Past dates → ALWAYS use cache (matches are finished; no point
          in re-fetching).
        - Future dates → use cache if available (22-day window pre-loaded
          on startup); calculate countdowns locally.
        - On day rollover, invalidate today's cache so the first tick of
          the new day does a real network fetch.
        
        Force reload policy:
        - force_reload=True → ignore cache and fetch from network
        - Cooldown: _force_refresh_cooldown seconds (default 10s) between
          force reloads to avoid API ban.
        - During cooldown, shows a countdown timer in status bar that
          updates every second until cooldown ends.
        - skip_cooldown=True → bypass cooldown check (used for initial load).
        """
        # Check cooldown for force reload
        if force_reload and not skip_cooldown:
            now = time.time()
            if now - self._last_force_refresh_time < self._force_refresh_cooldown:
                remaining = int(self._force_refresh_cooldown - (now - self._last_force_refresh_time)) + 1
                # Start a countdown timer to show remaining seconds
                self._cooldown_remaining = remaining
                self.status_bar.setText(f"⏳ 刷新过于频繁，请等待 {remaining} 秒")
                # Update countdown every second
                if hasattr(self, '_cooldown_timer') and self._cooldown_timer:
                    self._cooldown_timer.stop()
                self._cooldown_timer = QTimer(self)
                self._cooldown_timer.timeout.connect(self._update_cooldown)
                self._cooldown_timer.start(1000)  # Update every second
                return
            self._last_force_refresh_time = now
        
        today = get_beijing_today()
        is_today = (self.current_date == today)

        # Caching logic:
        # - Today with ALL FINISHED matches → use cache, don't reload
        # - Today with LIVE/UPCOMING matches → do live fetch
        # - Past dates → ALWAYS use cache (never reload)
        # - Future dates → use cache if available (calculate locally)
        
        if is_today:
            # Check if all today's matches are finished
            if self.current_date in self._bulk_cache:
                cached = self._bulk_cache[self.current_date]
                matches = cached[0] if isinstance(cached, tuple) else cached
                if matches and all(m.status == "finished" for m in matches):
                    # All finished → use cache, don't reload
                    self._on_data_ready(matches, self.current_date, True)
                    return
        else:
            # Past or future date: ALWAYS use cache if available
            if self.current_date in self._bulk_cache:
                cached = self._bulk_cache[self.current_date]
                matches = cached[0] if isinstance(cached, tuple) else cached
                success = True
                self._on_data_ready(matches, self.current_date, success)
                return

        # If the bulk fetch is still running and we don't have this
        # date in cache yet, wait for it. Otherwise we'd race a
        # network fetch against the in-flight bulk and waste quota —
        # or, worse, get rate-limited because the bulk request
        # already used the per-minute budget. This applies to BOTH
        # future dates and today; the only difference is what we
        # display in the status bar.
        if self._bulk_fetcher and self._bulk_fetcher.isRunning() and \
                self.current_date not in self._bulk_cache:
            if is_today:
                self.status_bar.setText("⏳ 预加载赛程数据中...")
            else:
                self.status_bar.setText("正在预加载赛程数据...")
            return

        # Future date that's NOT in cache: the bulk fetch has finished
        # and the date has no matches. Render an empty list.
        if not is_today and self._bulk_loaded:
            self._on_data_ready([], self.current_date, True)
            return

        # --- Live fetch path (today with at least one live match) ---

        # Invalidate the stale "today" entry so the new data replaces it.
        if is_today and self.current_date in self._bulk_cache:
            del self._bulk_cache[self.current_date]

        # If the API rate limit is in effect, do NOT call the network
        # at all — just keep the current screen state and tell the
        # user we're waiting. The previous behaviour silently
        # produced an empty matches list (rate-limited fetch returns
        # []), which got fed to _on_data_ready as (matches=[],
        # success=False), which then ran "empty success → clear
        # matches → 该日期没有比赛" — overwriting perfectly good
        # data with an empty-state card. That was the bug where the
        # overlay would say "该日期没有比赛" 60s after showing real
        # matches on first launch.
        if not self.api._can_make_request():
            wait_secs = max(1, int(self.api._rate_limited_until - time.time()) + 1)
            if self.matches:
                # Keep the cards on screen. Just note the wait.
                self.status_bar.setText(
                    f"⏳ API 限流中，{wait_secs}秒后自动恢复 · 当前数据 {datetime.now(BEIJING_TZ).strftime('%H:%M:%S')}"
                )
            else:
                self.status_bar.setText(
                    f"⏳ API 限流中，{wait_secs}秒后首次拉取 · {datetime.now(BEIJING_TZ).strftime('%H:%M:%S')}"
                )
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
        """Kick off the bulk pre-load in a background thread.

        On first startup (empty bulk cache), load the entire World Cup
        date range (June 11 – July 20 Beijing time) so the user can
        browse any date instantly without waiting for per-date API calls.

        On subsequent startups the cache is already warm; we only
        refresh the nearby window (today ± a few days) to pick up
        latest scores.
        """
        if self._bulk_fetcher and self._bulk_fetcher.isRunning():
            return

        today = get_beijing_today()

        # First startup → load full World Cup range
        if not self._bulk_cache:
            today_dt = datetime.strptime(today, "%m/%d/%Y")
            wc_start = datetime(2026, 6, 11)
            wc_end   = datetime(2026, 7, 20)
            past   = max(0, (today_dt - wc_start).days)
            future = max(0, (wc_end   - today_dt).days)
        else:
            past   = 1
            future = self._bulk_window_days

        self._bulk_fetcher = BulkFetchWorker(
            self.api, today,
            past_days=past, future_days=future,
        )
        self._bulk_fetcher.bulk_ready.connect(self._on_bulk_ready)
        self._bulk_fetcher.finished.connect(self._on_bulk_finished)
        self._bulk_fetcher.start()

    def _find_live_date(self):
        """Scan all dates in the bulk cache and return the date
        (string 'YYYY-MM-DD') that has at least one live match.
        Returns None if no live match is found.
        """
        live_statuses = {"1h", "2h", "ht", "et", "pen", "live"}
        for bj_date, (matches, _, _) in self._bulk_cache.items():
            if any(m.status in live_statuses for m in matches):
                return bj_date
        return None

    def _auto_switch_to_live_date(self):
        """If the current date has no live match, but another date
        in the cache does, switch to that date automatically.

        Only switches if:
        - bulk cache is loaded
        - current date has no live match
        - another date has a live match
        - user hasn't manually navigated (self._user_navigated is False)
        """
        if not self._bulk_loaded:
            return
        live_date = self._find_live_date()
        if live_date and live_date != self.current_date:
            # Check if current date has any live match
            current_matches = self._bulk_cache.get(self.current_date, (None, None, False))[0]
            current_has_live = current_matches and any(
                m.status in {"1h", "2h", "ht", "et", "pen", "live"} for m in current_matches
            )
            if not current_has_live:
                self._user_navigated = False  # allow auto-switch
                self._navigate_to_date(live_date)

    def _on_bulk_ready(self, by_date: dict):
        """Bulk cache populated. If the user is still viewing today
        (or any date the cache now covers), refresh the UI from
        cache immediately. The earlier _refresh_data() call that
        ran while bulk was in-flight is now obsolete — it would
        have either timed out or hit the rate limit; we want the
        cache to be the source of truth for the first paint.

        Note: even if the user is on "today" and there's a live
        match, we still serve from the cache first. The next
        30s tick will detect the live match and re-fetch from
        the network. This is intentional — it gives the user
        instant feedback (no spinner) and saves the per-minute
        API budget for the first refresh cycle.

        Also persists the fresh cache to disk so the next app
        launch can serve today's view without waiting for ESPN.
        """
        try:
            self._on_bulk_ready_impl(by_date)
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[WorldCupOverlay] Error in _on_bulk_ready (prevented crash):\n{tb}", file=sys.stderr)
            # Don't leave app stuck — mark bulk as loaded so _refresh_data stops waiting
            self._bulk_loaded = True
            # Try to refresh from whatever partial data we have
            if self.current_date in self._bulk_cache or not self._has_ever_loaded:
                self._refresh_data()

    def _on_bulk_ready_impl(self, by_date: dict):
        for bj_date, matches in by_date.items():
            # Enrich ET scores for AET matches (same as the
            # per-date path does)
            self._enrich_matches_with_et_scores(matches)
            self._bulk_cache[bj_date] = (matches, time.time(), True)
        self._bulk_loaded = True
        # Persist the freshly-loaded cache for next launch.
        # This is what makes reopening the app instant — see
        # _load_bulk_cache in __init__.
        self._save_bulk_cache()
        # If the user is currently viewing a date that the cache
        # now covers, refresh the UI from cache.
        if self.current_date in by_date or (
            not self._has_ever_loaded and self.current_date in self._bulk_cache
        ):
            self._refresh_data()

        # Auto-switch to the date with live matches (e.g. if the
        # live match is on yesterday but today is selected).
        if not getattr(self, '_user_navigated', False):
            self._auto_switch_to_live_date()

    def _on_bulk_finished(self):
        # Bulk thread done; nothing else to do — the cache is the
        # only output.
        pass

    def _on_data_ready(self, matches: list, date_str: str, success: bool):
        try:
            self._on_data_ready_impl(matches, date_str, success)
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[WorldCupOverlay] Error in _on_data_ready (prevented crash):\n{tb}", file=sys.stderr)
            # Don't leave UI stuck — treat as fetch failure so retry logic kicks in
            self._is_refreshing = False
            self._fetch_worker = None
            self._on_data_ready_impl([], date_str, False)

    def _on_data_ready_impl(self, matches: list, date_str: str, success: bool):
        if date_str != self.current_date:
            return

        if success:
            self._has_ever_loaded = True
            self._retry_count = 0

        if matches:
            # Check for notifications (always call, _check_notifications
            # uses _last_pushed_score to avoid re-sending).
            if date_str == get_beijing_today():
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
            # Skip UI updates when the window is hidden (⌘H / dock-hide).
            # The data is still fresh in self.matches; cards will be
            # rebuilt in showEvent when the user brings the window back.
            if not self._is_hidden:
                self._rebuild_match_cards()
                self._update_status()
                # Force a viewport repaint so cards flush.
                self.scroll_area.setVisible(True)
                self.scroll_area.viewport().update()
            # Also persist today's snapshot so a quick app restart
            # can render this exact view before the next network
            # call returns. Cheap, and prevents the "no matches"
            # flash on relaunch.
            self._save_bulk_cache()
            
            # Update "今天" button state:
            # - If viewing today and ALL matches are finished → disable button
            # - Otherwise → enable button
            today = get_beijing_today()
            if date_str == today:
                all_finished = matches and all(m.status == "finished" for m in matches)
                self.today_btn.setEnabled(not all_finished)
                if all_finished:
                    self.today_btn.setToolTip("今天的比赛已全部结束")
                else:
                    self.today_btn.setToolTip("回到今天")
            else:
                # Always enable "今天" button when viewing other dates
                self.today_btn.setEnabled(True)
                self.today_btn.setToolTip("回到今天")
        elif success:
            # API succeeded but no matches for this date — clear old data
            self.matches = []
            if not self._is_hidden:
                self._rebuild_match_cards()
                self._update_status()
        elif not success and self._retry_count < self._max_retries:
            self._retry_count += 1
            # If we already have matches on screen, the failure is
            # purely transient (rate limit / 5xx). Don't show a
            # scary "加载失败" — instead, just kick off a quiet
            # retry. Only show the warning when there's nothing on
            # screen yet.
            if self.matches:
                self.status_bar.setText(
                    f"⏳ 等待API恢复，第{self._retry_count}次重试..."
                )
            else:
                self.status_bar.setText(
                    f"加载失败，5秒后第{self._retry_count}次重试..."
                )
            self._start_retry_countdown()
        elif not success:
            if self.matches:
                # Don't overwrite a working UI with a permanent
                # error message — the cached data is still on screen.
                self._update_status()
            else:
                self.status_bar.setText("加载失败，请检查网络后重试")

    def _start_retry_countdown(self):
        """Start a 30-second countdown timer. Update status bar every second."""
        self._retry_countdown_remaining = 30
        if self._retry_countdown_timer:
            self._retry_countdown_timer.stop()
        self._retry_countdown_timer = QTimer(self)
        self._retry_countdown_timer.timeout.connect(self._update_retry_countdown)
        self._retry_countdown_timer.start(1000)
        self._update_retry_countdown()  # immediate first update

    def _update_retry_countdown(self):
        """Update status bar with remaining seconds. Retry when reaches 0."""
        try:
            self._retry_countdown_remaining -= 1
            if self._retry_countdown_remaining <= 0:
                if self._retry_countdown_timer:
                    self._retry_countdown_timer.stop()
                    self._retry_countdown_timer = None
                self._retry_fetch()
                return
            if self.matches:
                self.status_bar.setText(
                    f"⏳ 等待API恢复，第{self._retry_count}次重试 ({self._retry_countdown_remaining}s)..."
                )
            else:
                self.status_bar.setText(
                    f"加载失败，{self._retry_countdown_remaining}秒后第{self._retry_count}次重试..."
                )
        except Exception:
            print(f"[WorldCupOverlay] Error in _update_retry_countdown (prevented crash):\n{traceback.format_exc()}", file=sys.stderr)

    def _retry_fetch(self):
        # Force-bypass the rate limit window. The user just clicked
        # refresh or hit a tick; they want a real network fetch, not
        # the cached answer. ESPN allows many reqs, our 2/min cap is
        # a courtesy, not a hard limit.
        self.api.force_allow()
        self.api._rate_limited_until = 0
        self._fetch_worker = None  # clear to allow new fetch
        self._retry_count = 0
        self._refresh_data(force_reload=True)

    def _on_fetch_finished(self):
        """Called when FetchWorker thread finishes."""
        try:
            self._is_refreshing = False
            self._fetch_worker = None
            if self._pending_refresh and not self._is_refreshing:
                self._pending_refresh = False
                # Use QTimer.singleShot to avoid re-entering during signal handling
                QTimer.singleShot(200, self._refresh_data)
        except Exception:
            print(f"[WorldCupOverlay] Error in _on_fetch_finished (prevented crash):\n{traceback.format_exc()}", file=sys.stderr)

    def _force_refresh(self):
        """Force refresh data from network (with cooldown check).
        
        Called by:
        - Right-click menu "立即刷新"
        - Ctrl+R shortcut
        - Tray icon menu "立即刷新"
        - Initial app load (skips cooldown check)
        """
        # Initial load: skip cooldown check
        if not self._has_ever_loaded:
            self._refresh_data(force_reload=True, skip_cooldown=True)
        else:
            self._refresh_data(force_reload=True)

    def _update_cooldown(self):
        """Update the cooldown countdown in status bar every second."""
        try:
            if not hasattr(self, '_cooldown_remaining'):
                return

            self._cooldown_remaining -= 1
            if self._cooldown_remaining <= 0:
                # Cooldown ended, stop timer and restore status
                if hasattr(self, '_cooldown_timer') and self._cooldown_timer:
                    self._cooldown_timer.stop()
                    self._cooldown_timer = None
                self._update_status()
            else:
                # Update countdown display
                self.status_bar.setText(f"⏳ 刷新过于频繁，请等待 {self._cooldown_remaining} 秒")
        except Exception:
            print(f"[WorldCupOverlay] Error in _update_cooldown (prevented crash):\n{traceback.format_exc()}", file=sys.stderr)

    # ---- Notifications ----

    def _status_display(self, m) -> str:
        """Return a Chinese display string for the match status + minute.
        Examples: "上半场 10'", "下半场 64'", "中场休息", "加时 105'", "点球大战"
        """
        stage_map = {
            "1h": "上半场",
            "2h": "下半场",
            "ht": "中场休息",
            "et": "加时",
            "pen": "点球大战",
            "live": "进行中",
        }
        stage = stage_map.get(m.status, "进行中")
        if m.status in ("ht", "pen"):
            return stage
        # m.minute 可能已经是 "90'+1" 格式（带'），不要再追加'
        minute_raw = str(m.minute) if m.minute else ""
        if minute_raw and not minute_raw.endswith("'"):
            minute_raw = minute_raw + "'"
        if m.status == "et":
            return f"加时 {minute_raw}" if minute_raw else "加时赛"
        return f"{stage} {minute_raw}" if minute_raw else stage

    def _parse_minute(self, minute_str) -> int:
        """Parse a minute string like "27'", "45+2'", "HT" into an int.
        Returns 999 if parsing fails (treated as 'not early in match')."""
        if not minute_str:
            return 999
        s = str(minute_str).strip().rstrip("'")
        # Handle "45+2" format — take the base part before '+'
        if "+" in s:
            s = s.split("+")[0]
        try:
            return int(s)
        except (ValueError, TypeError):
            return 999

    def _check_notifications(self, new_matches: list):
        """Send macOS + PushPlus notifications for match events.

        逻辑（按用户 2026-07-06 描述）：
        - 只用 _last_pushed_score 判断：这场比赛已推送的比分是多少
        - 没有记录 → 推"比赛已开始 N 分钟，当前比分 X-Y"，并记录
        - 有记录 + 当前比分 == 已推送比分 → 不推
        - 有记录 + 当前比分 != 已推送比分 → 推"比分更新 A-B → X-Y"，并更新记录
        - 比赛结束 → 推"比赛结束"，并记录（避免重复推）
        """
        # Debug log
        import datetime
        with open("/tmp/worldcup_notify.log", "a") as f:
            f.write(f"[{datetime.datetime.now()}] _check_notifications: {len(new_matches)} matches, last_pushed_score={self._last_pushed_score}\n")
        live_statuses = {"1h", "2h", "ht", "et", "pen", "live"}

        for m in new_matches:
            last = self._last_pushed_score.get(m.match_id)

            if m.status in live_statuses:
                if last is None:
                    # 第一次推这场比赛
                    status_disp = self._status_display(m)
                    if m.home_score == 0 and m.away_score == 0 and (not m.minute or self._parse_minute(m.minute) <= 10):
                        title = f"⚽ 比赛开始 · {m.home_team} vs {m.away_team}"
                        body = f"已开赛 {m.minute or '?'}分钟 · 当前比分：{m.home_score}-{m.away_score}"
                    else:
                        title = f"⚽ {status_disp} · {m.home_team} vs {m.away_team}"
                        body = f"{status_disp} · 当前比分：{m.home_score}-{m.away_score}"
                    # 先推 macOS 通知
                    self._send_notification(title, body)
                    # 再推 PushPlus，成功才记录
                    push_ok = True  # macOS 通知基本不会失败
                    if self._pushplus_on_start:
                        t, c = self._format_match_push(m, "start")
                        if not self._send_pushplus(t, c):
                            push_ok = False
                    # 只有推送成功后才记录（避免推送失败但记录已写入，导致下次不推）
                    if push_ok or not self._pushplus_on_start:
                        self._last_pushed_score[m.match_id] = [m.home_score, m.away_score, m.home_penalty, m.away_penalty]
                        self._save_settings()
                else:
                    # Pad old 2-element data to 4 elements
                    if len(last) < 4:
                        last = last + [0, 0] * (4 - len(last))
                    prev_home, prev_away = last[0], last[1]
                    prev_home_pen = last[2] if len(last) > 2 else 0
                    prev_away_pen = last[3] if len(last) > 3 else 0

                    # 1. Check regular score change
                    if m.home_score != prev_home or m.away_score != prev_away:
                        # 比分变化，推送
                        home_delta = m.home_score - prev_home
                        away_delta = m.away_score - prev_away
                        # 区分进球和进球取消
                        is_cancellation = home_delta < 0 or away_delta < 0
                        is_new_goal = home_delta > 0 or away_delta > 0
                        if is_cancellation and not is_new_goal:
                            self._send_notification(
                                f"⚠️ 进球取消 · {m.home_team} {m.home_score}-{m.away_score} {m.away_team}",
                                f"比分回退：{prev_home}-{prev_away} → {m.home_score}-{m.away_score}"
                            )
                        else:
                            self._send_notification(
                                f"⚽ 比分变化 · {m.home_team} {m.home_score}-{m.away_score} {m.away_team}",
                                f"比分更新：{prev_home}-{prev_away} → {m.home_score}-{m.away_score}"
                            )
                        push_ok = True
                        if self._pushplus_on_goal:
                            t, c = self._format_goal_push(m, home_delta, away_delta)
                            if not self._send_pushplus(t, c):
                                push_ok = False
                        if push_ok or not self._pushplus_on_goal:
                            self._last_pushed_score[m.match_id] = [m.home_score, m.away_score, m.home_penalty, m.away_penalty]
                            self._save_settings()

                    # 2. Check penalty score change (点球大战)
                    elif m.status == "pen" and (m.home_penalty != prev_home_pen or m.away_penalty != prev_away_pen):
                        pen_delta_home = m.home_penalty - prev_home_pen
                        pen_delta_away = m.away_penalty - prev_away_pen
                        # Determine which team scored the penalty
                        if pen_delta_home > 0:
                            pen_team = m.home_team
                        elif pen_delta_away > 0:
                            pen_team = m.away_team
                        else:
                            pen_team = ""

                        self._send_notification(
                            f"🥅 点球 · {m.home_team} {m.home_penalty}-{m.away_penalty} {m.away_team}",
                            f"点球比分更新：{prev_home_pen}-{prev_away_pen} → {m.home_penalty}-{m.away_penalty}"
                            + (f"\n{pen_team} 命中点球" if pen_team else "")
                        )
                        push_ok = True
                        if self._pushplus_on_goal:
                            # PushPlus: title = 球队名 点球, content = 点球明细
                            pen_title = f"{pen_team} 点球命中" if pen_team else f"{m.home_team} VS {m.away_team} 点球命中"
                            pen_content_lines = [
                                f"{m.home_team} {m.home_score}-{m.away_score} {m.away_team}（加时结束）",
                                f"点球大战：{m.home_penalty} - {m.away_penalty}",
                            ]
                            pen_content = "<br>".join(pen_content_lines)
                            if not self._send_pushplus(pen_title, pen_content):
                                push_ok = False
                        if push_ok or not self._pushplus_on_goal:
                            self._last_pushed_score[m.match_id] = [m.home_score, m.away_score, m.home_penalty, m.away_penalty]
                            self._save_settings()

            elif m.status == "finished":
                # 比赛结束，推送（如果之前没推送过结束通知）
                if m.match_id not in self._end_pushed:
                    # macOS 通知：有点球时显示点球结果
                    has_pen = getattr(m, "has_penalties", False)
                    if has_pen:
                        if m.home_penalty > m.away_penalty:
                            end_title = f"🏆 {m.home_team} 获胜 {m.home_penalty}:{m.away_penalty}（点球）"
                        else:
                            end_title = f"🏆 {m.away_team} 获胜 {m.home_penalty}:{m.away_penalty}（点球）"
                        end_msg = f"点球大战：{m.home_penalty} - {m.away_penalty}"
                        if getattr(m, "has_extra_time", False):
                            end_msg += f" · 加时：{m.home_extra_time}-{m.away_extra_time}"
                        end_msg += f" · 常规：{m.home_score}-{m.away_score}"
                    else:
                        end_title = f"🏁 {m.home_team} VS {m.away_team} 比赛结束"
                        end_msg = f"最终比分：{m.home_score} - {m.away_score} · 全场比赛结束"
                    self._send_notification(
                        end_title,
                        end_msg
                    )
                    push_ok = True
                    if self._pushplus_on_start:
                        t, c = self._format_match_push(m, "end")
                        if not self._send_pushplus(t, c):
                            push_ok = False
                    if push_ok or not self._pushplus_on_start:
                        self._end_pushed.add(m.match_id)
                        self._last_pushed_score[m.match_id] = [m.home_score, m.away_score, m.home_penalty, m.away_penalty]
                        self._save_settings()

            # ---- 半场 / 阶段结束推送 ----
            prev_state = self._prev_matches_state.get(m.match_id)
            prev_status = prev_state[0] if prev_state else None

            # 1. 半场结束（status == "ht"）
            if m.status == "ht" and m.match_id not in self._ht_pushed:
                self._send_notification(
                    f"⏸ 半场结束 · {m.home_team} vs {m.away_team}",
                    f"半场比分：{m.home_score} - {m.away_score}"
                )
                push_ok = True
                if self._pushplus_on_start:
                    t = f"{m.home_team} VS {m.away_team} 半场结束"
                    c_lines = [f"半场比分：{m.home_score} - {m.away_score}"]
                    if (m.home_score > 0 or m.away_score > 0) and hasattr(self, 'api'):
                        try:
                            summary = self.api.fetch_match_summary(m.match_id)
                            if summary:
                                all_goals = self.api.extract_all_goals_from_summary(summary, m)
                                if all_goals:
                                    c_lines.append("进球明细：")
                                for g in all_goals:
                                    g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                                    period_cn = g.get('period_cn', '')
                                    c_lines.append(
                                        f"  {period_cn} {g['minute']} {g['scorer']}{g_team} {g['method']}  {g['score']}"
                                    )
                        except Exception:
                            pass
                    c = "<br>".join(c_lines)
                    if not self._send_pushplus(t, c):
                        push_ok = False
                if push_ok or not self._pushplus_on_start:
                    self._ht_pushed.add(m.match_id)
                    self._save_settings()

            # 2. 常规时间结束 → 进入加时（2h → et）
            if (prev_status == "2h" and m.status == "et"
                    and m.match_id not in self._reg_end_pushed):
                self._send_notification(
                    f"⏱ 常规时间结束 · {m.home_team} vs {m.away_team}",
                    f"常规时间比分：{m.home_score} - {m.away_score} · 进入加时赛"
                )
                push_ok = True
                if self._pushplus_on_start:
                    t = f"{m.home_team} VS {m.away_team} 常规时间结束"
                    c_lines = [
                        f"常规时间比分：{m.home_score} - {m.away_score}",
                        "进入加时赛 ⚡",
                    ]
                    if (m.home_score > 0 or m.away_score > 0) and hasattr(self, 'api'):
                        try:
                            summary = self.api.fetch_match_summary(m.match_id)
                            if summary:
                                all_goals = self.api.extract_all_goals_from_summary(summary, m)
                                if all_goals:
                                    c_lines.append("进球明细：")
                                for g in all_goals:
                                    g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                                    period_cn = g.get('period_cn', '')
                                    c_lines.append(
                                        f"  {period_cn} {g['minute']} {g['scorer']}{g_team} {g['method']}  {g['score']}"
                                    )
                        except Exception:
                            pass
                    c = "<br>".join(c_lines)
                    if not self._send_pushplus(t, c):
                        push_ok = False
                if push_ok or not self._pushplus_on_start:
                    self._reg_end_pushed.add(m.match_id)
                    self._save_settings()

            # 3. 加时赛结束 → 进入点球（et → pen）
            if (prev_status == "et" and m.status == "pen"
                    and m.match_id not in self._et_end_pushed):
                self._send_notification(
                    f"⏱ 加时赛结束 · {m.home_team} vs {m.away_team}",
                    f"加时比分：{m.home_score} - {m.away_score} · 进入点球大战"
                )
                push_ok = True
                if self._pushplus_on_start:
                    t = f"{m.home_team} VS {m.away_team} 加时赛结束"
                    c_lines = [
                        f"加时比分：{m.home_score} - {m.away_score}",
                        "进入点球大战 ⚽",
                    ]
                    if (m.home_score > 0 or m.away_score > 0) and hasattr(self, 'api'):
                        try:
                            summary = self.api.fetch_match_summary(m.match_id)
                            if summary:
                                all_goals = self.api.extract_all_goals_from_summary(summary, m)
                                if all_goals:
                                    c_lines.append("进球明细：")
                                for g in all_goals:
                                    g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                                    period_cn = g.get('period_cn', '')
                                    c_lines.append(
                                        f"  {period_cn} {g['minute']} {g['scorer']}{g_team} {g['method']}  {g['score']}"
                                    )
                        except Exception:
                            pass
                    c = "<br>".join(c_lines)
                    if not self._send_pushplus(t, c):
                        push_ok = False
                if push_ok or not self._pushplus_on_start:
                    self._et_end_pushed.add(m.match_id)
                    self._save_settings()

        # 更新 prev 状态，供下次 fetch 对比
        self._prev_matches_state = {
            m.match_id: (m.status, m.home_score, m.away_score)
            for m in new_matches
        }


    def _send_notification(self, title: str, body: str):
        """Send a macOS system notification via osascript.
        Uses a temp file to avoid shell-escaping issues with Chinese/emoji."""
        import tempfile
        try:
            script = (
                'display notification "' +
                body.replace('\\', '\\\\').replace('"', '\\"') +
                '" with title "' +
                title.replace('\\', '\\\\').replace('"', '\\"') +
                '" sound name "Glass"'
            )
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.applescript', delete=False
            ) as f:
                f.write(script)
                tmp_path = f.name
            subprocess.Popen(["osascript", tmp_path],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            # Clean up temp file after a delay
            def _cleanup():
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            QTimer.singleShot(5000, _cleanup)
        except Exception:
            pass

    def _send_pushplus(self, title: str, content: str) -> bool:
        """Send a push notification via PushPlus (pushplus.plus).
        Supports multiple tokens separated by '/' — each token gets its own push.
        Returns True if at least one push succeeded, False otherwise."""
        raw = getattr(self, "_pushplus_token", "")
        if not raw:
            return False
        # Split on '/' and strip whitespace; ignore empty fragments
        tokens = [t.strip() for t in raw.split("/") if t.strip()]
        if not tokens:
            return False
        url = "https://www.pushplus.plus/api/send"
        headers = {"Content-Type": "application/json"}
        any_success = False
        for token in tokens:
            try:
                payload = {
                    "token": token,
                    "title": title,
                    "content": content,
                }
                resp = requests.post(url, json=payload, headers=headers, timeout=5)
                if resp.ok:
                    any_success = True
            except Exception:
                # One bad token shouldn't stop the others from receiving the push
                continue
        return any_success

    def _format_match_push(self, m, kind: str, home_delta: int = 0, away_delta: int = 0) -> tuple:
        """Return (title, content) for a PushPlus push.
        kind: 'start', 'score_change', or 'live_catchup'.
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
            # 如果比分不是0-0，说明比赛已进行一段时间，不写"比赛开始"
            if m.home_score == 0 and m.away_score == 0 and (not m.minute or self._parse_minute(m.minute) <= 10):
                title = f"{m.home_team} VS {m.away_team} 比赛开始"
            else:
                status_disp = self._status_display(m)
                title = f"{m.home_team} VS {m.away_team} {status_disp}"
            # content: 状态和比分 + 进球明细（如果有的话）
            lines = [
                f"{self._status_display(m)} · {m.home_team} vs {m.away_team}",
                f"当前比分：{m.home_score} - {m.away_score}",
            ]
            # 比分不是0-0时，尝试获取进球明细
            if (m.home_score > 0 or m.away_score > 0) and hasattr(self, 'api'):
                try:
                    summary = self.api.fetch_match_summary(m.match_id)
                    if summary:
                        all_goals = self.api.extract_all_goals_from_summary(summary, m)
                        if all_goals:
                            lines.append("进球明细：")
                        for g in all_goals:
                            g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                            # 显示格式：比赛阶段 + 时间 + 球员 + 方式 + 比分
                            period_cn = g.get('period_cn', '')
                            lines.append(
                                f"  {period_cn} {g['minute']} {g['scorer']}{g_team} {g['method']}  {g['score']}"
                            )
                except Exception:
                    pass
            content = "<br>".join(lines)
        elif kind == "end":
            # 有点球时，显示点球结果和胜者
            has_pen = getattr(m, "has_penalties", False)
            if has_pen:
                # 点球大战结束：谁胜 + 点球比分
                if m.home_penalty > m.away_penalty:
                    title = f"{m.home_team}胜利 {m.home_penalty}:{m.away_penalty}（点球）"
                    winner = m.home_team
                else:
                    title = f"{m.away_team}胜利 {m.home_penalty}:{m.away_penalty}（点球）"
                    winner = m.away_team
                lines = [
                    f"点球大战：{m.home_penalty} - {m.away_penalty}",
                    f"🏆 {winner} 获胜",
                ]
                # 有加时赛时显示加时比分
                if getattr(m, "has_extra_time", False):
                    lines.append(f"加时比分：{m.home_extra_time} - {m.away_extra_time}")
                lines.append(f"常规比分：{m.home_score} - {m.away_score}")
            else:
                title = f"{m.home_team} VS {m.away_team} {m.home_score}:{m.away_score}"
                lines = [
                    f"最终比分：{m.home_score} - {m.away_score}",
                    "全场比赛结束 🏁",
                ]
            # 比赛结束时也包含进球明细
            if (m.home_score > 0 or m.away_score > 0) and hasattr(self, 'api'):
                try:
                    summary = self.api.fetch_match_summary(m.match_id)
                    if summary:
                        all_goals = self.api.extract_all_goals_from_summary(summary, m)
                        if all_goals:
                            lines.append("进球明细：")
                        for g in all_goals:
                            g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                            # 显示格式：比赛阶段 + 时间 + 球员 + 方式 + 比分
                            period_cn = g.get('period_cn', '')
                            lines.append(
                                f"  {period_cn} {g['minute']} {g['scorer']}{g_team} {g['method']}  {g['score']}"
                            )
                except Exception:
                    pass
            content = "<br>".join(lines)
        elif kind == "live_catchup":
            # First-load catch-up: match is already live with goals
            status_disp = self._status_display(m)
            title = (
                f"⚽ 进行中 {m.home_team} {m.home_score}-{m.away_score} "
                f"{m.away_team}"
            )
            lines = [
                f"{m.home_team} vs {m.away_team}",
                f"{status_disp} · 当前比分：{m.home_score} - {m.away_score}",
            ]
            # 包含进球明细
            if (m.home_score > 0 or m.away_score > 0) and hasattr(self, 'api'):
                try:
                    summary = self.api.fetch_match_summary(m.match_id)
                    if summary:
                        all_goals = self.api.extract_all_goals_from_summary(summary, m)
                        if all_goals:
                            lines.append("进球明细：")
                        for g in all_goals:
                            g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                            # 显示格式：比赛阶段 + 时间 + 球员 + 方式 + 比分
                            period_cn = g.get('period_cn', '')
                            lines.append(
                                f"  {period_cn} {g['minute']} {g['scorer']}{g_team} {g['method']}  {g['score']}"
                            )
                except Exception:
                    pass
            content = "<br>".join(lines)
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

            # Content: 当前比分 + 进球明细
            lines = [
                f"{m.home_team} vs {m.away_team}",
                f"当前比分：{m.home_score} - {m.away_score}{stage_tag}",
            ]
            if delta_line:
                lines.append(delta_line)
            
            # 包含进球明细
            if (m.home_score > 0 or m.away_score > 0) and hasattr(self, 'api'):
                try:
                    summary = self.api.fetch_match_summary(m.match_id)
                    if summary:
                        all_goals = self.api.extract_all_goals_from_summary(summary, m)
                        if all_goals:
                            lines.append("进球明细：")
                        for g in all_goals:
                            g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                            # 显示格式：比赛阶段 + 时间 + 球员 + 方式 + 比分
                            period_cn = g.get('period_cn', '')
                            lines.append(
                                f"  {period_cn} {g['minute']} {g['scorer']}{g_team} {g['method']}  {g['score']}"
                            )
                            # 如果有比分回退（进球取消），添加提示
                            if has_correction:
                                cancelled_teams = []
                                if home_delta < 0:
                                    cancelled_teams.append(m.home_team)
                                if away_delta < 0:
                                    cancelled_teams.append(m.away_team)
                                cancelled_str = "、".join(cancelled_teams)
                                prev_home = m.home_score - home_delta
                                prev_away = m.away_score - away_delta
                                lines.append(
                                    f"⚠️ 注：{cancelled_str}一粒进球已被VAR取消"
                                    f"（原比分 {prev_home}-{prev_away} → {m.home_score}-{m.away_score}）"
                                )
                except Exception:
                    pass

            content = "<br>".join(lines)
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
        # 检测是否为进球取消（比分下降）
        has_cancellation = home_delta < 0 or away_delta < 0
        has_new_goal = home_delta > 0 or away_delta > 0

        if has_cancellation and not has_new_goal:
            # 纯进球取消（VAR取消进球等）
            cancelled_teams = []
            if home_delta < 0:
                cancelled_teams.append(m.home_team)
            if away_delta < 0:
                cancelled_teams.append(m.away_team)
            cancelled_str = "、".join(cancelled_teams)

            title = f"⚠️ {cancelled_str} 进球取消"

            br = "<br>"
            prev_home = m.home_score - home_delta
            prev_away = m.away_score - away_delta
            lines = [
                f"{m.home_team} {m.home_score}-{m.away_score} {m.away_team}",
                f"⚠️ {cancelled_str}一粒进球已被取消（原比分 {prev_home}-{prev_away} → {m.home_score}-{m.away_score}）",
            ]
            # 获取当前剩余进球明细
            try:
                summary = self.api.fetch_match_summary(m.match_id)
                all_goals = self.api.extract_all_goals_from_summary(summary, m)
            except Exception:
                all_goals = []
            if all_goals:
                lines.append("剩余进球明细：")
                for g in all_goals:
                    g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                    g_minute = g.get('minute', '')
                    period_cn = g.get('period_cn', '')
                    lines.append(
                        f"  {period_cn} {g_minute} {g['scorer']}{g_team} {g['method']}  {g['score']}"
                    )
            else:
                lines.append("（进球明细获取失败）")
            content = br.join(lines)
            return title, content

        # 以下为正常进球（或混合情况）逻辑
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
        # 用户要求：标题写 "队伍 头球 进球" 这样的格式（球队名 + 进球方式）
        # 开局和结束推送的标题保持不变，只有进球推送改用此格式
        if team_cn:
            title = f"{team_cn} {method}"
        else:
            #  fallback：如果无法获取球队名，使用原来的格式
            if minute and minute not in ("0'", "0''"):
                title = f"⚽ {minute} {scorer} {method}  {score_str}"
            else:
                title = f"⚽ {scorer} {method}  {score_str}"

        # --- Content: full goal log for this match ---
        # Use <br> for line breaks (WeChat renders HTML in PushPlus content).
        br = "<br>"
        if all_goals:
            lines = [f"{m.home_team} {m.home_score}-{m.away_score} {m.away_team}  进球明细："]
            for g in all_goals:
                g_team = f"（{g['team_cn']}）" if g.get('team_cn') else ""
                g_minute = g.get('minute', '')
                period_cn = g.get('period_cn', '')
                lines.append(
                    f"  {period_cn} {g_minute} {g['scorer']}{g_team} {g['method']}  {g['score']}"
                )
            # 混合情况：既有进球又有取消，加取消提示
            if has_cancellation:
                cancelled_teams = []
                if home_delta < 0:
                    cancelled_teams.append(m.home_team)
                if away_delta < 0:
                    cancelled_teams.append(m.away_team)
                cancelled_str = "、".join(cancelled_teams)
                prev_home = m.home_score - home_delta
                prev_away = m.away_score - away_delta
                lines.append(
                    f"⚠️ 注：{cancelled_str}一粒进球已被取消"
                    f"（原比分 {prev_home}-{prev_away} → {m.home_score}-{m.away_score}）"
                )
            content = br.join(lines)
        else:
            # summary 获取失败，至少显示当前比分和最后进球
            content = (
                f"{m.home_team} {m.home_score}-{m.away_score} {m.away_team}{br}"
                f"进球明细：（详情获取失败，当前比分：{m.home_score}-{m.away_score}）"
            )

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
        # We deliberately do NOT call self.repaint() / self.update() at the
        # window level — those go through the window manager and can briefly
        # take focus on Windows / macOS, causing the overlay to "pop to
        # foreground" when the user hid it on the dock. The viewport
        # repaint below is enough to flush card content.
        self.scroll_area.viewport().update()

        # Process pending layout/paint events immediately so cards render
        # without this, macOS may defer rendering and show blank frames
        QApplication.processEvents()

        # Calculate height
        card_count = len(display_matches)
        total_height = 44 + 24 + card_count * 140 + 8 + 28
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
        card.setFixedHeight(120)
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
        self._update_height(44 + 24 + 140 + 8 + 28)

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
            self.hide()
            # Build the menu bar icon (as a secondary affordance)
            if not hasattr(self, "_tray_icon") or self._tray_icon is None:
                self._tray_icon = self._create_tray_icon()
            # In-app shortcut for ⌘⇧W (works if the user is in this app)
            self._register_global_hotkey()
        except Exception:
            pass

    def _nswindow_order_out(self):
        """Qt-only: just hide the window."""
        self.hide()

    def _nswindow_order_front(self):
        """Qt-only: show and raise the window."""
        self.show()
        self.raise_()
        self.activateWindow()
        self.setWindowState(Qt.WindowNoState)

    def _register_dock_click_handler(self):
        """Dock click handler via Qt — ⌘+Tab or clicking dock icon
        will raise the window if it's hidden."""
        # Pure Qt: we rely on showEvent / activateWindow to
        # handle dock clicks.  The PyObjC NSApplication delegate
        # path is disabled because pyobjc isn't in our venv.
        try:
            if sys.platform != "darwin":
                return
            # Qt handles dock-click → showEvent on macOS reliably
            # since we set WA_ShowWithoutActivating and use
            # showEvent to rebuild cards. No extra handler needed.
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
        """Global key monitor disabled — requires PyObjC which is
        not installed in the venv.  Use ⌘⇧W in-app shortcut
        instead."""
        return

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
        refresh_action.triggered.connect(self._force_refresh)
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
        refresh_action.triggered.connect(self._force_refresh)
        refresh_action.setEnabled(not self._is_refreshing)
        menu.addAction(refresh_action)

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
                self._force_refresh()
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

    # --- Global exception hook: prevent PyQt5 abort() on unhandled slot errors ---
    def _global_excepthook(exc_type, exc_value, exc_tb):
        """Catch unhandled exceptions in Qt slots to prevent SIGABRT crash."""
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            log_path = os.path.join(os.path.expanduser("~"), ".worldcup_overlay", "error.log")
            with open(log_path, "a") as f:
                f.write(f"\n[{datetime.now().isoformat()}] Unhandled exception:\n{tb_str}\n")
        except Exception:
            pass
        print(f"[WorldCupOverlay] Unhandled exception caught (prevented crash):\n{tb_str}", file=sys.stderr)

    sys.excepthook = _global_excepthook

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
