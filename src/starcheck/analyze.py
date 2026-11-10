"""Signs that a repo's stars were bought: empty throwaway accounts, created just before starring, arriving in bursts,
made in batches on the same day.

No single signal means much (plenty of real people have empty profiles). A star counts as suspicious only when an
empty account also shows one of the other signs.
"""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from .github import Repo, Star

FRESH_DAYS = 30  # account created this soon before starring
BURST_MIN = 20  # stars in one day to consider it a burst
CLUSTER_MIN = 8  # empty accounts created on the same day


def empty(s: Star) -> bool:
    """An account that has never done anything visible: no followers, no repos, no gists, nothing in the profile."""
    return s.followers == 0 and s.following <= 1 and s.repos <= 1 and s.gists == 0 and not s.has_profile


def fresh(s: Star) -> bool:
    return (s.starred_at - s.created_at) < timedelta(days=FRESH_DAYS)


@dataclass
class Burst:
    day: date
    stars: int
    empty_share: float


@dataclass
class Result:
    repo: Repo
    stars: list[Star]
    suspicious: list[Star] = field(default_factory=list)
    empty: int = 0
    fresh: int = 0
    bursts: list[Burst] = field(default_factory=list)
    clusters: list[tuple[date, int]] = field(default_factory=list)
    sampled_all: bool = True

    @property
    def share(self) -> float:
        return len(self.suspicious) / len(self.stars) if self.stars else 0.0

    @property
    def estimate(self) -> int:
        return round(self.share * self.repo.stars)

    @property
    def verdict(self) -> tuple[str, str]:
        if len(self.stars) < 30:
            return "Too few stars to tell", "dim"
        if self.share >= 0.2:
            return "Lots of these stars look fake", "bold #ff5f5f"
        if self.share >= 0.05:
            return "Some stars look fake", "bold #ffaf5f"
        return "Stars look organic", "bold #87d787"

    def share_line(self) -> str:
        if self.share >= 0.05:
            return (f"{self.share:.0%} of {self.repo.name}'s stars look fake (about {self.estimate:,} of "
                    f"{self.repo.stars:,}). Check any repo: uvx starcheck owner/repo")
        return (f"{self.repo.name}'s {self.repo.stars:,} stars look real. Check any repo before you trust its stars: "
                "uvx starcheck owner/repo")


def bursts(stars: list[Star]) -> list[Burst]:
    by_day: dict[date, list[Star]] = {}
    for s in stars:
        by_day.setdefault(s.starred_at.date(), []).append(s)
    if not by_day:
        return []
    days = sorted(by_day)
    out = []
    for d in days:
        n = len(by_day[d])
        if n < BURST_MIN:
            continue
        around = [len(by_day.get(d + timedelta(days=k), [])) for k in range(-15, 16) if k]
        base = max(1.0, statistics.median(around))
        e = sum(empty(s) for s in by_day[d]) / n
        if n >= 5 * base and e >= 0.5:
            out.append(Burst(d, n, e))
    return out


def clusters(stars: list[Star]) -> list[tuple[date, int]]:
    c = Counter(s.created_at.date() for s in stars if empty(s))
    return sorted(((d, n) for d, n in c.items() if n >= CLUSTER_MIN), key=lambda x: -x[1])


def analyze(repo: Repo, stars: list[Star]) -> Result:
    r = Result(repo, stars, sampled_all=len(stars) >= repo.stars * 0.95)
    r.bursts = bursts(stars)
    r.clusters = clusters(stars)
    burst_days = {b.day for b in r.bursts}
    cluster_days = {d for d, _ in r.clusters}
    for s in stars:
        e, f = empty(s), fresh(s)
        r.empty += e
        r.fresh += f
        if e and (f or s.starred_at.date() in burst_days or s.created_at.date() in cluster_days):
            r.suspicious.append(s)
    return r


def timeline(stars: list[Star], bad: set = frozenset(), width: int = 60) -> list[tuple[date, int, int]]:
    """(bucket start, stars, suspicious stars) over the sampled period, `width` buckets."""
    if not stars:
        return []
    start, end = stars[0].starred_at.date(), stars[-1].starred_at.date()
    span = max(1, (end - start).days + 1)
    step = max(1, -(-span // width))
    buckets: dict[int, list[int]] = {}
    for s in stars:
        i = (s.starred_at.date() - start).days // step
        b = buckets.setdefault(i, [0, 0])
        b[0] += 1
        b[1] += s.login in bad
    return [(start + timedelta(days=i * step), *buckets.get(i, [0, 0])) for i in range(-(-span // step))]
