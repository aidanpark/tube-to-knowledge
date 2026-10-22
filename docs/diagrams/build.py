"""README 도식(diagram-design 양식)을 생성한다. 사용: python docs/diagrams/build.py

각 도식을 밝은 · 어두운 테마의 HTML 로 만든다 (docs/diagrams/<이름>-<테마>.html).
PNG 는 이 HTML 의 <svg> 영역을 브라우저로 캡처해 docs/images/ 에 둔다.
"""
from pathlib import Path

OUT = Path(__file__).parent
FONTS = ("https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600"
         "&family=Geist+Mono:wght@400;500;600&family=Noto+Sans+KR:wght@400;500;600&family=Noto+Serif+KR:wght@400&display=swap")
SANS = "'Geist', 'Noto Sans KR', 'Apple SD Gothic Neo', sans-serif"
MONO = "'Geist Mono', ui-monospace, monospace"

THEMES = {
    "light": dict(paper="#f5f5f5", ink="#2d3142", muted="#4f5d75", soft="#7a8399", accent="#eb6c36",
                  node="#ffffff", node_stroke="#2d3142", accent_tint="rgba(235,108,54,0.08)",
                  user_fill="rgba(79,93,117,0.10)", user_stroke="#7a8399", rule="rgba(45,49,66,0.12)",
                  rule_strong="rgba(45,49,66,0.22)"),
    "dark": dict(paper="#2d3142", ink="#f5f5f5", muted="#bfc0c0", soft="#8e98ac", accent="#f08a59",
                 node="rgba(245,245,245,0.04)", node_stroke="rgba(245,245,245,0.55)", accent_tint="rgba(240,138,89,0.12)",
                 user_fill="rgba(191,192,192,0.10)", user_stroke="#8e98ac", rule="rgba(245,245,245,0.12)",
                 rule_strong="rgba(245,245,245,0.22)"),
}


def text_w(s: str, size: float) -> float:
    """글자 폭 추정: 한글 등 전각 1em, 나머지 0.6em."""
    return sum(size if ord(ch) > 0x2E80 else size * 0.6 for ch in s)


def defs(t) -> str:
    return f"""<defs>
  <marker id="arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{t['muted']}"/></marker>
  <marker id="arrow-accent" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="{t['accent']}"/></marker>
</defs>"""


def node(t, x, y, w, h, name, sub, kind="step", mono_sub=False) -> str:
    fill, stroke = {"step": (t["node"], t["node_stroke"]), "focal": (t["accent_tint"], t["accent"]),
                    "user": (t["user_fill"], t["user_stroke"])}[kind]
    cx = x + w / 2
    sub_font = f'font-family="{MONO}" font-size="10"' if mono_sub else f'font-family="{SANS}" font-size="12"'
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{t["paper"]}"/>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="1"/>'
            f'<text x="{cx}" y="{y + h / 2 - 3}" fill="{t["ink"]}" font-size="14" font-weight="600" font-family="{SANS}" text-anchor="middle">{name}</text>'
            f'<text x="{cx}" y="{y + h / 2 + 16}" fill="{t["muted"]}" {sub_font} text-anchor="middle">{sub}</text>')


def label(t, cx, y_line, s, color=None, above=True) -> str:
    """연결선 위(above) 또는 아래에 6px 간격을 두고 가린 글자를 둔다."""
    w = (int(text_w(s, 12) + 12) + 3) // 4 * 4
    top = y_line - 6 - 16 if above else y_line + 6
    return (f'<rect x="{cx - w / 2}" y="{top}" width="{w}" height="16" rx="2" fill="{t["paper"]}"/>'
            f'<text x="{cx}" y="{top + 12}" fill="{color or t["muted"]}" font-size="12" font-weight="500" font-family="{SANS}" text-anchor="middle">{s}</text>')


def page(t, slug, title, desc, w, h, body) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link href="{FONTS}" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: {t['paper']}; color: {t['ink']}; font-family: {SANS}; padding: 2rem; }}
  .frame {{ max-width: 1280px; margin: 0 auto; }}
  .eyebrow {{ font-family: {MONO}; font-size: 0.66rem; letter-spacing: 0.18em; text-transform: uppercase; color: {t['muted']}; margin-bottom: 0.5rem; }}
  h1 {{ font-family: 'Instrument Serif', 'Noto Serif KR', serif; font-weight: 400; font-size: 2rem; margin-bottom: 1.5rem; }}
  svg {{ width: 100%; display: block; }}
</style>
</head>
<body>
<div class="frame">
<p class="eyebrow">t2k · Diagram Design</p>
<h1>{title}</h1>
<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="{slug}-title {slug}-desc">
<title id="{slug}-title">{title}</title>
<desc id="{slug}-desc">{desc}</desc>
{defs(t)}
<rect width="100%" height="100%" fill="{t['paper']}"/>
{body}
</svg>
</div>
</body>
</html>
"""


def flow(t, x) -> str:
    """링크 하나가 요약 사이트가 되기까지 (가로 다섯 단계)."""
    kinds = [("user", False), ("step", True), ("focal", False), ("step", False), ("step", False)]
    steps = [(name, sub, kind, mono) for (name, sub), (kind, mono) in zip(x["flow_steps"], kinds)]
    w, h, gap, x0, y = 192, 72, 48, 64, 48
    out = []
    for i in range(len(steps) - 1):  # 연결선을 먼저 그린다
        x1 = x0 + i * (w + gap) + w
        out.append(f'<line x1="{x1}" y1="{y + h / 2}" x2="{x1 + gap - 2}" y2="{y + h / 2}" stroke="{t["muted"]}" stroke-width="1.2" marker-end="url(#arrow)"/>')
    # 자동으로 처리하는 구간(2~5단계)을 아래 괄호로 묶는다
    bx1, bx2, by = x0 + (w + gap), x0 + 4 * (w + gap) + w, y + h + 36
    out.append(f'<path d="M {bx1} {by - 8} V {by} H {bx2} V {by - 8}" fill="none" stroke="{t["soft"]}" stroke-width="1"/>')
    out.append(label(t, (bx1 + bx2) / 2, by, x["flow_auto"], color=t["soft"], above=False).replace(
        f'y="{by + 6}"', f'y="{by - 8}"').replace(f'y="{by + 18}"', f'y="{by + 4}"'))
    for i, (name, sub, kind, mono) in enumerate(steps):
        x = x0 + i * (w + gap)
        out.append(f'<text x="{x + w / 2}" y="{y - 14}" fill="{t["accent"] if kind == "focal" else t["soft"]}" font-size="10" '
                   f'font-family="{MONO}" letter-spacing="0.18em" text-anchor="middle">{i + 1:02d}</text>')
        out.append(node(t, x, y, w, h, name, sub, kind, mono))
    return "\n".join(out)


def instruct(t, x) -> str:
    """디스코드 지시로 사이트를 고치는 흐름 (네 줄 스윔레인)."""
    lanes = x["lanes"]
    lx, top, lane_h, right = 40, 24, 84, 1240
    col0, slot, nw, nh = 200, 172, 148, 56
    cx = lambda j: col0 + slot / 2 + j * slot
    ny = lambda k: top + k * lane_h + (lane_h - nh) / 2
    mid = lambda k: top + k * lane_h + lane_h / 2
    out = []
    for k in range(len(lanes)):  # 구분선 · 이름
        y0 = top + k * lane_h
        out.append(f'<line x1="{lx}" y1="{y0}" x2="{right}" y2="{y0}" stroke="{t["rule"]}" stroke-width="1"/>')
        out.append(f'<text x="{lx + 8}" y="{mid(k) + 5}" fill="{t["muted"]}" font-size="13" font-weight="500" font-family="{SANS}">{lanes[k]}</text>')
    out.append(f'<line x1="{lx}" y1="{top + len(lanes) * lane_h}" x2="{right}" y2="{top + len(lanes) * lane_h}" stroke="{t["rule"]}" stroke-width="1"/>')
    out.append(f'<line x1="{col0 - 16}" y1="{top}" x2="{col0 - 16}" y2="{top + len(lanes) * lane_h}" stroke="{t["rule_strong"]}" stroke-width="1"/>')

    nodes = [  # (줄, 열, 이름, 설명, 종류)
        (k, j, name, sub, kind) for (k, j, kind), (name, sub) in zip(
            [(0, 0, "user"), (1, 1, "step"), (2, 2, "step"), (0, 3, "user"), (1, 4, "step"), (3, 5, "focal")], x["nodes"])
    ]

    def elbow(a, b, color="muted", marker="arrow", text=None, text_dx=0):
        (k1, j1), (k2, j2) = a, b
        x1, y1 = cx(j1) + nw / 2, mid(k1)
        x2 = cx(j2)
        down = k2 > k1
        y2 = ny(k2) if down else ny(k2) + nh
        s = 1 if down else -1
        d = f"M {x1} {y1} H {x2 - 8} Q {x2} {y1} {x2} {y1 + 8 * s} V {y2}"
        c = t[color]
        out.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="{1.4 if color == "accent" else 1.2}" marker-end="url(#{marker})"/>')
        if text:
            out.append(label(t, (x1 + x2) / 2 + text_dx, y1, text, color=c if color == "accent" else None))

    elbow((0, 0), (1, 1))
    elbow((1, 1), (2, 2))
    elbow((2, 2), (0, 3), text=x["arrows"][0])
    elbow((0, 3), (1, 4), text=x["arrows"][1])
    elbow((1, 4), (3, 5), color="accent", marker="arrow-accent", text=x["arrows"][2])
    for k, j, name, sub, kind in nodes:
        out.append(node(t, cx(j) - nw / 2, ny(k), nw, nh, name, sub, kind))
    return "\n".join(out)


# 언어별 문구. 영어는 상자 너비(처리 흐름 192px, 지시 흐름 148px)에 들어가도록 짧게 쓴다
TEXT = {
    "ko": {
        "flow": ("링크 하나가 요약 사이트가 되기까지",
                 "디스코드에 올린 YouTube 링크가 자막 추출, Claude 요약과 분류, 사이트 배포, 디스코드 알림을 거쳐 정리되는 다섯 단계."),
        "flow_steps": [("링크 올리기", "디스코드 채널에 붙여 넣기"), ("자막 추출", "yt-dlp"), ("요약 · 분류", "내 관심사와 카테고리로"),
                       ("사이트 배포", "로그인해야 열리는 사이트"), ("디스코드 알림", "✅ 요약 주소로 답장")],
        "flow_auto": "여기부터 자동 · 1분 주기",
        "instruct": ("채팅 한 줄로 사이트 고치기",
                     "채팅 UI 로 보낸 지시를 t2k 가 받아 사용자의 모델이 그 사람의 파일만 고치고, 승인을 받은 뒤 데이터에 커밋하고(백업 원격이 있으면 푸시) 그 사람의 사이트만 다시 배포하는 흐름."),
        "lanes": ["채팅 UI", "t2k", "내 모델", "내 사이트"],
        "nodes": [("지시 보내기", "“글씨를 더 크게 해 줘”"), ("지시 접수", "👀 표시 · 바로 처리 시작"), ("내 파일만 수정", "격리된 작업 공간에서"),
                  ("승인 또는 취소", "취소하면 바로 버린다"), ("내 데이터에 저장", "커밋 · 푸시(선택)"), ("다시 배포", "바로 새 모습으로")],
        "arrows": ["승인 요청", "승인", "그 사람 사이트만"],
    },
    "en": {
        "flow": ("From one link to a summary page",
                 "Five steps: a YouTube link shared to Discord gets its captions, a Claude summary and category, a page on the private site, and a Discord reply."),
        "flow_steps": [("Share a link", "Paste into your channel"), ("Get captions", "yt-dlp"), ("Summarize & file", "Using your interests"),
                       ("Publish", "To your private site"), ("Notify", "✅ Reply with the link")],
        "flow_auto": "Automatic from here · every minute",
        "instruct": ("Change your site with one chat message",
                     "A request sent from the chat UI is picked up by t2k; your model edits only that person's files, and after approval the change is committed to their data (pushed only if a backup remote is set) and only their site is redeployed."),
        "lanes": ["Chat UI", "t2k", "Your model", "Your site"],
        "nodes": [("Send a request", "“Make text bigger”"), ("Pick it up", "👀 right away"), ("Edit your files", "In a sandbox"),
                  ("Approve or cancel", "Cancel discards it"), ("Save your data", "Commit · optional push"), ("Redeploy", "Live right away")],
        "arrows": ["Approve?", "Approve", "Their site"],
    },
}
DIAGRAMS = {"flow": (1280, 196, flow), "instruct": (1280, 384, instruct)}

if __name__ == "__main__":
    for lang, x in TEXT.items():
        for name, (w, h, fn) in DIAGRAMS.items():
            title, desc = x[name]
            for theme, t in THEMES.items():
                f = OUT / f"{name}-{lang}-{theme}.html"
                f.write_text(page(t, f"{name}-{lang}-{theme}", title, desc, w, h, fn(t, x)).replace('<html lang="ko">', f'<html lang="{lang}">'))
            print(f)
