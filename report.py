# -*- coding: utf-8 -*-
"""피키픽시 주간 마케팅 소구점 리포트 생성기.

data/videos.json의 수집 데이터를 분석하여 reports/YYYY-Wxx.md를 생성한다.
모든 수치는 수집된 데이터에서 계산한 값이며 외부 추정치를 포함하지 않는다.
표준 라이브러리만 사용.
"""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(BASE_DIR, "data", "videos.json")
REPORT_DIR = os.path.join(BASE_DIR, "reports")

# ---- 테마 사전 (제목 키워드 → 소비자 니즈 테마) ----
THEMES = {
    "손상모 복구": ["손상", "극손상", "보수", "단백질", "케라틴", "클리닉", "복구", "회복"],
    "윤기·광택": ["윤기", "광택", "물광", "유리머릿결", "찰랑", "반짝"],
    "볼륨·스타일링": ["볼륨", "고데기", "웨이브", "컬", "앞머리", "세팅", "드라이", "스타일링", "펌"],
    "두피·탈모 케어": ["두피", "탈모", "비듬", "유분", "각질", "모근"],
    "염색·컬러": ["염색", "셀프염색", "탈색", "컬러", "새치", "브릿지"],
    "가성비·올영템": ["올리브영", "올영", "가성비", "세일", "할인", "저렴", "다이소"],
    "살롱·전문가 노하우": ["미용실", "미용사", "원장", "살롱", "디자이너", "전문가"],
    "향·감성": ["향", "향기", "퍼퓸", "향수"],
    "루틴·습관": ["루틴", "습관", "아침", "저녁", "매일", "순서"],
}

# ---- 훅(Hook) 패턴 사전 (제목의 후킹 방식) ----
HOOKS = {
    "리스트형 (TOP N·N가지)": re.compile(r"(TOP\s*\d|BEST\s*\d|\d+\s*가지|\d+\s*단계|순위)", re.I),
    "질문형": re.compile(r"[?？]|일까|할까요|어떻게"),
    "경고·금지형": re.compile(r"하지 ?마|금지|손해|절대|실수|망하|주의"),
    "비포애프터·변화형": re.compile(r"달라|바뀌|변화|비포|애프터|전후|한 ?달|일주일 ?만"),
    "추천·꿀팁형": re.compile(r"추천|꿀팁|필수템|인생템|찐"),
    "비밀·폭로형": re.compile(r"비밀|몰랐|사실은|진실|충격|폭로|안 ?알려"),
    "광고·협찬 표기": re.compile(r"#?(광고|협찬|AD)\b", re.I),
}


def _fmt(n):
    if n is None:
        return "—"
    if n >= 100000000:
        return f"{n/100000000:.1f}억".replace(".0억", "억")
    if n >= 10000:
        return f"{n/10000:.1f}만".replace(".0만", "만")
    return f"{n:,}"


def _week_label(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _load_videos():
    with open(STORE_PATH, encoding="utf-8") as f:
        store = json.load(f)
    vids = list(store.get("videos", {}).values())
    vids += store.get("instagram_manual", [])
    return vids


def _week_growth(v, week_ago_str):
    """최근 7일간 조회수 증가량 (history 기반). 이력이 부족하면 None."""
    hist = v.get("history") or {}
    if len(hist) < 2:
        return None
    dates = sorted(hist)
    base = None
    for d in dates:
        if d <= week_ago_str:
            base = hist[d]
    if base is None:
        base = hist[dates[0]]
    return hist[dates[-1]] - base


def generate(now=None):
    now = now or datetime.now()
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    videos = _load_videos()
    label = _week_label(now.date())
    period = f"{week_ago} ~ {now.strftime('%Y-%m-%d')}"

    with_views = [v for v in videos if v.get("views") is not None]
    ig_unknown = [v for v in videos if v.get("views") is None]
    fresh = [v for v in with_views if (v.get("first_seen") or "") >= week_ago]

    # ---- 플랫폼 요약 ----
    plat = defaultdict(lambda: {"n": 0, "views": []})
    for v in with_views:
        plat[v["platform"]]["n"] += 1
        plat[v["platform"]]["views"].append(v["views"])

    # ---- TOP / 급상승 ----
    top10 = sorted(with_views, key=lambda v: v["views"], reverse=True)[:10]
    rising = sorted(
        [(v, _week_growth(v, week_ago)) for v in with_views],
        key=lambda t: (t[1] or -1), reverse=True)
    rising = [(v, g) for v, g in rising if g and g > 0][:5]
    fresh_top = sorted(fresh, key=lambda v: v["views"], reverse=True)[:5]

    # ---- 테마 분석 (조회수 가중) ----
    theme_stats = {}
    total_views = sum(v["views"] for v in with_views) or 1
    for theme, kws in THEMES.items():
        matched = [v for v in with_views
                   if any(k in (v.get("title") or "") for k in kws)]
        if not matched:
            continue
        tv = sum(v["views"] for v in matched)
        theme_stats[theme] = {
            "n": len(matched),
            "views": tv,
            "share": tv / total_views * 100,
            "top": sorted(matched, key=lambda v: v["views"], reverse=True)[:3],
        }
    theme_rank = sorted(theme_stats.items(),
                        key=lambda t: t[1]["views"], reverse=True)

    # ---- 훅 패턴 분석 (조회수 상위 50개 대상) ----
    top50 = sorted(with_views, key=lambda v: v["views"], reverse=True)[:50]
    hook_stats = []
    for name, pat in HOOKS.items():
        hits = [v for v in top50 if pat.search(v.get("title") or "")]
        if hits:
            hook_stats.append((name, len(hits),
                               max(hits, key=lambda v: v["views"])))
    hook_stats.sort(key=lambda t: t[1], reverse=True)

    # ---- 해시태그 ----
    tags = Counter()
    for v in videos:
        for tag in re.findall(r"#([0-9A-Za-z가-힣_]+)", v.get("title") or ""):
            tags[tag] += 1
    top_tags = tags.most_common(20)

    # ---- 소구점 제안 (수집 데이터 기반 규칙 생성) ----
    suggestions = []
    copy_templates = {
        "손상모 복구": ("손상모 고민을 정면으로 겨냥한 '복구/회복' 소구",
                    ["매일 고데기? 그래도 머릿결은 피키픽시가 지킵니다",
                     "극손상모 구조대, 피키픽시 7일 챌린지",
                     "단백질 채우는 순서, 미용실 말고 집에서"]),
        "윤기·광택": ("눈으로 확인되는 '윤기' 비주얼 소구 (비포/애프터 최적)",
                   ["빛나는 건 조명이 아니라 머릿결이었다",
                    "물광 피부 말고, 물광 머릿결",
                    "찰랑임이 증명하는 피키픽시"]),
        "볼륨·스타일링": ("스타일링 과정·결과를 보여주는 '변신' 소구",
                     ["아침 5분, 볼륨이 달라진다",
                      "고데기 없이도 살아나는 웨이브",
                      "스타일링의 마지막 한 방울, 피키픽시"]),
        "두피·탈모 케어": ("두피 건강 불안을 케어 습관으로 전환하는 소구",
                      ["머릿결의 시작은 두피부터",
                       "오늘 감은 머리, 두피는 만족했을까?",
                       "두피 루틴에 피키픽시 한 단계"]),
        "염색·컬러": ("셀프 염색 손상 걱정을 해소하는 '애프터 케어' 소구",
                   ["셀프 염색 후 3일, 골든타임을 지켜라",
                    "컬러는 살리고 손상은 줄이고",
                    "염색머리 전용 케어, 피키픽시"]),
        "가성비·올영템": ("가격 대비 성능·접근성 소구 (리뷰/추천 포맷)",
                     ["한 병으로 살롱 부럽지 않게",
                      "가성비로 시작해서 인생템으로",
                      "장바구니에 넣기 전에 이 영상부터"]),
        "살롱·전문가 노하우": ("전문가 권위 빌리기 소구 (미용사 협업 콘텐츠)",
                        ["미용실 원장님이 조용히 쓰는 그 제품",
                         "디자이너의 마무리 한 스텝",
                         "살롱 케어를 화장대 위로"]),
        "향·감성": ("향 중심 감성 소구 (무드·ASMR 포맷)",
                 ["스치기만 해도 남는 향",
                  "머릿결에 뿌리는 향수",
                  "향으로 기억되는 사람"]),
        "루틴·습관": ("데일리 루틴 속 한 단계로 자리잡는 소구",
                   ["샤워 후 30초, 머릿결 루틴",
                    "매일의 작은 습관이 만드는 큰 차이",
                    "아침 루틴 마지막은 피키픽시"]),
    }
    for theme, st in theme_rank[:3]:
        direction, copies = copy_templates.get(theme, ("", []))
        best = st["top"][0]
        suggestions.append({
            "theme": theme, "share": st["share"], "n": st["n"],
            "direction": direction,
            "evidence": f"「{(best.get('title') or '')[:50]}」 ({_fmt(best['views'])}회)",
            "copies": copies,
        })

    # ---- Markdown 작성 ----
    L = []
    L.append(f"# 피키픽시 주간 트렌드 리포트 — {label}")
    L.append(f"\n분석 기간: {period} · 분석 대상: 헤어 코스메틱 숏폼 "
             f"{len(videos)}개 (조회수 확인 가능 {len(with_views)}개)\n")
    L.append("> 모든 수치는 이 사이트가 수집한 영상 데이터에서 계산한 값입니다. "
             "Instagram 릴스는 조회수 미제공으로 정량 분석에서 제외됩니다.\n")

    L.append("## 1. 플랫폼 요약\n")
    for p, name in [("youtube", "YouTube Shorts"), ("tiktok", "TikTok"),
                    ("instagram", "Instagram Reels")]:
        s = plat.get(p)
        if s and s["views"]:
            vs = sorted(s["views"])
            L.append(f"- **{name}**: {s['n']}개 · 최고 {_fmt(vs[-1])}회 · "
                     f"중앙값 {_fmt(vs[len(vs)//2])}회")
        elif p == "instagram":
            L.append(f"- **Instagram Reels**: URL {len(ig_unknown)}개 발굴 (조회수 미상)")
    L.append("")

    L.append("## 2. 이번 주 조회수 TOP 10\n")
    for i, v in enumerate(top10, 1):
        L.append(f"{i}. [{(v.get('title') or '(제목 없음)')[:60]}]({v['url']}) — "
                 f"{_fmt(v['views'])}회 · {v['platform']} · {v.get('channel','')}")
    L.append("")

    if rising or fresh_top:
        L.append("## 3. 급상승·신규 주목 영상\n")
        for v, g in rising:
            L.append(f"- 📈 [{(v.get('title') or '')[:55]}]({v['url']}) — "
                     f"7일간 +{_fmt(g)}회 (현재 {_fmt(v['views'])}회)")
        for v in fresh_top:
            L.append(f"- 🆕 [{(v.get('title') or '')[:55]}]({v['url']}) — "
                     f"이번 주 발견, {_fmt(v['views'])}회 · {v['platform']}")
        L.append("")

    L.append("## 4. 테마별 소비자 관심 (조회수 가중)\n")
    for theme, st in theme_rank:
        L.append(f"- **{theme}** — 조회수 점유율 {st['share']:.1f}% "
                 f"({st['n']}개 영상)")
        L.append(f"  - 대표: 「{(st['top'][0].get('title') or '')[:50]}」 "
                 f"({_fmt(st['top'][0]['views'])}회)")
    L.append("")

    L.append("## 5. 훅(Hook) 패턴 — 조회수 상위 50개 영상 기준\n")
    for name, cnt, best in hook_stats:
        L.append(f"- **{name}**: {cnt}/50개 — 예: 「{(best.get('title') or '')[:45]}」"
                 f" ({_fmt(best['views'])}회)")
    L.append("")

    L.append("## 6. 피키픽시 마케팅 소구점 제안\n")
    for i, s in enumerate(suggestions, 1):
        L.append(f"### {i}. {s['theme']} (점유율 {s['share']:.1f}%)\n")
        L.append(f"- **방향**: {s['direction']}")
        L.append(f"- **근거**: 이번 주 해당 테마 영상 {s['n']}개, 대표 {s['evidence']}")
        L.append(f"- **카피 시안**:")
        for c in s["copies"]:
            L.append(f"  - “{c}”")
        L.append("")
    if hook_stats:
        L.append(f"**포맷 제안**: 이번 주 상위 영상에서 가장 잦은 훅은 "
                 f"‘{hook_stats[0][0]}’({hook_stats[0][1]}/50). "
                 "위 소구점과 이 훅 형식을 결합한 콘텐츠 제작을 권장합니다.\n")

    L.append("## 7. 해시태그 TOP 20\n")
    L.append(" · ".join(f"#{t}({c})" for t, c in top_tags))
    L.append("")

    os.makedirs(REPORT_DIR, exist_ok=True)
    fname = f"{label}.md"
    with open(os.path.join(REPORT_DIR, fname), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # 리포트 목록 index (정적 사이트에서 사용)
    files = sorted([f for f in os.listdir(REPORT_DIR) if f.endswith(".md")],
                   reverse=True)
    with open(os.path.join(REPORT_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(files, f, ensure_ascii=False)

    return fname


if __name__ == "__main__":
    print("생성됨:", generate())
