#!/usr/bin/env python3
"""给 snk 生成的贪吃蛇加一条深色主题覆盖，输出到 assets/snake.svg。

snk 的输出把颜色放在 CSS 变量里（--cs 是蛇、--c0~--c4 是贡献格子），
所以只要在同一个 <style> 里补一段 @media 就能跟着系统主题切换，
不需要维护亮/暗两份图。
"""

import pathlib

SRC = pathlib.Path("dist/github-snake.svg")
DST = pathlib.Path("assets/snake.svg")

DARK = (
    "@media (prefers-color-scheme:dark){:root{"
    "--cs:#58A6FF;--ce:#21262D;--c0:#21262D;--c1:#30363D;"
    "--c2:#484F58;--c3:#6E7681;--c4:#8B949E}}"
)


def main() -> int:
    svg = SRC.read_text(encoding="utf-8")
    if "</style>" not in svg:
        raise SystemExit("蛇图里没有 <style>，snk 的输出格式可能变了")
    svg = svg.replace("</style>", DARK + "</style>", 1)
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(svg, encoding="utf-8")
    print(f"写入 {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
