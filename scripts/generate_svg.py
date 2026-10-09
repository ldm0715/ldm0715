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
import sys
import urllib.request
from datetime import date

API = "https://api.github.com"
USER = os.environ.get("GH_USER", "ldm0715")
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
OUT_DIR = os.environ.get("OUT_DIR", "assets")

# 宣纸主题配色，与博客主题 xuanzhi 保持一致
PAPER = "#FDF6EC"
INK = "#2E4756"
CLAY = "#C86B4A"
BORDER = "#E8D9C5"
MUTED = "#9A8C7A"
FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',"
    "'Microsoft YaHei',Helvetica,Arial,sans-serif"
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
        forkCount
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
        f'viewBox="0 0 {width} {height}" role="img">'
    )


def card_background(width: int, height: int) -> str:
    return (
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="14" '
        f'fill="{PAPER}" stroke="{BORDER}"/>'
    )


def text(x, y, value, size, fill, weight="400", anchor="start", spacing=None) -> str:
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}>'
        f"{html.escape(str(value))}</text>"
    )


def render_stats(data: dict) -> str:
    width, height = 480, 220
    tiles = [
        ("总获星", data["stars"]),
        ("公开仓库", data["repos"]),
        ("关注者", data["followers"]),
        ("近一年贡献", data["contributions"] if data["contributions"] is not None else "-"),
    ]
    out = [svg_open(width, height), card_background(width, height)]
    out.append(text(26, 42, "GitHub 统计", 15, CLAY, "700", spacing="1"))
    for (label, value), (x, y) in zip(tiles, [(26, 70), (250, 70), (26, 146), (250, 146)]):
        out.append(f'<rect x="{x}" y="{y}" width="4" height="44" rx="2" fill="{CLAY}"/>')
        out.append(text(x + 16, y + 24, value, 26, INK, "700"))
        out.append(text(x + 16, y + 46, label, 12.5, MUTED))
    out.append("</svg>")
    return "\n".join(out)


def arc_path(cx, cy, r, start, end, fill) -> str:
    x1 = cx + r * math.cos(math.radians(start))
    y1 = cy + r * math.sin(math.radians(start))
    x2 = cx + r * math.cos(math.radians(end))
    y2 = cy + r * math.sin(math.radians(end))
    large = 1 if (end - start) > 180 else 0
    d = f"M {cx} {cy} L {x1:.2f} {y1:.2f} A {r} {r} 0 {large} 1 {x2:.2f} {y2:.2f} Z"
    return f'<path d="{d}" fill="{fill}" stroke="{PAPER}" stroke-width="1.5"/>'


def render_pie(languages: list[tuple[str, int]]) -> str:
    width, height = 480, 220
    cx, cy, r = 118, 124, 74
    keep = languages[:6]
    rest = sum(size for _, size in languages[6:])
    slices = [(name, size, LANG_COLORS.get(name, PALETTE[i % len(PALETTE)]))
              for i, (name, size) in enumerate(keep)]
    if rest > 0:
        slices.append(("其他", rest, "#C9BCA8"))
    total = sum(size for _, size, _ in slices) or 1

    out = [svg_open(width, height), card_background(width, height)]
    out.append(text(26, 42, "常用语言", 15, CLAY, "700", spacing="1"))
    if len(slices) == 1:
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{slices[0][2]}"/>')
    else:
        angle = -90.0
        for _, size, color in slices:
            sweep = size / total * 360.0
            out.append(arc_path(cx, cy, r, angle, angle + sweep, color))
            angle += sweep
    y = 66
    for name, size, color in slices:
        out.append(f'<rect x="238" y="{y - 11}" width="12" height="12" rx="3" fill="{color}"/>')
        out.append(text(258, y, name, 12.5, INK))
        out.append(text(452, y, f"{size / total * 100:.1f}%", 12.5, MUTED, anchor="end"))
        y += 22
    out.append("</svg>")
    return "\n".join(out)


def render_streak(data: dict) -> str:
    width, height = 620, 150
    days = data["days"]
    if days:
        current, longest = compute_streak(days)
        total = data["contributions"]
        span = f"{days[0][0]} - {days[-1][0]}"
    else:
        current, longest, total, span = "-", "-", "-", ""
    columns = [("当前连续", current, "天"), ("最长连续", longest, "天"), ("近一年贡献", total, "次")]

    out = [svg_open(width, height), card_background(width, height)]
    for (label, value, unit), x in zip(columns, (width * 0.22, width * 0.5, width * 0.78)):
        out.append(text(x, 54, label, 13, MUTED, anchor="middle", spacing="0.5"))
        out.append(text(x, 96, value, 36, INK, "700", anchor="middle"))
        out.append(text(x, 118, unit, 12.5, MUTED, anchor="middle"))
    for x in (width * 0.36, width * 0.64):
        out.append(f'<line x1="{x:.0f}" y1="40" x2="{x:.0f}" y2="112" stroke="{BORDER}"/>')
    if span:
        out.append(text(width / 2, 136, span, 11, MUTED, anchor="middle"))
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
        "streak.svg": render_streak(data),
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
