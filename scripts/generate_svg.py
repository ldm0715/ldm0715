#!/usr/bin/env python3
"""抓取 GitHub 数据并渲染成静态 SVG 卡片。

优先走 GraphQL（可拿到贡献日历，用于连续天数），失败时回退到 REST。
产物写到 assets/ 目录，由 GitHub Actions 定时刷新，README 直接引用本地文件，
不再依赖任何第三方统计图服务。仅使用标准库，无需安装依赖。
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import sys
import urllib.request
from datetime import date

API = "https://api.github.com"
USER = os.environ.get("GH_USER", "ldm0715")
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
OUT_DIR = os.environ.get("OUT_DIR", "assets")

# 极客风：等宽字体 + 终端绿点缀，无卡片底。文字配色靠内嵌的
# prefers-color-scheme 规则跟随页面主题。已验证：宿主页面设了 color-scheme 时，
# <img> 里 SVG 的媒体查询会跟着触发，而 GitHub 的深色主题正是这么设的。
INK = "#1F2328"     # 主要文字
MUTED = "#59636E"   # 次要文字
ACCENT = "#0969DA"  # 极客蓝点缀
BORDER = "#D0D7DE"  # 描边与分割线
FONT = (
    "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,"
    "'Liberation Mono',monospace"
)

# 深色主题下把上面几个颜色整体换掉
DARK_STYLE = (
    "<style>@media (prefers-color-scheme:dark){"
    'text[fill="#1F2328"]{fill:#E6EDF3}'
    'text[fill="#59636E"]{fill:#8B949E}'
    'text[fill="#0969DA"]{fill:#58A6FF}'
    'line[stroke="#D0D7DE"]{stroke:#30363D}'
    "}</style>"
)

# 生成型仓库（如 Hexo 博客的输出产物）会严重拉偏语言占比，不计入语言统计，
# 但仍计入仓库数与 star 数。按需增删。
EXCLUDE_FROM_LANGS = {"ldm0715.github.io"}

# 常见语言的官方配色，未收录的语言回退到 PALETTE
LANG_COLORS = {
    "Python": "#3572A5", "CSS": "#563D7C", "JavaScript": "#F1E05A",
    "TypeScript": "#3178C6", "C#": "#178600", "Go": "#00ADD8",
    "Dart": "#00B4AB", "HTML": "#E34C26", "Java": "#B07219",
    "C++": "#F34B7D", "C": "#555555", "Shell": "#89E051",
    "Rust": "#DEA584", "Vue": "#41B883", "Jupyter Notebook": "#DA5B0B",
    "PHP": "#4F5D95", "Kotlin": "#A97BFF", "Lua": "#000080",
    "Dockerfile": "#384D54", "Ruby": "#701516", "Swift": "#F05138",
    "TeX": "#3D6117", "Batchfile": "#C1F12E", "PowerShell": "#012456",
    "Makefile": "#427819", "Nix": "#7E7EFF",
}
PALETTE = ["#C86B4A", "#2E4756", "#6B8E9F", "#B08968", "#7D8CA3", "#9C6644"]

# 图标取自 simple-icons，路径数据固化在 icons.json，渲染时不再联网
ICONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons.json")
try:
    with open(ICONS_PATH, encoding="utf-8") as _fh:
        ICONS = json.load(_fh)
except FileNotFoundError:
    ICONS = {}

# 技术栈卡片内容，手写维护，格式为 (显示名, 图标 slug, 品牌色)
TECH_STACK = [
    ("语言", [
        ("Go", "go", "#00ADD8"),
        ("C#", "csharp", "#239120"),
        ("Python", "python", "#3776AB"),
        ("TypeScript", "typescript", "#3178C6"),
        ("JavaScript", "javascript", "#F7DF1E"),
        ("Dart", "dart", "#0175C2"),
        ("CSS", "css3", "#1572B6"),
    ]),
    ("框架 / 工具", [
        ("Tauri v2", "tauri", "#24C8DB"),
        ("WinUI 3", "microsoft", "#5E5E5E"),
        ("Flutter", "flutter", "#02569B"),
        ("Qt", "qt", "#41CD52"),
        ("Hugo", "hugo", "#FF4088"),
        ("Node.js", "nodedotjs", "#5FA04E"),
        ("Tampermonkey", "tampermonkey", "#00485B"),
    ]),
    ("环境", [
        ("Windows", "windows", "#0078D6"),
        ("Linux", "linux", "#FCC624"),
        ("Git", "git", "#F05032"),
    ]),
]

# Material 星形图标路径（24x24 viewBox）
STAR_PATH = "M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"

GRAPHQL_QUERY = """
query ($login: String!) {
  user(login: $login) {
    followers { totalCount }
    pinnedItems(first: 6, types: [REPOSITORY]) {
      nodes {
        ... on Repository {
          name
          description
          stargazerCount
          primaryLanguage { name color }
        }
      }
    }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC) {
      totalCount
      nodes {
        name
        stargazerCount
        primaryLanguage { name color }
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
  }
}
"""


def request(url: str, data: bytes | None = None, headers: dict | None = None):
    """发一个带鉴权的 GitHub API 请求并解析 JSON。"""
    merged = {"Accept": "application/vnd.github+json", "User-Agent": "profile-stats"}
    if TOKEN:
        merged["Authorization"] = f"Bearer {TOKEN}"
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=data, headers=merged)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_graphql() -> dict:
    payload = json.dumps({"query": GRAPHQL_QUERY, "variables": {"login": USER}}).encode()
    result = request(
        f"{API}/graphql", data=payload, headers={"Content-Type": "application/json"}
    )
    if "errors" in result:
        raise RuntimeError(result["errors"])
    return result["data"]["user"]


def fetch_rest() -> dict:
    """没有 token 或 GraphQL 不可用时的降级路径，拿不到贡献日历。"""
    profile = request(f"{API}/users/{USER}")
    repos = [r for r in request(f"{API}/users/{USER}/repos?per_page=100&type=owner") if not r["fork"]]
    langs: dict[str, int] = {}
    for repo in repos:
        if repo["name"] in EXCLUDE_FROM_LANGS:
            continue
        for name, size in request(f"{API}/repos/{USER}/{repo['name']}/languages").items():
            langs[name] = langs.get(name, 0) + size
    return {
        "repos": len(repos),
        "stars": sum(r["stargazers_count"] for r in repos),
        "followers": profile["followers"],
        "contributions": None,
        "days": [],
        "languages": sorted(langs.items(), key=lambda kv: kv[1], reverse=True),
        # REST 拿不到置顶，退化成 star 最多的前 6 个
        "pinned": [
            {
                "name": r["name"],
                "desc": (r.get("description") or "").strip(),
                "language": r["language"] or "-",
                "color": LANG_COLORS.get(r["language"], MUTED),
                "stars": r["stargazers_count"],
            }
            for r in sorted(repos, key=lambda x: x["stargazers_count"], reverse=True)[:6]
        ],
    }


def normalize(user: dict) -> dict:
    nodes = user["repositories"]["nodes"]
    langs: dict[str, int] = {}
    for node in nodes:
        if node["name"] in EXCLUDE_FROM_LANGS:
            continue
        for edge in node["languages"]["edges"]:
            name = edge["node"]["name"]
            langs[name] = langs.get(name, 0) + edge["size"]
    calendar = user["contributionsCollection"]["contributionCalendar"]
    days = [
        (day["date"], day["contributionCount"])
        for week in calendar["weeks"]
        for day in week["contributionDays"]
    ]
    pinned_nodes = (user.get("pinnedItems") or {}).get("nodes") or []
    pinned = [
        {
            "name": node["name"],
            "desc": (node.get("description") or "").strip(),
            "language": (node["primaryLanguage"] or {}).get("name", "-"),
            "color": (node["primaryLanguage"] or {}).get("color") or MUTED,
            "stars": node["stargazerCount"],
        }
        for node in pinned_nodes
        if node
    ]
    return {
        "repos": user["repositories"]["totalCount"],
        "stars": sum(n["stargazerCount"] for n in nodes),
        "followers": user["followers"]["totalCount"],
        "contributions": calendar["totalContributions"],
        "days": days,
        "languages": sorted(langs.items(), key=lambda kv: kv[1], reverse=True),
        "pinned": pinned,
    }


def compute_streak(days: list[tuple[str, int]]) -> tuple[int, int]:
    """返回 (当前连续天数, 最长连续天数)。"""
    counts = [c for _, c in days]
    longest = run = 0
    for count in counts:
        run = run + 1 if count > 0 else 0
        longest = max(longest, run)
    current = 0
    i = len(counts) - 1
    if i >= 0 and counts[i] == 0:
        i -= 1  # 今天尚未贡献不算中断
    while i >= 0 and counts[i] > 0:
        current += 1
        i -= 1
    return current, longest


def svg_open(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">{DARK_STYLE}'
    )


def text(x, y, value, size, fill, weight="400", anchor="start", spacing=None) -> str:
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}>'
        f"{html.escape(str(value))}</text>"
    )


def render_stats(data: dict) -> str:
    width, height = 400, 180
    tiles = [
        ("总获星", data["stars"]),
        ("公开仓库", data["repos"]),
        ("关注者", data["followers"]),
        ("近一年贡献", data["contributions"] if data["contributions"] is not None else "-"),
    ]
    out = [svg_open(width, height)]
    out.append(text(22, 38, "GitHub 统计", 14, ACCENT, "700", spacing="1"))
    out.append(f'<line x1="22" y1="56" x2="{width - 22}" y2="56" stroke="{BORDER}"/>')
    for (label, value), cx in zip(tiles, (60, 160, 260, 360)):
        out.append(text(cx, 112, value, 26, INK, "700", anchor="middle"))
        out.append(text(cx, 138, label, 12, MUTED, anchor="middle"))
    out.append("</svg>")
    return "\n".join(out)


def arc_path(cx, cy, r, start, end, fill) -> str:
    x1 = cx + r * math.cos(math.radians(start))
    y1 = cy + r * math.sin(math.radians(start))
    x2 = cx + r * math.cos(math.radians(end))
    y2 = cy + r * math.sin(math.radians(end))
    large = 1 if (end - start) > 180 else 0
    d = f"M {cx} {cy} L {x1:.2f} {y1:.2f} A {r} {r} 0 {large} 1 {x2:.2f} {y2:.2f} Z"
    return f'<path d="{d}" fill="{fill}"/>'


def render_pie(languages: list[tuple[str, int]]) -> str:
    width, height = 400, 180
    cx, cy, r = 100, 98, 56
    keep = languages[:6]
    rest = sum(size for _, size in languages[6:])
    slices = [(name, size, LANG_COLORS.get(name, PALETTE[i % len(PALETTE)]))
              for i, (name, size) in enumerate(keep)]
    if rest > 0:
        slices.append(("其他", rest, "#C9BCA8"))
    total = sum(size for _, size, _ in slices) or 1

    out = [svg_open(width, height)]
    out.append(text(22, 38, "常用语言", 14, ACCENT, "700", spacing="1"))
    if len(slices) == 1:
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{slices[0][2]}"/>')
    else:
        angle = -90.0
        for _, size, color in slices:
            sweep = size / total * 360.0
            out.append(arc_path(cx, cy, r, angle, angle + sweep, color))
            angle += sweep
    y = 62
    for name, size, color in slices:
        out.append(f'<rect x="176" y="{y - 10}" width="11" height="11" rx="3" fill="{color}"/>')
        out.append(text(195, y, name, 12, INK))
        out.append(text(374, y, f"{size / total * 100:.1f}%", 12, MUTED, anchor="end"))
        y += 17
    out.append("</svg>")
    return "\n".join(out)


def render_streak(data: dict) -> str:
    width, height = 400, 150
    days = data["days"]
    if days:
        current, longest = compute_streak(days)
        total = data["contributions"]
        span = f"{days[0][0]} - {days[-1][0]}"
    else:
        current, longest, total, span = "-", "-", "-", ""
    columns = [("当前连续", current, "天"), ("最长连续", longest, "天"), ("近一年贡献", total, "次")]

    out = [svg_open(width, height)]
    for (label, value, unit), x in zip(columns, (width * 0.22, width * 0.5, width * 0.78)):
        out.append(text(x, 54, label, 13, MUTED, anchor="middle", spacing="0.5"))
        out.append(text(x, 96, value, 32, INK, "700", anchor="middle"))
        out.append(text(x, 118, unit, 12.5, MUTED, anchor="middle"))
    for x in (width * 0.36, width * 0.64):
        out.append(f'<line x1="{x:.0f}" y1="40" x2="{x:.0f}" y2="112" stroke="{BORDER}"/>')
    if span:
        out.append(text(width / 2, 136, span, 11, MUTED, anchor="middle"))
    out.append("</svg>")
    return "\n".join(out)


def icon_path(slug: str, x: float, y: float, size: float, color: str) -> str:
    """把 24x24 viewBox 的图标缩放到指定位置与尺寸。"""
    if slug not in ICONS:
        return f'<circle cx="{x + size / 2:.2f}" cy="{y + size / 2:.2f}" r="{size / 2:.2f}" fill="{color}"/>'
    scale = size / 24.0
    return (
        f'<path transform="translate({x:.2f} {y:.2f}) scale({scale:.5f})" '
        f'd="{ICONS[slug]}" fill="{color}"/>'
    )


def render_tech_icons() -> dict[str, str]:
    """每个技术一张小图（图标 + 名字）。图很小，任何视口都放得下，
    所以永远按原始尺寸渲染、不缩放，折行交给 HTML。"""
    icons = {}
    icon, gap, size, pad, height = 18, 7, 13, 9, 34
    for _, items in TECH_STACK:
        for name, slug, color in items:
            width = pad * 2 + icon + gap + len(name) * size * 0.6
            yc = height / 2
            icons[slug] = (
                svg_open(int(round(width)), height)
                + icon_path(slug, pad, yc - icon / 2, icon, color)
                + text(pad + icon + gap, yc + 4.5, name, size, INK)
                + "</svg>"
            )
    return icons


def star_icon(x: float, y: float, size: float) -> str:
    scale = size / 24.0
    return (
        f'<path transform="translate({x:.2f} {y:.2f}) scale({scale:.5f})" '
        f'd="{STAR_PATH}" fill="{ACCENT}"/>'
    )


def text_width_est(value: str, size: float) -> float:
    """等宽字体下估算文字宽度：中日韩 1em，其余约 0.6em。"""
    units = 0.0
    for ch in value:
        units += 1.0 if ord(ch) > 0x2E80 else (0.3 if ch == " " else 0.6)
    return units * size


def fit_text(value: str, size: float, max_width: float) -> str:
    """超过可用宽度就截断，末尾加省略号。"""
    if text_width_est(value, size) <= max_width:
        return value
    kept = ""
    for ch in value:
        if text_width_est(kept + ch + "…", size) > max_width:
            break
        kept += ch
    return kept + "…"


def badge_width(lang: str, size: float = 10.5) -> float:
    return 26 + text_width_est(lang, size)


def lang_badge(right: float, center_y: float, lang: str, color: str, size: float = 10.5) -> str:
    """语言小徽章，右边界对齐到 right，垂直居中于 center_y。"""
    w, h = badge_width(lang, size), 18
    x, y = right - w, center_y - h / 2
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="{h / 2}" '
        f'fill="#8B98A5" fill-opacity="0.18"/>'
        f'<circle cx="{x + 11:.1f}" cy="{center_y:.1f}" r="3.2" fill="{color}"/>'
        + text(x + 18, center_y + 3.8, lang, size, INK)
    )


def render_project_cards(pinned: list[dict]) -> dict[str, str]:
    """每个置顶项目一张小图（400x62）。左右留内边距，并排时两张之间自然有缝；
    桌面一行放得下两张（800px），手机自动变一张。"""
    cards = {}
    width, height, pad = 400, 62, 9
    top, right = 5, width - pad
    for item in pinned:
        name, desc = item["name"], item["desc"]
        lang, color, stars = item["language"], item["color"], item["stars"]
        # 简介的可用宽度要扣掉右侧语言徽章
        desc_max = right - pad - badge_width(lang) - 12
        out = [svg_open(width, height)]
        out.append(text(pad, top + 20, name, 13, INK, "700"))
        out.append(star_icon(right - 30, top + 10, 11))
        out.append(text(right, top + 20, stars, 11.5, ACCENT, "700", anchor="end"))
        out.append(text(pad, top + 42, fit_text(desc, 11, desc_max), 11, MUTED))
        out.append(lang_badge(right, top + 38, lang, color))
        out.append("</svg>")
        cards[name] = "\n".join(out)
    return cards


# 宠物心情：几天没提交就依次变成 Okay / Hungry / Sick
PET_MOODS = [("happy", "Happy"), ("content", "Okay"), ("hungry", "Hungry"), ("sick", "Sick")]


def pet_state(days: list[tuple[str, int]]) -> tuple[str, int, int]:
    """返回 (心情, 等级, 当前连续天数)。"""
    if not days:
        return "content", 1, 0
    counts = [c for _, c in days]
    gap = 0
    for count in reversed(counts):
        if count > 0:
            break
        gap += 1
    if gap <= 1:
        mood = "happy"
    elif gap <= 3:
        mood = "content"
    elif gap <= 7:
        mood = "hungry"
    else:
        mood = "sick"
    streak, _ = compute_streak(days)
    return mood, 1 + sum(counts) // 100, streak


CLAWD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "clawd"
)
# 宠物的名字，显示在卡片上
PET_NAME = "Clawd"
# 心情 -> 使用哪个 Clawd 状态文件
PET_STATES = {
    "happy": "happy",
    "content": "idle-living",
    "hungry": "idle-low-battery",
    "sick": "dizzy",
}


# 四个状态真实边界的并集（Chrome getBBox 量的），再留些余量：
# happy(-7.5,-9.5,29,25.5) / idle-living(0,6,15,10) /
# idle-low-battery(-0.6,0.1,16.2,15.9) / dizzy(0,-0.3,15,16.3)
CLAWD_VIEW = "-13 -16 40 40"


def clawd_svg(state: str, cx: float, cy: float, size: float) -> str:
    """把 assets/clawd 下的状态图嵌进卡片，居中到 (cx, cy)，保留自带动画。"""
    with open(os.path.join(CLAWD_DIR, f"{state}.svg"), encoding="utf-8") as handle:
        raw = handle.read()
    inner = raw[raw.index(">") + 1: raw.rindex("</svg>")]
    return (
        f'<svg x="{cx - size / 2:.1f}" y="{cy - size / 2:.1f}" '
        f'width="{size}" height="{size}" viewBox="{CLAWD_VIEW}">{inner}</svg>'
    )


def render_pet(data: dict) -> str:
    """Clawd 宠物：状态跟着提交活跃度变，全部自托管。"""
    width, height = 340, 112
    mood, level, streak = pet_state(data["days"])
    label = dict(PET_MOODS)[mood]
    total = data["contributions"] if data["contributions"] is not None else 0
    recent = [c for _, c in data["days"]][-7:] if data["days"] else []
    today = recent[-1] if recent else 0

    pet_cx, pet_cy, pet_size = 42.0, 76.0, 76.0
    name_y = pet_cy - pet_size / 2 - 11
    name_w = text_width_est(PET_NAME, 10)
    out = [
        svg_open(width, height),
        # 名字做成小名牌贴在宠物上方
        f'<rect x="{pet_cx - name_w / 2 - 6:.1f}" y="{name_y - 9:.1f}" '
        f'width="{name_w + 12:.1f}" height="18" rx="9" fill="{ACCENT}" fill-opacity="0.12"/>',
        text(pet_cx, name_y + 3.5, PET_NAME, 10, ACCENT, anchor="middle"),
        clawd_svg(PET_STATES[mood], pet_cx, pet_cy, pet_size),
    ]
    tx = 86
    out.append(text(tx, 30, f"Lv {level} · {label}", 13, INK, "700"))
    out.append(text(tx, 58, f"今日 {today} 次 · 连续 {streak} 天", 11, MUTED))
    out.append(text(tx, 80, f"累计 {total} 次贡献", 11, MUTED))
    levels = ["#EBEDF0", "#9EC5FE", "#58A6FF", "#0969DA"]
    for i, count in enumerate(recent):
        shade = levels[0] if count == 0 else levels[min(3, 1 + count // 3)]
        out.append(
            f'<rect x="{tx + i * 13}" y="92" width="10" height="15" rx="2" fill="{shade}"/>'
        )
    out.append("</svg>")
    return "\n".join(out)




README_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "README.md")
TECH_MARKERS = ("<!-- TECH:START -->", "<!-- TECH:END -->")
PROJECT_MARKERS = ("<!-- PROJECTS:START -->", "<!-- PROJECTS:END -->")


def build_tech_html() -> str:
    chunks = []
    for label, items in TECH_STACK:
        icons = " ".join(
            f'<img src="./assets/tech/{slug}.svg" alt="{name}" />' for name, slug, _ in items
        )
        chunks.append(f"<sub>{label}</sub><br/>\n{icons}")
    return "<br/><br/>\n".join(chunks)


def build_projects_html(pinned: list[dict]) -> str:
    return "\n".join(
        f'<img src="./assets/projects/{item["name"]}.svg" alt="{item["name"]}" />'
        for item in pinned
    )


def replace_block(text: str, markers: tuple[str, str], body: str) -> str:
    """把标记之间的内容换成 body。找不到标记就原样返回。"""
    start, end = markers
    i = text.find(start)
    j = text.find(end, i + len(start)) if i != -1 else -1
    if i == -1 or j == -1:
        return text
    return f"{text[: i + len(start)]}\n{body}\n{text[j:]}"


def main() -> int:
    data = None
    if TOKEN:
        try:
            data = normalize(fetch_graphql())
        except Exception as exc:  # noqa: BLE001 - 任何失败都降级到 REST
            print(f"GraphQL 不可用，回退 REST：{exc}", file=sys.stderr)
    if data is None:
        data = fetch_rest()

    os.makedirs(OUT_DIR, exist_ok=True)
    # 整卡类：统计 / 饼图 / 连续天数，它们本身就是图形，没有排版问题
    cards = {
        "stats.svg": render_stats(data),
        "languages.svg": render_pie(data["languages"]),
        "streak.svg": render_streak(data),
        "pet.svg": render_pet(data),
    }
    # 拆件类：技术栈和精选项目拆成一张张小图，由 HTML 负责折行
    groups = {
        "tech": render_tech_icons(),
        "projects": render_project_cards(data["pinned"]),
    }

    for name, content in cards.items():
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        print(f"写入 {path}")
    for folder, items in groups.items():
        directory = os.path.join(OUT_DIR, folder)
        os.makedirs(directory, exist_ok=True)
        # 清掉不再需要的旧图（比如取消置顶的项目），避免留成孤儿
        for stale in sorted(os.listdir(directory)):
            if stale.endswith(".svg") and stale[:-4] not in items:
                os.remove(os.path.join(directory, stale))
                print(f"删除 {directory}/{stale}")
        for key, content in items.items():
            with open(os.path.join(directory, f"{key}.svg"), "w", encoding="utf-8") as handle:
                handle.write(content)
        print(f"写入 {directory}/ 共 {len(items)} 张")

    # README 里技术栈与精选项目的图片清单也一并重写，置顶变了就跟着变
    if os.path.exists(README_PATH):
        with open(README_PATH, encoding="utf-8") as handle:
            readme = handle.read()
        updated = replace_block(readme, TECH_MARKERS, build_tech_html())
        updated = replace_block(updated, PROJECT_MARKERS, build_projects_html(data["pinned"]))
        if updated != readme:
            with open(README_PATH, "w", encoding="utf-8") as handle:
                handle.write(updated)
            print(f"更新 {README_PATH}")
    print(f"生成于 {date.today().isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
