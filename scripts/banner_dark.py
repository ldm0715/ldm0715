#!/usr/bin/env python3
"""给下载下来的 capsule-render 头图/页脚补一份深色配色。

它们自带的波浪是深色半透明，在 GitHub 深色主题下等于看不见。
这里加一个亮色渐变，并用 @media 在深色主题下把波浪的填充换过去。
重复执行不会叠加（用标记判断）。
"""

import pathlib

FILES = ("assets/header.svg", "assets/footer.svg")

LIGHT_GRADIENT = (
    '<linearGradient id="linearDark" x1="0%" y1="0%" x2="100%" y2="0%">'
    '<stop offset="0%" stop-color="#E6EDF3"/>'
    '<stop offset="100%" stop-color="#8B949E"/></linearGradient>'
)
DARK_RULE = (
    '@media (prefers-color-scheme:dark){'
    'path[fill="url(#linear)"]{fill:url(#linearDark)}}'
)
MARK = "linearDark"


def main() -> int:
    for name in FILES:
        path = pathlib.Path(name)
        svg = path.read_text(encoding="utf-8")
        if MARK in svg:
            print(f"{name}: 已有深色配色，跳过")
            continue
        if "</defs>" not in svg or "</style>" not in svg:
            raise SystemExit(f"{name}: 结构不符合预期")
        svg = svg.replace("</defs>", LIGHT_GRADIENT + "</defs>", 1)
        svg = svg.replace("</style>", DARK_RULE + "</style>", 1)
        path.write_text(svg, encoding="utf-8")
        print(f"{name}: 已补深色配色")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
