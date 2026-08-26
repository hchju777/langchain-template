"""메모용 아키텍처 다이어그램 생성기.

SVG를 그린 뒤 headless Chrome으로 2배 해상도 PNG까지 만든다. 창 크기는
각 SVG의 실제 크기에서 그대로 가져오므로 손으로 맞출 일이 없다.

    python3 docs/superpowers/tools/gen_diagrams.py
"""
from pathlib import Path

from _common import IMAGES as OUT, rel, run_chrome, warn_no_chrome

OUT.mkdir(parents=True, exist_ok=True)

#: 파일명 → (너비, 높이). PNG 렌더링 창 크기로 쓴다.
SIZES: dict[str, tuple[int, int]] = {}


def save(name: str, svg: str, w: int, h: int) -> None:
    (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
    SIZES[name] = (w, h)

FONT = "'Noto Sans CJK KR','Noto Sans KR',sans-serif"

INK      = "#1f2933"
MUTED    = "#5c6b7a"
LINE     = "#94a3b8"
BG_BASE  = "#eef2f7"
BD_BASE  = "#94a3b8"
BG_LLM   = "#e7edfb"
BD_LLM   = "#6b8fd6"
BG_NEW   = "#e3f3e8"
BD_NEW   = "#5aa172"
BG_FUT   = "#fbf1dd"
BD_FUT   = "#c9a227"
BG_INFRA = "#f3f0f7"
BD_INFRA = "#9c8ab8"
BG_GUARD = "#fdeaea"
BD_GUARD = "#c86b6b"


def header(w, h):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="{FONT}">
<defs>
  <marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{LINE}"/></marker>
  <marker id="ag" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{BD_NEW}"/></marker>
  <marker id="al" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{BD_LLM}"/></marker>
  <marker id="ap" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{BD_INFRA}"/></marker>
  <marker id="af" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{BD_FUT}"/></marker>
  <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{BD_GUARD}"/></marker>
</defs>
<rect width="{w}" height="{h}" fill="#ffffff"/>
'''


def box(x, y, w, h, title, sub=None, bg=BG_BASE, bd=BD_BASE, ts=15, ss=11.5, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    s = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="{bg}" '
         f'stroke="{bd}" stroke-width="1.5"{d}/>\n')
    if sub:
        lines = sub if isinstance(sub, list) else [sub]
        top = y + h / 2 - 5 - (len(lines) - 1) * 6
        s += (f'<text x="{x+w/2}" y="{top}" text-anchor="middle" font-size="{ts}" '
              f'font-weight="600" fill="{INK}">{title}</text>\n')
        for i, ln in enumerate(lines):
            s += (f'<text x="{x+w/2}" y="{top+18+i*14}" text-anchor="middle" '
                  f'font-size="{ss}" fill="{MUTED}">{ln}</text>\n')
    else:
        s += (f'<text x="{x+w/2}" y="{y+h/2+5}" text-anchor="middle" font-size="{ts}" '
              f'font-weight="600" fill="{INK}">{title}</text>\n')
    return s


def label(x, y, t, size=12.5, fill=MUTED, anchor="middle", weight="400"):
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}">{t}</text>\n')


def poly(pts, color=LINE, dash=None, marker="a", w=1.6):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    m = f' marker-end="url(#{marker})"' if marker else ""
    p = " L ".join(f"{x} {y}" for x, y in pts)
    return f'<path d="M {p}" fill="none" stroke="{color}" stroke-width="{w}"{m}{d}/>\n'


def arrow(x1, y1, x2, y2, color=LINE, dash=None, marker="a", w=1.6):
    return poly([(x1, y1), (x2, y2)], color, dash, marker, w)


def group(x, y, w, h, title, bd=LINE, dash="6 4", tf=MUTED):
    s = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="none" '
         f'stroke="{bd}" stroke-width="1.3" stroke-dasharray="{dash}"/>\n')
    if title:
        s += (f'<text x="{x+14}" y="{y+21}" font-size="12.5" font-weight="600" '
              f'fill="{tf}">{title}</text>\n')
    return s


def legend(x, y, items, gap=160):
    s = ""
    for i, (bg, bd, txt) in enumerate(items):
        cx = x + i * gap
        s += (f'<rect x="{cx}" y="{y}" width="16" height="13" rx="3" fill="{bg}" '
              f'stroke="{bd}" stroke-width="1.4"/>\n')
        s += label(cx + 23, y + 11, txt, 12, MUTED, "start")
    return s


# ══════════════════════════════════════════════════════════
# 그림 1 — 현재 구조
# ══════════════════════════════════════════════════════════
W, H = 1200, 646
s = header(W, H)
s += label(W / 2, 34, "그림 1. 현재 구조", 19, INK, "middle", "700")
s += label(W / 2, 57, "실행 경로와 돌아가는 분석이 config로 고정되어 있다", 13, MUTED)

s += box(40, 90, 180, 46, "CLI", "사람이 명령", ts=14, ss=11)
s += box(240, 90, 180, 46, "스케줄러", "시간이 트리거", ts=14, ss=11)
s += arrow(130, 136, 200, 152)
s += arrow(330, 136, 270, 152)
s += box(120, 156, 230, 50, "BaseContext", "as_of 확정 · 밖에서 주입", ts=14, ss=11)
s += arrow(235, 206, 235, 220)
s += box(120, 222, 230, 44, "RunLock", "중복 실행 방지", ts=14, ss=11)
s += arrow(235, 266, 235, 280)
s += box(160, 282, 150, 42, "START", bg="#dde5ef", ts=14)

subs = ["health.connectivity", "kafka.lag", "line.equipment",
        "kpi.check", "alarm.trend", "material.stock"]
s += group(452, 96, 268, 310, "항상 전체 실행 (config enabled)")
for i, n in enumerate(subs):
    y = 118 + i * 47
    bg, bd = (BG_LLM, BD_LLM) if n == "kpi.check" else (BG_BASE, BD_BASE)
    s += box(470, y, 232, 38, n, bg=bg, bd=bd, ts=13.5)
    s += poly([(310, 303), (420, 303), (420, y + 19), (470, y + 19)])
    s += poly([(702, y + 19), (752, y + 19), (752, 303), (790, 303)])

s += box(790, 278, 130, 50, "aggregate", "코드 집계 + LLM 서술",
         bg=BG_LLM, bd=BD_LLM, ts=14, ss=10.5)
s += arrow(920, 303, 946, 303)
s += box(946, 278, 110, 50, "render", "md 생성", ts=14, ss=11)
s += arrow(1056, 303, 1082, 303)
s += box(1082, 278, 100, 50, "deliver", "멱등키", ts=14, ss=11)

s += group(40, 424, 380, 160, "서브그래프 내부 — 4슬롯 (공통)")
s += box(56, 452, 104, 40, "validate", ts=13)
s += arrow(160, 472, 180, 472)
s += box(180, 452, 104, 40, "process", ts=13, bg="#dde5ef")
s += arrow(284, 472, 304, 472)
s += box(304, 452, 100, 40, "output", ts=13)
s += poly([(108, 492), (108, 524), (228, 524)], color=BD_GUARD)
s += poly([(232, 492), (232, 524)], color=BD_GUARD, marker=None)
s += poly([(354, 492), (354, 524), (256, 524)], color=BD_GUARD)
s += box(176, 528, 130, 34, "handle_error", ts=12, bg=BG_GUARD, bd=BD_GUARD)
s += label(318, 550, "부분 실패 격리", 11.5, BD_GUARD, "start")

s += group(440, 424, 742, 160, "코드가 고정하는 세 가지")
s += box(460, 450, 212, 46, "데이터 조회 경로", "kind → 어댑터 (config ports)",
         bg=BG_INFRA, bd=BD_INFRA, ts=13, ss=10.5)
s += box(692, 450, 202, 46, "숫자 계산", "Metric 값은 코드가 산출",
         bg=BG_INFRA, bd=BD_INFRA, ts=13, ss=10.5)
s += box(914, 450, 250, 46, "근거 대조 (가드레일)", "evidence ID 전수 검사",
         bg=BG_GUARD, bd=BD_GUARD, ts=13, ss=10.5)
s += label(460, 526, "판정 자체는 이미 코드(임계치)와 LLM(judge)이 함께 담당한다. "
                     "위 세 가지만 코드 전용이다.", 12.3, MUTED, "start")
s += label(460, 552, "한계 — 질의를 받을 입구가 없고, 돌아가는 분석이 고정이며, "
                     "process 안에서 분기할 수 없다.", 12.3, "#b45309", "start")

s += legend(40, 606, [(BG_LLM, BD_LLM, "LLM 사용"), (BG_GUARD, BD_GUARD, "가드레일"),
                      (BG_INFRA, BD_INFRA, "코드 전용")])
s += "</svg>"
save("arch-current", s, W, H)

# ══════════════════════════════════════════════════════════
# 그림 2 — 최종 구조
# ══════════════════════════════════════════════════════════
W, H = 1240, 842
s = header(W, H)
s += label(W / 2, 34, "그림 2. 최종 구조", 19, INK, "middle", "700")
s += label(W / 2, 57, "질의가 초점과 범위를 정하고, 검증은 코드가 유지하며, "
                      "자율 판단은 사람이 실시간 확인하는 경로로 분리한다", 13, MUTED)

s += box(60, 88, 190, 50, "스케줄러", "질의 없음", ts=14, ss=11)
s += box(280, 88, 210, 50, "CLI --query \"...\"", "사람이 질의",
         bg=BG_NEW, bd=BD_NEW, ts=14, ss=11)
s += arrow(155, 138, 230, 152)
s += arrow(385, 138, 350, 152)
s += box(140, 156, 300, 52, "BaseContext", "as_of + query (thread_id · 멱등키에 반영)",
         bg=BG_NEW, bd=BD_NEW, ts=14, ss=11)
s += arrow(290, 208, 290, 222)
s += box(116, 224, 348, 60, "analyze_query",
         "질의 → Requirement{초점, 대상 분석, 사유}", bg=BG_LLM, bd=BD_LLM, ts=15, ss=11.5)
s += box(116, 290, 348, 34, "가드레일 — 대상 ⊆ config enabled, 위반 시 전체 실행",
         bg=BG_GUARD, bd=BD_GUARD, ts=11.8)
s += label(290, 350, "질의가 없으면 이 단계를 건너뛰고 지금과 동일하게 전체 실행", 11.5, MUTED)

s += arrow(464, 254, 520, 254)
s += label(492, 244, "조건부", 11, MUTED)
s += group(520, 140, 268, 296, "선택된 분석만 병렬 실행", bd=BD_NEW, tf=BD_NEW)
sel = [("kpi.check", 1), ("line.equipment", 1), ("material.stock", 1),
       ("alarm.trend", 0), ("kafka.lag", 0)]
for i, (n, on) in enumerate(sel):
    y = 176 + i * 48
    if on:
        s += box(538, y, 232, 38, n, bg=BG_LLM, bd=BD_LLM, ts=13)
    else:
        s += box(538, y, 232, 38, n, bg="#f7f8fa", bd="#cbd5e1", ts=13, dash="4 3")
s += label(654, 424, "선택 안 된 분석은 실행되지 않는다", 11.5, MUTED)

s += arrow(788, 256, 836, 256)
s += box(836, 228, 150, 56, "aggregate", "집계는 코드, 해석은 LLM",
         bg=BG_LLM, bd=BD_LLM, ts=14, ss=10.5)
s += arrow(911, 284, 911, 304)
s += box(814, 306, 194, 40, "근거 대조 (확대)", bg=BG_GUARD, bd=BD_GUARD, ts=12.5)
s += label(911, 364, "실행 중 추가 조회분까지 포함", 11.5, BD_GUARD)
s += arrow(911, 346, 911, 382)
s += box(836, 384, 150, 44, "render", ts=14)
s += arrow(986, 406, 1024, 406)
s += box(1024, 384, 150, 44, "deliver", ts=14)

s += box(1022, 140, 200, 48, "config 고정 문서", "SOP · KPI 정의서",
         bg=BG_INFRA, bd=BD_INFRA, ts=13, ss=10.5)
s += box(1022, 196, 200, 48, "실행 시 첨부 문서", "정비 계획 · 회의록",
         bg=BG_INFRA, bd=BD_INFRA, ts=13, ss=10.5)
s += box(1022, 252, 200, 48, "RAG 어댑터", "향후 — 포트 교체만",
         bg=BG_FUT, bd=BD_FUT, ts=13, ss=10.5, dash="5 3")
s += poly([(1022, 164), (1004, 164), (1004, 248), (986, 248)], color=BD_INFRA, marker="ap")
s += poly([(1022, 220), (1004, 220), (1004, 256), (986, 256)], color=BD_INFRA, marker="ap")
s += poly([(1022, 276), (1004, 276), (1004, 266), (986, 266)],
          color=BD_FUT, marker="af", dash="5 3")
s += label(1122, 322, "ReferencePort — 인용 가능한 ID 부여", 11.5, MUTED)

s += box(60, 470, 1114, 46,
         "공용 데이터 어댑터 — Redis / MongoDB / Kafka / REST",
         bg=BG_INFRA, bd=BD_INFRA, ts=13.5)
s += poly([(654, 436), (654, 468)], color=BD_INFRA, dash="4 3", marker=None)
s += poly([(415, 578), (415, 518)], color=BD_INFRA, dash="4 3", marker=None)
s += label(1186, 496, "공유", 11.5, MUTED, "start")

s += group(60, 552, 900, 130, "별도 경로 — 사람이 실시간으로 검증", bd=BD_NEW, tf=BD_NEW)
s += box(82, 582, 190, 60, "온디맨드 질의", "사람이 대화형으로",
         bg=BG_NEW, bd=BD_NEW, ts=14, ss=11)
s += arrow(272, 612, 300, 612)
s += box(300, 582, 230, 60, "질의 에이전트", "자유 tool-calling 허용",
         bg=BG_FUT, bd=BD_FUT, ts=14, ss=11)
s += arrow(530, 612, 558, 612)
s += box(558, 582, 160, 60, "사람이 확인", "되묻고 걸러냄",
         bg=BG_NEW, bd=BD_NEW, ts=13.5, ss=11)
s += label(742, 604, "재현성 · 무인 안정성 요구가", 11.5, MUTED, "start")
s += label(742, 623, "적용되지 않는 영역", 11.5, MUTED, "start")

s += group(60, 700, 1114, 116, "이 구조가 유지하는 것과 새로 얻는 것")
s += legend(856, 708, [(BG_NEW, BD_NEW, "신규 · 변경"), (BG_FUT, BD_FUT, "향후 확장")], 150)
s += label(82, 748, "유지 — 같은 (as_of, 질의)면 같은 결과 · 분석 단위 실패 격리 · "
                    "근거 전수 대조 · replay · 부팅 시 config 검증", 12.5, INK, "start")
s += label(82, 774, "확보 — 질의에 따른 초점과 범위 조정 · 참고 문서 반영 · "
                    "분석 내부의 조건 분기 · 사람이 검증하는 자율 조회 경로", 12.5, BD_NEW, "start")
s += label(82, 800, "향후 — ReferencePort를 RAG 어댑터로 교체 · "
                    "decide_next를 tool-calling으로 교체 (교체 지점이 각각 한 곳에 모여 있다)",
           12.5, "#8a6d1f", "start")
s += "</svg>"
save("arch-target", s, W, H)

# ══════════════════════════════════════════════════════════
# 그림 3 — 서브그래프 내부
# ══════════════════════════════════════════════════════════
W, H = 1200, 476
s = header(W, H)
s += label(W / 2, 34, "그림 3. 서브그래프 내부 — process의 확장", 19, INK, "middle", "700")
s += label(W / 2, 57, "단계를 나눠 교체 가능하게 하고, 분기 판단만 LLM이 맡는다", 13, MUTED)

s += group(40, 84, 520, 322, "현재 — process는 메서드 하나")
s += box(66, 144, 104, 44, "validate", ts=13)
s += arrow(170, 166, 194, 166)
s += box(194, 132, 216, 68, "process",
         ["fetch · compute · judge가", "한 함수 안에 있다"], bg="#dde5ef", ts=14, ss=11)
s += arrow(410, 166, 434, 166)
s += box(434, 144, 100, 44, "output", ts=13)
s += label(66, 236, "· 단계별로 갈아끼울 수 없다", 12.5, MUTED, "start")
s += label(66, 262, "· 중간에 조건 분기를 넣을 자리가 없다", 12.5, MUTED, "start")
s += label(66, 288, "· 실패하면 fetch부터 다시 시작한다", 12.5, MUTED, "start")
s += label(66, 314, "· 체크포인트가 process 통째로 하나다", 12.5, MUTED, "start")
s += label(66, 360, "판정에 LLM을 쓰는 경로는 이미 있다", 12.5, INK, "start")
s += label(66, 382, "(kpi.check의 use_llm_judge)", 12, MUTED, "start")

s += arrow(568, 244, 618, 244, color=BD_NEW, marker="ag", w=2.2)

s += group(640, 84, 520, 322, "최종 — process는 중첩 그래프", bd=BD_NEW, tf=BD_NEW)
s += box(658, 144, 88, 40, "validate", ts=12.5)
s += arrow(746, 164, 764, 164)
s += group(762, 118, 312, 176, "process (중첩 StateGraph)", bd=BD_NEW, tf=BD_NEW, dash="4 3")
s += box(778, 146, 84, 36, "fetch", ts=12.5, bg=BG_INFRA, bd=BD_INFRA)
s += arrow(862, 164, 880, 164)
s += box(880, 146, 84, 36, "compute", ts=12.5, bg=BG_INFRA, bd=BD_INFRA)
s += arrow(964, 164, 982, 164)
s += box(982, 146, 76, 36, "judge", ts=12.5, bg=BG_LLM, bd=BD_LLM)
s += poly([(1020, 182), (1020, 212)], color=BD_LLM, marker="al")
s += box(900, 214, 158, 38, "decide_next", ts=12.5, bg=BG_LLM, bd=BD_LLM)
s += poly([(900, 233), (820, 233), (820, 182)], color=BD_LLM, marker="al")
s += label(778, 278, "추가 조회 (상한 max_probe_rounds)", 11, MUTED, "start")
s += arrow(1074, 164, 1092, 164)
s += box(1092, 144, 56, 40, "output", ts=11.5)

s += label(658, 314, "· 단계마다 config로 교체 가능 (nodes override)", 12.5, INK, "start")
s += label(658, 340, "· LLM은 어느 노드로 갈지만 정한다", 12.5, INK, "start")
s += label(672, 361, "각 노드가 무엇을 조회할지는 코드에 고정", 11.8, MUTED, "start")
s += label(658, 386, "· 반복 상한을 넘기면 확신도를 낮춰 부분 실패로", 12.5, INK, "start")

s += group(40, 420, 1120, 44, "")
s += label(60, 448, "설계 근거 — 오류 탐지 정확도는 최고 52.87%인 반면, 위치를 알려주면 "
                    "수정 성능은 23~44%p 향상된다 (ACL Findings 2024). "
                    "따라서 '언제 확인할지'는 코드가, '무엇이 문제인지'는 LLM이 맡는다.",
           12.3, INK, "start")
s += "</svg>"
save("subgraph-process", s, W, H)


# ══════════════════════════════════════════════════════════
# SVG → PNG
# ══════════════════════════════════════════════════════════
rendered = 0
for name, (w, h) in SIZES.items():
    svg, png = OUT / f"{name}.svg", OUT / f"{name}.png"
    ok = run_chrome(
        "--force-device-scale-factor=2",      # 2배 해상도
        "--default-background-color=ffffff",
        f"--window-size={w},{h}",             # SVG 크기 그대로. 손으로 맞출 일이 없다
        f"--screenshot={png}",
        svg.as_uri(),
    )
    if not ok:
        break
    rendered += 1
    print(f"  {rel(svg)}  →  {rel(png)}  ({w}×{h} @2x)")

print(f"\nSVG {len(SIZES)}장, PNG {rendered}장을 만들었습니다.")
if rendered < len(SIZES):
    warn_no_chrome("PNG")
