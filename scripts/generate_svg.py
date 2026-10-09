#!/usr/bin/env python3
"""抓取 GitHub 数据并渲染成静态 SVG。

极简风格：不画卡片底色、边框和圆角，内容直接排在页面上，只用字重、留白
和细分割线分层。文字颜色通过内嵌的 prefers-color-scheme 规则跟随明暗主题。
产物写到 assets/ 目录，由 GitHub Action 定时刷新。仅使用标准库。
"""

from __future__ import annotations

import html
import json
import math
import os
import sys
import urllib.request
from datetime import date

API = "https://api.github.com"
USER = os.environ.get("GH_USER", "ldm0715")
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
OUT_DIR = os.environ.get("OUT_DIR", "assets")

FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',"
    "'Microsoft YaHei',Helvetica,Arial,sans-serif"
)

# 无底色，只有文字与细线；暗色规则内嵌，跟随系统主题
STYLE = (
    "<style>"
    ".ink{fill:#22333D}.muted{fill:#6B7B87}.accent{fill:#B85C38}"
    ".rule{stroke:#E3DACD;stroke-width:1}"
    "@media (prefers-color-scheme:dark){"
    ".ink{fill:#D6DEE6}.muted{fill:#8B98A5}.accent{fill:#E0916A}"
    ".rule{stroke:#30363D}}"
    "</style>"
)

# 语言色点缺失时的中性色
NEUTRAL = "#8B98A5"

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

# 技术栈内容，格式为 (显示名, 图标 slug, 品牌色)
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

# 精选项目，格式为 (仓库名, 一句话简介)；语言与 star 数由 API 实时提供
FEATURED = [
    ("xuanzhi", "宣纸风格 Hugo 博客主题"),
    ("fangclass_check_web", "方班研讨厅提问查询工具"),
    ("emobox", "本地表情包管理器"),
    ("anyswitch", "直连 IP 自动写入 hosts"),
    ("bowen_music", "波点音乐第三方桌面客户端"),
    ("HYB_farm_helper", "黑与白农场油猴助手脚本"),
]

# Material 星形图标路径（24x24 viewBox）
STAR_PATH = "M12 17.27L18.18 21l-1.64-7.03L22 9.24l-7.19-.61L12 2 9.19 8.63 2 9.24l5.46 4.73L5.82 21z"

GRAPHQL_QUERY = """
query ($login: String!) {
  user(login: $login) {
    followers { totalCount }
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
        "repos_meta": {
            r["name"]: {
                "stars": r["stargazers_count"],
                "language": r["language"] or "-",
                "color": LANG_COLORS.get(r["language"], NEUTRAL),
            }
            for r in repos
        },
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
    return {
        "repos": user["repositories"]["totalCount"],
        "stars": sum(n["stargazerCount"] for n in nodes),
        "followers": user["followers"]["totalCount"],
        "contributions": calendar["totalContributions"],
        "days": days,
        "languages": sorted(langs.items(), key=lambda kv: kv[1], reverse=True),
        "repos_meta": {
            n["name"]: {
                "stars": n["stargazerCount"],
                "language": (n["primaryLanguage"] or {}).get("name", "-"),
                "color": (n["primaryLanguage"] or {}).get("color") or NEUTRAL,
            }
            for n in nodes
        },
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
        f'viewBox="0 0 {width} {height}" role="img">{STYLE}'
    )


def text(x, y, value, size, cls="ink", weight="400", anchor="start", spacing=None) -> str:
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'<text x="{x}" y="{y}" class="{cls}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="{anchor}"{extra}>{html.escape(str(value))}</text>'
    )


def text_width(value: str, size: float, bold: bool = False) -> float:
    """按字符类别估算文本宽度（em 为单位的经验系数），用于排版定位。"""
    units = 0.0
    for ch in value:
        if ord(ch) > 0x2E80:      # 中日韩全角
            units += 1.0
        elif ch.isupper():
            units += 0.68
        elif ch.islower():
            units += 0.53
        elif ch.isdigit():
            units += 0.57
        elif ch == " ":
            units += 0.28
        elif ch in ".,:;'|!|":
            units += 0.27
        else:
            units += 0.60
    return units * size * (1.04 if bold else 1.0)


def rule(y, x1, x2, cls="rule") -> str:
    return f'<line class="{cls}" x1="{x1}" y1="{y}" x2="{x2}" y2="{y}"/>'


def icon_path(slug: str, x: float, y: float, size: float, color: str) -> str:
    """把 24x24 viewBox 的图标缩放到指定位置与尺寸，用品牌色绘制。"""
    if slug not in ICONS:
        return (
            f'<circle cx="{x + size / 2:.2f}" cy="{y + size / 2:.2f}" '
            f'r="{size / 2:.2f}" fill="{color}"/>'
        )
    scale = size / 24.0
    return (
        f'<path transform="translate({x:.2f} {y:.2f}) scale({scale:.5f})" '
        f'd="{ICONS[slug]}" fill="{color}"/>'
    )


def star_icon(x: float, y: float, size: float) -> str:
    scale = size / 24.0
    return (
        f'<path class="accent" transform="translate({x:.2f} {y:.2f}) '
        f'scale({scale:.5f})" d="{STAR_PATH}"/>'
    )


def render_stats(data: dict) -> str:
    """六项指标，三列两行，无边框。"""
    width, height = 400, 176
    days = data["days"]
    current, longest = compute_streak(days) if days else ("-", "-")
    metrics = [
        ("总获星", data["stars"]),
        ("公开仓库", data["repos"]),
        ("关注者", data["followers"]),
        ("近一年贡献", data["contributions"] if data["contributions"] is not None else "-"),
        ("连续天数", current),
        ("最长连续", longest),
    ]
    out = [svg_open(width, height)]
    for index, (label, value) in enumerate(metrics):
        cx = 66 + (index % 3) * 134
        y = 52 + (index // 3) * 74
        out.append(text(cx, y, value, 28, "ink", "700", anchor="middle"))
        out.append(text(cx, y + 24, label, 11.5, "muted", anchor="middle"))
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
    """语言饼图 + 图例，无边框。高度与统计卡一致，方便并排。"""
    width, height = 400, 176
    cx, cy, r = 96, 88, 54
    keep = languages[:6]
    rest = sum(size for _, size in languages[6:])
    slices = [(name, size, LANG_COLORS.get(name, PALETTE[i % len(PALETTE)]))
              for i, (name, size) in enumerate(keep)]
    if rest > 0:
        slices.append(("其他", rest, NEUTRAL))
    total = sum(size for _, size, _ in slices) or 1

    out = [svg_open(width, height)]
    if len(slices) == 1:
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{slices[0][2]}"/>')
    else:
        angle = -90.0
        for _, size, color in slices:
            sweep = size / total * 360.0
            out.append(arc_path(cx, cy, r, angle, angle + sweep, color))
            angle += sweep
    y = 44
    for name, size, color in slices:
        out.append(
            f'<rect x="170" y="{y - 9}" width="10" height="10" rx="2.5" fill="{color}"/>'
        )
        out.append(text(188, y, name, 12, "ink"))
        out.append(text(376, y, f"{size / total * 100:.1f}%", 12, "muted", anchor="end"))
        y += 18
    out.append("</svg>")
    return "\n".join(out)


def render_tech_stack() -> str:
    """窄版，400px 宽，手机上缩放后文字仍可读。"""
    width, height, pad = 400, 242, 0
    icon_size, gap, row_gap = 16, 14, 26
    out = [svg_open(width, height)]
    label_y = 18
    for index, (label, items) in enumerate(TECH_STACK):
        if index:
            out.append(rule(label_y - 22, pad, width - pad))
        out.append(text(pad, label_y, label, 11.5, "muted"))
        x, row_y = float(pad), label_y + 24
        for name, slug, color in items:
            item_w = icon_size + 5 + text_width(name, 12)
            if x + item_w > width - pad:
                x, row_y = float(pad), row_y + row_gap
            out.append(icon_path(slug, x, row_y - icon_size / 2, icon_size, color))
            out.append(text(x + icon_size + 5, row_y + 4, name, 12, "ink"))
            x += item_w + gap
        label_y = row_y + 40
    out.append("</svg>")
    return "\n".join(out)


def render_tech_stack_wide() -> str:
    """宽版，820px，一行一组。"""
    width, height = 820, 128
    icon_size, label_w = 18, 92
    out = [svg_open(width, height)]
    for row, (label, items) in enumerate(TECH_STACK):
        yc = 22 + row * 42
        out.append(text(0, yc + 4.5, label, 12, "muted"))
        x = float(label_w)
        for name, slug, color in items:
            out.append(icon_path(slug, x, yc - icon_size / 2, icon_size, color))
            out.append(text(x + icon_size + 6, yc + 4.5, name, 12.5, "ink"))
            x += icon_size + 6 + text_width(name, 12.5) + 22
    out.append("</svg>")
    return "\n".join(out)


def badge_width(lang: str, size: float = 10.5) -> float:
    return 21 + text_width(lang, size) + 8


def lang_badge(left: float, center_y: float, lang: str, color: str, size: float = 10.5) -> str:
    """语言小徽章，左边界为 left，垂直居中于 center_y。底色是语言本色的低透明度，
    因此在明暗两种主题下都成立。"""
    w, h = badge_width(lang, size), 19
    y = center_y - h / 2
    return (
        f'<rect x="{left:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h}" rx="{h / 2}" '
        f'fill="{color}" fill-opacity="0.16"/>'
        f'<circle cx="{left + 13:.1f}" cy="{center_y:.1f}" r="3.5" fill="{color}"/>'
        + text(left + 21, center_y + 3.8, lang, size, "ink")
    )


def repo_meta(meta: dict, repo: str) -> tuple[str, str, int]:
    entry = meta.get(repo, {})
    return entry.get("language") or "-", entry.get("color") or NEUTRAL, entry.get("stars", 0)


def render_projects(meta: dict) -> str:
    """窄版，400px 宽，单列 + 细分割线。"""
    width, pad, entry_h = 400, 0, 58
    height = 20 + len(FEATURED) * entry_h
    out = [svg_open(width, height)]
    for index, (repo, desc) in enumerate(FEATURED):
        y = 20 + index * entry_h
        lang, color, stars = repo_meta(meta, repo)
        if index:
            out.append(rule(y - 6, pad, width - pad))
        out.append(text(pad, y + 16, repo, 13, "ink", "700"))
        badge_x = pad + text_width(repo, 13, bold=True) + 12
        out.append(lang_badge(badge_x, y + 11.5, lang, color))
        star_x = badge_x + badge_width(lang) + 16
        out.append(star_icon(star_x, y + 6, 11))
        out.append(text(star_x + 15, y + 16, stars, 11.5, "accent", "700"))
        out.append(text(pad, y + 38, desc, 11, "muted"))
    out.append("</svg>")
    return "\n".join(out)


def render_projects_wide(meta: dict) -> str:
    """宽版，820px，两列三行，行间细线。"""
    width, col_w, row_h = 820, 400, 58
    height = 20 + 2 * row_h + 58
    out = [svg_open(width, height)]
    for index, (repo, desc) in enumerate(FEATURED):
        x = (index % 2) * (col_w + 20)
        y = 20 + (index // 2) * row_h
        lang, color, stars = repo_meta(meta, repo)
        if index >= 2 and index % 2 == 0:
            out.append(rule(y - 6, 0, width))
        out.append(text(x, y + 16, repo, 13, "ink", "700"))
        badge_x = x + text_width(repo, 13, bold=True) + 12
        out.append(lang_badge(badge_x, y + 11.5, lang, color))
        star_x = badge_x + badge_width(lang) + 16
        out.append(star_icon(star_x, y + 6, 11))
        out.append(text(star_x + 15, y + 16, stars, 11.5, "accent", "700"))
        out.append(text(x, y + 38, desc, 11, "muted"))
    out.append("</svg>")
    return "\n".join(out)


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
    cards = {
        "stats.svg": render_stats(data),
        "languages.svg": render_pie(data["languages"]),
        "tech-stack.svg": render_tech_stack(),
        "tech-stack-wide.svg": render_tech_stack_wide(),
        "projects.svg": render_projects(data["repos_meta"]),
        "projects-wide.svg": render_projects_wide(data["repos_meta"]),
    }
    for name, content in cards.items():
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        print(f"写入 {path}")
    print(f"生成于 {date.today().isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
