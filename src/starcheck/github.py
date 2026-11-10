"""Fetch a repo's stargazers (who starred it, when, and what their accounts look like) with GitHub's GraphQL API."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime

API = "https://api.github.com/graphql"

QUERY = """
query($owner: String!, $name: String!, $after: String, $dir: OrderDirection!) {
  repository(owner: $owner, name: $name) {
    nameWithOwner stargazerCount forkCount createdAt isFork
    watchers { totalCount }
    issues { totalCount }
    pullRequests { totalCount }
    stargazers(first: 100, after: $after, orderBy: {field: STARRED_AT, direction: $dir}) {
      pageInfo { hasNextPage endCursor }
      edges {
        starredAt
        node {
          login createdAt bio company location websiteUrl twitterUsername
          followers { totalCount }
          following { totalCount }
          repositories(privacy: PUBLIC) { totalCount }
          starredRepositories { totalCount }
          gists { totalCount }
        }
      }
    }
  }
  rateLimit { remaining resetAt }
}
"""


class GitHubError(Exception):
    pass


@dataclass
class Star:
    login: str
    starred_at: datetime
    created_at: datetime
    followers: int
    following: int
    repos: int
    starred: int  # how many repos this account has starred in total
    gists: int
    has_profile: bool  # bio, company, location, website or twitter filled in


@dataclass
class Repo:
    name: str
    stars: int
    forks: int
    watchers: int
    issues: int
    prs: int
    created_at: datetime


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def token() -> str:
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip()
    if shutil.which("gh"):
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def parse_repo(arg: str) -> tuple[str, str]:
    m = re.search(r"(?:github\.com[/:])?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", arg.strip())
    if not m:
        raise GitHubError(f"'{arg}' isn't a repo. Use owner/name or a GitHub URL.")
    return m.group(1), m.group(2)


def post(query: str, variables: dict, tok: str) -> dict:
    req = urllib.request.Request(API, json.dumps({"query": query, "variables": variables}).encode(),
                                 {"Authorization": f"bearer {tok}", "Content-Type": "application/json",
                                  "User-Agent": "starcheck"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise GitHubError("GitHub rejected the token. Run `gh auth login` or set a valid GITHUB_TOKEN.") from None
        raise GitHubError(f"GitHub API error {e.code}") from None
    except urllib.error.URLError as e:
        raise GitHubError(f"Can't reach GitHub: {e.reason}") from None
    if data.get("errors"):
        raise GitHubError(data["errors"][0].get("message", "GitHub API error"))
    return data["data"]


def to_star(edge: dict) -> Star | None:
    n = edge.get("node")
    if not n:  # deleted accounts come back empty
        return None
    return Star(n["login"], parse_time(edge["starredAt"]), parse_time(n["createdAt"]), n["followers"]["totalCount"],
                n["following"]["totalCount"], n["repositories"]["totalCount"],
                n["starredRepositories"]["totalCount"], n["gists"]["totalCount"],
                any(n.get(k) for k in ("bio", "company", "location", "websiteUrl", "twitterUsername")))


def fetch(owner: str, name: str, limit: int = 3000, tok: str = "", post=post, progress=lambda done, total: None):
    """(Repo, stars). Up to `limit` stargazers: all of them for small repos, otherwise the oldest and newest halves."""
    tok = tok or token()
    if not tok:
        raise GitHubError("starcheck needs a GitHub token (GitHub only lists stargazers to signed-in users). "
                          "Run `gh auth login`, or set GITHUB_TOKEN to any token; no scopes needed.")
    stars: list[Star] = []
    repo = None
    directions = ["ASC"]
    seen: set[str] = set()
    for direction in directions:
        after = None
        want = limit if len(directions) == 1 and direction == "ASC" else limit // 2
        got = 0
        while got < want:
            d = post(QUERY, {"owner": owner, "name": name, "after": after, "dir": direction}, tok)
            r = d.get("repository")
            if not r:
                raise GitHubError(f"{owner}/{name} not found (or it's private).")
            if repo is None:
                repo = Repo(r["nameWithOwner"], r["stargazerCount"], r["forkCount"], r["watchers"]["totalCount"],
                            r["issues"]["totalCount"], r["pullRequests"]["totalCount"], parse_time(r["createdAt"]))
                if repo.stars > limit:  # big repo: sample both ends of the timeline
                    want = limit // 2
                    directions.append("DESC")
            for e in r["stargazers"]["edges"]:
                s = to_star(e)
                got += 1
                if s and s.login not in seen:
                    seen.add(s.login)
                    stars.append(s)
            progress(len(stars), min(limit, repo.stars))
            page = r["stargazers"]["pageInfo"]
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]
    stars.sort(key=lambda s: s.starred_at)
    return repo, stars
