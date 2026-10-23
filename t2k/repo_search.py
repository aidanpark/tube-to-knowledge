"""영상이 이름만 말한 GitHub 저장소의 주소를 검색으로 찾는다 (관련 링크의 보충).

요약 모델은 도구 없이 실행되므로(summarize.call_claude) 저장소 이름(repos)만 추출하고, 주소는 이 모듈이 GitHub 검색 API 로 찾는다.
판정: 이름이 정확히 같은 저장소(대소문자 · 기호 무시) 가운데 별이 가장 많은 것을 쓰되,
별이 MIN_STARS 이상이고 같은 이름의 두 번째 저장소보다 DOMINANCE 배 이상 많을 때만 쓴다. 애매하면 버린다.
(시험: 쇼츠 댓글 · 요약의 이름 21개에서 정답은 모두 1위였고, 1위와 2위의 차이는 가장 작은 것이 11.8배였다. 2026-10-23)
찾은 링크는 영상이 준 주소가 아니므로 estimated 로 표시한다 (사이트 · 요약 파일에 "추정").

설명란 · 게시자 댓글에 이미 주소가 있는 저장소는 검색하지 않는다.
실패(네트워크 · 한도 초과)는 링크 없이 넘어간다: 링크는 부가 정보다.
인증: 환경 변수 GITHUB_TOKEN → `gh auth token` → 인증 없음. 검색 한도는 분당 30회(인증) · 10회(인증 없음)이고,
남은 횟수가 0 이면 초기화 시각까지 기다린다 (최대 RESET_WAIT_MAX 초).
"""
import os
import re
import shutil
import subprocess
import time
from urllib.parse import urlsplit

import requests

SEARCH = "https://api.github.com/search/repositories"
MIN_STARS = 500
DOMINANCE = 5
RESET_WAIT_MAX = 65
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")  # GitHub 저장소 이름에 쓸 수 있는 글자


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def known_repos(urls: list[str]) -> set[str]:
    """github.com/<owner>/<repo>… 주소들의 저장소 이름(norm)."""
    out = set()
    for u in urls:
        p = urlsplit(u)
        parts = [x for x in p.path.split("/") if x]
        if p.netloc.lower().removeprefix("www.") == "github.com" and len(parts) >= 2:
            out.add(norm(parts[1]))
    return out


def token() -> str | None:
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        p = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() or None


def pick(name: str, items: list[dict]) -> dict | None:
    """검색 결과에서 판정 기준을 만족하는 저장소. 없으면 None."""
    same = sorted((i for i in items if norm(i.get("name", "")) == norm(name)),
                  key=lambda i: i.get("stargazers_count", 0), reverse=True)
    if not same or same[0].get("stargazers_count", 0) < MIN_STARS:
        return None
    if len(same) > 1 and same[0]["stargazers_count"] < DOMINANCE * same[1].get("stargazers_count", 0):
        return None
    return same[0]


class Searcher:
    def __init__(self, session: requests.Session | None = None, sleep=time.sleep):
        self.s = session or requests.Session()
        self.s.headers.update({"Accept": "application/vnd.github+json", "User-Agent": "tube-to-knowledge"})
        if (tok := token()):
            self.s.headers["Authorization"] = f"Bearer {tok}"
        self.sleep = sleep

    def search(self, name: str) -> list[dict] | None:
        """이름 검색 결과(별 순). 실패하면 None."""
        try:
            r = self.s.get(SEARCH, params={"q": f"{name} in:name", "sort": "stars", "per_page": 20}, timeout=20)
        except requests.RequestException:
            return None
        if r.headers.get("x-ratelimit-remaining") == "0":
            wait = int(r.headers.get("x-ratelimit-reset", "0")) - int(time.time()) + 1
            self.sleep(min(max(wait, 1), RESET_WAIT_MAX))
        if r.status_code != 200:
            return None
        return r.json().get("items") or []


def find(repos: list[dict], known_urls: list[str], limit: int, searcher: Searcher | None = None) -> list[dict]:
    """repos([{"name", "label"}]) 의 주소를 찾아 링크 [{"url", "label", "estimated": True}] 로 반환한다 (최대 limit 개)."""
    known = known_repos(known_urls)
    todo, seen = [], set()
    for x in repos:
        n = norm(x["name"])
        if n and n not in known and n not in seen:
            seen.add(n)
            todo.append(x)
    if limit <= 0 or not todo:
        return []
    searcher = searcher or Searcher()
    out = []
    for x in todo:
        items = searcher.search(x["name"])
        if items is None:
            break  # 한도 초과 · 네트워크 오류: 남은 것은 찾지 않는다
        hit = pick(x["name"], items)
        if hit:
            out.append({"url": hit["html_url"], "label": x["label"], "estimated": True})
            if len(out) >= limit:
                break
    return out
