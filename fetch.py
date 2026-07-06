# -*- coding: utf-8 -*-
"""피키픽시 트렌드 워치 — 데이터 수집 모듈 (API 키 불필요).

YouTube Shorts : YouTube 웹사이트 내부 검색 엔드포인트(Innertube) 사용. 키 불필요.
TikTok         : tikwm.com 비공개 공개 API. 키 불필요. 한국(KR) 영상만 필터.
Instagram      : 공개 검색엔진(DDG/Bing/Mojeek)에서 릴스 URL 자동 발굴 (best-effort).
                 인스타그램은 로그인 없이 조회수를 제공하지 않으므로 조회수는 '미상' 처리,
                 UI에서 수동 등록/보정 가능.

표준 라이브러리만 사용한다 (별도 설치 불필요).
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STORE_PATH = os.path.join(DATA_DIR, "videos.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_store():
    if os.path.exists(STORE_PATH):
        with open(STORE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"last_updated": None, "videos": {}, "instagram_manual": []}


def save_store(store):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)


def _http(url, data=None, headers=None, timeout=25):
    h = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _http_json(url, payload=None, data=None, timeout=25):
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    return json.loads(_http(url, data=data, headers=headers, timeout=timeout))


# ---------------------------------------------------------------- YouTube (키 불필요)

# Innertube 검색 필터 (조회수순 + 업로드 기간 + 동영상 + 4분 미만)
_YT_PARAMS = {
    "today": "CAMSBggCEAEYAQ==",
    "week": "CAMSBggDEAEYAQ==",
    "month": "CAMSBggEEAEYAQ==",
    "year": "CAMSBggFEAEYAQ==",
}


def _yt_upload_window(days_back):
    d = int(days_back or 30)
    if d <= 1:
        return "today"
    if d <= 7:
        return "week"
    if d <= 31:
        return "month"
    return "year"


def _parse_korean_views(s):
    """'조회수 352,339회' → 352339"""
    m = re.search(r"([\d,\.]+)\s*([천만억]?)", s or "")
    if not m:
        return 0
    num = float(m.group(1).replace(",", ""))
    mult = {"천": 1e3, "만": 1e4, "억": 1e8}.get(m.group(2), 1)
    return int(num * mult)


def _parse_relative_date(s):
    """'6일 전', '2주 전', '1개월 전' → 대략적 ISO 날짜"""
    m = re.search(r"(\d+)\s*(분|시간|일|주|개월|년)", s or "")
    if not m:
        return ""
    n = int(m.group(1))
    unit = m.group(2)
    days = {"분": n / 1440, "시간": n / 24, "일": n,
            "주": n * 7, "개월": n * 30, "년": n * 365}[unit]
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_length(s):
    """'1:05' 또는 '1:02:03' → 초"""
    parts = [int(p) for p in (s or "0").split(":") if p.strip().isdigit()]
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


def _has_hangul(s):
    return bool(re.search(r"[가-힣]", s or ""))


def _walk(obj, key, out):
    if isinstance(obj, dict):
        if key in obj:
            out.append(obj[key])
        for v in obj.values():
            _walk(v, key, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, key, out)


def fetch_youtube(cfg, log):
    window = _yt_upload_window(cfg.get("days_back", 30))
    params = _YT_PARAMS[window]
    domestic = cfg.get("domestic_only", True)
    skipped_foreign = 0
    videos = {}
    for kw in cfg.get("keywords", []):
        payload = {
            "context": {"client": {
                "clientName": "WEB",
                "clientVersion": "2.20250101.00.00",
                "hl": "ko", "gl": "KR",
            }},
            "query": kw,
            "params": params,
        }
        try:
            res = _http_json(
                "https://www.youtube.com/youtubei/v1/search?prettyPrint=false",
                payload=payload)
        except Exception as e:
            log.append(f"YouTube '{kw}' 검색 실패: {e}")
            continue
        items = []
        _walk(res, "videoRenderer", items)
        for it in items:
            vid = it.get("videoId")
            if not vid or ("yt_" + vid) in videos:
                continue
            dur = _parse_length(it.get("lengthText", {}).get("simpleText"))
            if dur == 0 or dur > 185:  # Shorts만 (3분 이하)
                continue
            title = "".join(r.get("text", "")
                            for r in it.get("title", {}).get("runs", []))
            channel = "".join(r.get("text", "")
                              for r in it.get("ownerText", {}).get("runs", []))
            if domestic and not (_has_hangul(title) or _has_hangul(channel)):
                skipped_foreign += 1
                continue
            thumbs = it.get("thumbnail", {}).get("thumbnails", [])
            videos["yt_" + vid] = {
                "id": "yt_" + vid,
                "platform": "youtube",
                "title": title,
                "channel": channel,
                "url": f"https://www.youtube.com/shorts/{vid}",
                "thumbnail": thumbs[-1]["url"] if thumbs else "",
                "views": _parse_korean_views(
                    it.get("viewCountText", {}).get("simpleText")),
                "likes": 0,
                "duration": dur,
                "published": _parse_relative_date(
                    it.get("publishedTimeText", {}).get("simpleText")),
                "keyword": kw,
            }
        time.sleep(0.5)
    result = list(videos.values())
    msg = f"YouTube: {len(result)}개 수집 (최근 {window}, 한국 기준)"
    if skipped_foreign:
        msg += f" — 해외 영상 {skipped_foreign}개 제외"
    log.append(msg)
    return result


# ---------------------------------------------------------------- TikTok (키 불필요)

def fetch_tiktok(cfg, log):
    videos = []
    seen = set()
    count = min(int(cfg.get("max_per_keyword", 25)), 30)
    domestic = cfg.get("domestic_only", True)
    skipped_foreign = 0
    for kw in cfg.get("keywords", []):
        data = urllib.parse.urlencode({"keywords": kw, "count": count}).encode()
        try:
            res = _http_json("https://www.tikwm.com/api/feed/search", data=data)
        except Exception as e:
            log.append(f"TikTok '{kw}' 검색 실패: {e}")
            time.sleep(1.2)
            continue
        if res.get("code") != 0:
            log.append(f"TikTok '{kw}' 응답 오류: {res.get('msg')}")
            time.sleep(1.2)
            continue
        for item in res.get("data", {}).get("videos", []):
            vid = item.get("video_id")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            author = item.get("author", {}) or {}
            if domestic and (
                item.get("region") != "KR"
                or not (_has_hangul(item.get("title", ""))
                        or _has_hangul(author.get("nickname", "")))):
                skipped_foreign += 1
                continue
            handle = author.get("unique_id", "")
            created = item.get("create_time", 0)
            videos.append({
                "id": "tt_" + vid,
                "platform": "tiktok",
                "title": item.get("title", ""),
                "channel": author.get("nickname", handle),
                "url": f"https://www.tiktok.com/@{handle}/video/{vid}",
                "thumbnail": item.get("cover", ""),
                "views": int(item.get("play_count", 0)),
                "likes": int(item.get("digg_count", 0)),
                "duration": int(item.get("duration", 0)),
                "published": datetime.fromtimestamp(created, tz=timezone.utc)
                             .strftime("%Y-%m-%dT%H:%M:%SZ") if created else "",
                "keyword": kw,
            })
        time.sleep(1.2)  # tikwm 요청 제한 (약 1회/초)
    msg = f"TikTok: {len(videos)}개 수집 (한국 영상만)"
    if skipped_foreign:
        msg += f" — 해외 영상 {skipped_foreign}개 제외"
    log.append(msg)
    return videos


# ---------------------------------------------------------------- Instagram (best-effort)

_REEL_LINK_RE = re.compile(
    r'href="(https://www\.instagram\.com/reel/([A-Za-z0-9_\-]+)[^"]*)"')
_REEL_RE = re.compile(r"instagram\.com(?:%2F|/)reel(?:%2F|/)([A-Za-z0-9_\-]+)")
_TAG_RE = re.compile(r"<[^>]+>")


def _ig_parse_brave(page):
    """Brave 검색 결과에서 (릴스ID, 제목) 목록 추출."""
    import html as _h
    out = []
    for m in _REEL_LINK_RE.finditer(page):
        rid = m.group(2)
        seg = page[m.end():m.end() + 2000]
        tm = (re.search(r'title[^>]*>(.*?)</div>', seg, re.S)
              or re.search(r'<span[^>]*>(.*?)</span>', seg, re.S))
        title = _h.unescape(_TAG_RE.sub("", tm.group(1))).strip() if tm else ""
        out.append((rid, title))
    return out


def _ig_search_brave(kw):
    q = urllib.parse.quote(f"site:instagram.com/reel {kw}")
    page = _http(f"https://search.brave.com/search?q={q}")
    return _ig_parse_brave(page)


def _ig_search_ddg(kw):
    body = urllib.parse.urlencode(
        {"q": f"site:instagram.com/reel {kw}"}).encode()
    page = _http("https://html.duckduckgo.com/html/", data=body)
    if "challenge" in page and "duck" in page.lower():
        raise RuntimeError("봇 차단(캡차)")
    return [(rid, "") for rid in dict.fromkeys(_REEL_RE.findall(page))]


def _ig_search_mojeek(kw):
    q = urllib.parse.quote(f"site:instagram.com/reel {kw}")
    page = _http(f"https://www.mojeek.com/search?q={q}")
    return [(rid, "") for rid in dict.fromkeys(_REEL_RE.findall(page))]


def _ig_clean_title(raw, kw):
    """'계정명 on Instagram: "캡션..."' → (제목, 계정명)"""
    if not raw:
        return f"릴스 ({kw})", ""
    m = re.match(r'^(.{1,40}?) on Instagram:?\s*[\"“]?(.*)$', raw)
    if m:
        channel = m.group(1).strip()
        title = m.group(2).strip().strip('"”').strip()
        return (title or raw, channel)
    m = re.match(r"^(.{1,30}?)\s*\|\s*(.+)$", raw)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    return raw, ""


def fetch_instagram(cfg, log, existing_ids):
    """공개 검색엔진에서 릴스 URL·제목을 발굴한다. 조회수는 로그인 없이 미제공(null)."""
    engines = [("Brave", _ig_search_brave),
               ("DuckDuckGo", _ig_search_ddg),
               ("Mojeek", _ig_search_mojeek)]
    videos = []
    seen = set()
    blocked = set()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for kw in cfg.get("keywords", []):
        found = []
        for name, fn in engines:
            if name in blocked:
                continue
            try:
                found = fn(kw)
            except Exception as e:
                if "429" in str(e):  # 요청 과다 → 잠시 대기 후 1회 재시도
                    time.sleep(25)
                    try:
                        found = fn(kw)
                    except Exception as e2:
                        blocked.add(name)
                        log.append(f"Instagram({name}) '{kw}' 차단/실패: {e2}")
                        continue
                else:
                    blocked.add(name)
                    log.append(f"Instagram({name}) '{kw}' 차단/실패: {e}")
                    continue
            if found:
                break
            time.sleep(1.5)
        for rid, raw_title in found:
            vid = "ig_" + rid
            if vid in seen or vid in existing_ids:
                continue
            seen.add(vid)
            title, channel = _ig_clean_title(raw_title, kw)
            videos.append({
                "id": vid,
                "platform": "instagram",
                "title": title,
                "channel": channel,
                "url": f"https://www.instagram.com/reel/{rid}/",
                "thumbnail": "",
                "views": None,  # 로그인 없이 조회수 미제공
                "likes": 0,
                "published": today,
                "keyword": kw,
                "auto": True,
            })
        time.sleep(8.0)  # 검색엔진 rate limit 보호
    if len(blocked) == len(engines):
        log.append("Instagram: 모든 검색엔진이 차단됨 — 나중에 다시 시도하거나 수동 등록 이용")
    log.append(f"Instagram: 신규 릴스 {len(videos)}개 발굴 "
               "(조회수는 인스타그램 정책상 미제공 → 카드에서 직접 입력 가능)")
    return videos


# ---------------------------------------------------------------- 통합

def refresh():
    """전체 플랫폼 수집 후 저장. (수집 결과 요약, 로그) 반환."""
    cfg = load_config()
    store = load_store()
    log = []
    today = datetime.now().strftime("%Y-%m-%d")

    new_videos = fetch_youtube(cfg, log) + fetch_tiktok(cfg, log)
    if cfg.get("instagram_auto", True):
        new_videos += fetch_instagram(cfg, log, set(store["videos"].keys()))

    added = 0
    for v in new_videos:
        prev = store["videos"].get(v["id"])
        if prev:
            if v.get("views") is not None:
                prev["views"] = v["views"]
                prev.setdefault("history", {})[today] = v["views"]
            prev["likes"] = v.get("likes", prev.get("likes", 0))
            prev["last_seen"] = today
        else:
            v["first_seen"] = today
            v["last_seen"] = today
            if v.get("views") is not None:
                v["history"] = {today: v["views"]}
            store["videos"][v["id"]] = v
            added += 1

    store["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_store(store)
    log.append(f"신규 {added}개, 전체 {len(store['videos'])}개 보유")
    return {"added": added, "total": len(store["videos"]),
            "last_updated": store["last_updated"]}, log


if __name__ == "__main__":
    summary, log = refresh()
    for line in log:
        print(line)
    print(summary)
