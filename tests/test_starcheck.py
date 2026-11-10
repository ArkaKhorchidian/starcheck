from __future__ import annotations

import io
import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from rich.console import Console

from starcheck import analyze, cli, github
from starcheck.github import GitHubError, Repo, Star

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def real(i: int, when: datetime) -> Star:
    rnd = random.Random(i)
    return Star(f"dev{i}", when, when - timedelta(days=rnd.randint(200, 3000)), rnd.randint(1, 300), rnd.randint(0, 50),
                rnd.randint(2, 60), rnd.randint(5, 900), rnd.randint(0, 3), rnd.random() < 0.7)


def fake(i: int, when: datetime, created: datetime) -> Star:
    return Star(f"x{i}", when, created, 0, 0, 0, 3, 0, False)


def organic(n=400) -> list[Star]:
    return [real(i, T0 + timedelta(hours=7 * i)) for i in range(n)]


def bought() -> list[Star]:
    stars = organic(300)
    burst_day = T0 + timedelta(days=40)
    batch = T0 + timedelta(days=35)
    stars += [fake(i, burst_day + timedelta(minutes=i), batch) for i in range(150)]
    return sorted(stars, key=lambda s: s.starred_at)


def repo(stars=450, forks=40) -> Repo:
    return Repo("acme/rocket", stars, forks, 20, 5, 3, T0)


def test_organic_repo_is_clean():
    r = analyze.analyze(repo(400), organic())
    assert r.suspicious == [] and r.bursts == [] and r.clusters == []
    assert r.verdict[0] == "Stars look organic" and "look real" in r.share_line()


def test_bought_stars_are_caught():
    r = analyze.analyze(repo(450), bought())
    assert len(r.suspicious) == 150 and r.share == pytest.approx(150 / 450)
    assert r.bursts and r.bursts[0].stars == 153 and r.bursts[0].empty_share > 0.95  # 3 real stars that day too
    assert r.clusters[0][1] == 150
    assert r.verdict[0] == "Lots of these stars look fake" and r.estimate == 150
    assert "33% of acme/rocket's stars look fake" in r.share_line()


def test_empty_account_alone_is_not_suspicious():
    lurkers = [Star(f"l{i}", T0 + timedelta(days=i), T0 - timedelta(days=900 + i * 3), 0, 0, 0, 2, 0, False)
               for i in range(100)]
    r = analyze.analyze(repo(100), lurkers)
    assert r.empty == 100 and r.suspicious == []


def test_launch_day_burst_of_real_people_is_fine():
    stars = organic(200) + [real(1000 + i, T0 + timedelta(days=30, minutes=i)) for i in range(300)]
    r = analyze.analyze(repo(500), sorted(stars, key=lambda s: s.starred_at))
    assert r.bursts == [] and r.suspicious == []


def test_timeline():
    r = analyze.analyze(repo(450), bought())
    tl = analyze.timeline(r.stars, {s.login for s in r.suspicious}, width=20)
    assert sum(n for _, n, _ in tl) == 450 and sum(b for _, _, b in tl) == 150 and len(tl) <= 20


def test_parse_repo():
    assert github.parse_repo("acme/rocket") == ("acme", "rocket")
    assert github.parse_repo("https://github.com/acme/rocket.git") == ("acme", "rocket")
    assert github.parse_repo("git@github.com:acme/rocket") == ("acme", "rocket")
    with pytest.raises(GitHubError):
        github.parse_repo("rocket")


def edge(s: Star | None, at: datetime) -> dict:
    if s is None:
        return {"starredAt": at.isoformat(), "node": None}
    return {"starredAt": s.starred_at.isoformat().replace("+00:00", "Z"), "node": {
        "login": s.login, "createdAt": s.created_at.isoformat(), "bio": "hi" if s.has_profile else None,
        "company": None, "location": None, "websiteUrl": None, "twitterUsername": None,
        "followers": {"totalCount": s.followers}, "following": {"totalCount": s.following},
        "repositories": {"totalCount": s.repos}, "starredRepositories": {"totalCount": s.starred},
        "gists": {"totalCount": s.gists}}}


def fake_api(stars: list[Star], total: int | None = None):
    calls = []

    def post(query, v, tok):
        calls.append(v)
        order = stars if v["dir"] == "ASC" else stars[::-1]
        start = int(v["after"] or 0)
        page = order[start:start + 100]
        edges = [edge(s, T0) for s in page]
        if start == 0 and v["dir"] == "ASC":
            edges[0] = edge(None, T0)  # a deleted account
        return {"repository": {
            "nameWithOwner": "acme/rocket", "stargazerCount": total or len(stars), "forkCount": 9, "isFork": False,
            "createdAt": "2024-01-01T00:00:00Z", "watchers": {"totalCount": 3}, "issues": {"totalCount": 1},
            "pullRequests": {"totalCount": 0},
            "stargazers": {"pageInfo": {"hasNextPage": start + 100 < len(order), "endCursor": str(start + 100)},
                           "edges": edges}}}
    return post, calls


def test_fetch_small_repo_reads_everything():
    stars = organic(250)
    post, calls = fake_api(stars)
    repo_, got = github.fetch("acme", "rocket", limit=3000, tok="t", post=post)
    assert repo_.stars == 250 and len(got) == 249 and len(calls) == 3  # one deleted account dropped
    assert got == sorted(got, key=lambda s: s.starred_at)


def test_fetch_big_repo_samples_both_ends():
    stars = organic(1000)
    post, calls = fake_api(stars)
    _, got = github.fetch("acme", "rocket", limit=400, tok="t", post=post)
    assert [c["dir"] for c in calls] == ["ASC", "ASC", "DESC", "DESC"]
    assert got[0].login == "dev1" and got[-1].login == "dev999" and len(got) == 399


def test_fetch_needs_a_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(github.shutil, "which", lambda _: None)
    with pytest.raises(GitHubError, match="token"):
        github.fetch("a", "b")
    monkeypatch.setenv("GH_TOKEN", "abc ")
    assert github.token() == "abc"


def test_render_and_main(monkeypatch, capsys):
    c = Console(width=110, record=True, file=io.StringIO())
    cli.render(analyze.analyze(repo(450), bought()), c)
    out = c.export_text()
    assert "Lots of these stars look fake" in out and "burst" in out and "batch" in out and "uvx starcheck" in out
    monkeypatch.setattr(cli, "fetch", lambda o, n, m, progress: (repo(450), bought()))
    assert cli.main(["acme/rocket", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["suspicious"] == 150 and data["batches"][0]["accounts"] == 150

    def boom(*a, **k):
        raise GitHubError("acme/nope not found (or it's private).")
    monkeypatch.setattr(cli, "fetch", boom)
    assert cli.main(["acme/nope"]) == 2
