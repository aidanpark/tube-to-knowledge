"""이름만 나온 저장소의 GitHub 검색 시험. 네트워크 · gh 는 호출하지 않는다."""
from t2k import repo_search
from t2k.repo_search import find, known_repos, pick


def repo(full, stars):
    return {"name": full.split("/")[1], "html_url": f"https://github.com/{full}", "stargazers_count": stars}


def test_pick_most_starred_exact_name():
    items = [repo("other/orca-tools", 900000), repo("stablyai/orca", 103450), repo("hundredrabbits/Orca", 5124)]
    assert pick("orca", items)["html_url"] == "https://github.com/stablyai/orca"
    assert pick("Agent-Reach", [repo("Panniantong/Agent-Reach", 101242), repo("x/agent_reach", 150)]) is not None


def test_pick_rejects_ambiguous_or_obscure():
    assert pick("paperclip", [repo("a/paperclip", 10000), repo("b/paperclip", 3000)]) is None  # 5배 미만
    assert pick("tiny", [repo("a/tiny", 120)]) is None  # 별 500 미만
    assert pick("missing", [repo("a/other", 9000)]) is None


def test_known_repos_from_github_urls_only():
    assert known_repos(["https://github.com/pbakaus/impeccable", "https://github.com/a/b/tree/main/x",
                        "https://example.com/a/c", "https://github.com/onlyowner"]) == {"impeccable", "b"}


class FakeSearcher:
    def __init__(self, results):
        self.results, self.asked = results, []

    def search(self, name):
        self.asked.append(name)
        return self.results.get(name, [])


def test_find_skips_known_and_duplicates_and_marks_estimated():
    s = FakeSearcher({"meetily": [repo("Zackriya-Solutions/meetily", 32083)], "herdr": [repo("herdrdev/herdr", 45501)]})
    repos = [{"name": "impeccable", "label": "디자인 점검"}, {"name": "meetily", "label": "회의록"},
             {"name": "Meetily", "label": "중복"}, {"name": "herdr", "label": "터미널"}]
    out = find(repos, ["https://github.com/pbakaus/impeccable"], 10, s)
    assert s.asked == ["meetily", "herdr"]
    assert out == [{"url": "https://github.com/Zackriya-Solutions/meetily", "label": "회의록", "estimated": True},
                   {"url": "https://github.com/herdrdev/herdr", "label": "터미널", "estimated": True}]


def test_find_respects_limit_and_stops_on_failure():
    s = FakeSearcher({"a": [repo("o/a", 1000)], "b": [repo("o/b", 1000)]})
    assert len(find([{"name": "a", "label": "x"}, {"name": "b", "label": "y"}], [], 1, s)) == 1
    assert find([{"name": "a", "label": "x"}], [], 0, s) == []

    class Failing(FakeSearcher):
        def search(self, name):
            self.asked.append(name)
            return None
    f = Failing({})
    assert find([{"name": "a", "label": "x"}, {"name": "b", "label": "y"}], [], 10, f) == [] and f.asked == ["a"]


def test_searcher_waits_for_rate_limit_reset(monkeypatch):
    monkeypatch.setattr(repo_search, "token", lambda: None)
    monkeypatch.setattr(repo_search.time, "time", lambda: 1000)

    class Resp:
        status_code = 200
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"}

        def json(self):
            return {"items": [repo("o/a", 1)]}

    class Session:
        headers = {}

        def get(self, *a, **k):
            return Resp()

    waits = []
    assert repo_search.Searcher(Session(), sleep=waits.append).search("a") == [repo("o/a", 1)]
    assert waits == [31]
