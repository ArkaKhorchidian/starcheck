"""starcheck: are a GitHub repo's stars real?"""
from __future__ import annotations

import argparse
import json
import sys

from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text

from . import __version__
from .analyze import CLUSTER_MIN, FRESH_DAYS, Result, analyze, timeline
from .github import GitHubError, fetch, parse_repo

BARS = " ▁▂▃▄▅▆▇█"


def pct(n: int, total: int) -> str:
    return f"{n / total:.0%}" if total else "-"


def spark(r: Result, width: int, rows: int = 4) -> tuple[list[Text], str, str]:
    """A small bar chart of stars per period (square-root scale so the normal days still show next to a spike)."""
    tl = timeline(r.stars, {s.login for s in r.suspicious}, width)
    peak = max((n for _, n, _ in tl), default=1) or 1
    lines = [Text() for _ in range(rows)]
    for _, n, bad in tl:
        h = max(1, round(rows * 8 * (n / peak) ** 0.5)) if n else 0
        style = "#ff5f5f" if n and bad / n >= 0.5 else ("#ffaf5f" if n and bad / n >= 0.2 else "#5fd7ff")
        for row in range(rows):
            level = h - (rows - 1 - row) * 8
            lines[row].append(BARS[max(0, min(8, level))] if level > 0 else (" " if row < rows - 1 or n else "·"),
                              style)
    first = tl[0][0].strftime("%b %Y") if tl else ""
    last = r.stars[-1].starred_at.strftime("%b %Y") if r.stars else ""
    return lines, first, last


def render(r: Result, console: Console) -> None:
    verdict, vstyle = r.verdict
    n = len(r.stars)
    head = Text.assemble((r.repo.name, "bold"), f"  {r.repo.stars:,} stars · {r.repo.forks:,} forks · "
                         f"{r.repo.watchers:,} watching", ("" if r.sampled_all else f"  (checked {n:,} stargazers: the "
                                                           "oldest and newest)", "dim"))
    big = Text.assemble((verdict, vstyle))
    if n >= 30:
        big.append(f"   {r.share:.0%} suspicious", vstyle)
        if r.share >= 0.05:
            big.append(f" ≈ {r.estimate:,} fake stars", "dim")
    parts: list = [head, Text(""), big, Text("")]

    t = Table(box=None, header_style="dim", padding=(0, 2))
    t.add_column("Signal")
    t.add_column("Stargazers", justify="right")
    t.add_column("Share", justify="right")
    t.add_row("Empty accounts (no followers, repos, gists or profile)", f"{r.empty:,}", pct(r.empty, n))
    t.add_row(f"Account made less than {FRESH_DAYS} days before starring", f"{r.fresh:,}", pct(r.fresh, n))
    burst_n = sum(b.stars for b in r.bursts)
    t.add_row("Starred in a burst of mostly empty accounts", f"{burst_n:,}", pct(burst_n, n))
    cl_n = sum(c for _, c in r.clusters)
    t.add_row(f"Empty accounts created in batches ({CLUSTER_MIN}+ the same day)", f"{cl_n:,}", pct(cl_n, n))
    t.add_row(Text("Suspicious: empty and at least one of the above", "bold"), Text(f"{len(r.suspicious):,}", "bold"),
              Text(pct(len(r.suspicious), n), vstyle))
    parts += [t, Text("")]

    width = 64
    lines, a, b = spark(r, width)
    parts += [Text("Stars over time   ", "dim") + Text("■", "#ff5f5f") + Text(" mostly suspicious", "dim"), *lines,
              Text(a.ljust(max(len(a) + 1, len(lines[0]) - len(b))) + b, "dim"), Text("")]
    for burst in r.bursts[:4]:
        parts.append(Text.assemble(("  burst ", "#ff5f5f"), f"{burst.day:%b %d %Y}: {burst.stars:,} stars in a day, "
                                   f"{burst.empty_share:.0%} from empty accounts"))
    for d, c in r.clusters[:3]:
        parts.append(Text.assemble(("  batch ", "#ffaf5f"), f"{c} empty accounts created on {d:%b %d %Y} starred it"))
    if r.repo.stars >= 500:
        ratio = r.repo.stars / max(1, r.repo.forks)
        if ratio > 100:
            parts.append(Text.assemble(("  low forks ", "#ffaf5f"), f"1 fork per {ratio:,.0f} stars. Starred-but-never-"
                                       "used is what bought stars look like."))
    if r.bursts or r.clusters or r.repo.stars >= 500 and r.repo.stars / max(1, r.repo.forks) > 100:
        parts.append(Text(""))
    parts.append(Text("Signals, not proof: real people make empty accounts too. A star counts as suspicious only "
                      "when several signs line up.", "dim"))
    console.print(Panel(Group(*parts), title="[bold]Are these stars real?[/]", border_style="#ffd75f", padding=(1, 2)))
    console.print(Text("  Share: ", "dim") + Text(r.share_line(), "italic"))
    console.print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="starcheck", description="Check whether a GitHub repo's stars are real: empty "
                                 "throwaway accounts, created just before starring, arriving in bursts.")
    ap.add_argument("repo", help="owner/name or a GitHub URL")
    ap.add_argument("--max", type=int, default=3000, help="stargazers to check (default 3000; big repos are sampled "
                                                           "from both ends of the timeline)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-V", "--version", action="version", version=f"starcheck {__version__}")
    a = ap.parse_args(argv)
    console = Console(stderr=a.json, highlight=False)
    try:
        owner, name = parse_repo(a.repo)
        with Progress(TextColumn("[dim]Reading stargazers[/]"), BarColumn(), MofNCompleteColumn(),
                      console=console, transient=True) as prog:
            task = prog.add_task("", total=None)
            repo, stars = fetch(owner, name, max(100, a.max),
                                progress=lambda done, total: prog.update(task, completed=done, total=total))
    except GitHubError as e:
        console.print(f"[red]{e}[/]")
        return 2
    r = analyze(repo, stars)
    if a.json:
        print(json.dumps({"repo": repo.name, "stars": repo.stars, "forks": repo.forks, "checked": len(stars),
                          "suspicious": len(r.suspicious), "share": round(r.share, 4), "estimate": r.estimate,
                          "verdict": r.verdict[0], "empty": r.empty, "fresh": r.fresh,
                          "bursts": [{"day": str(b.day), "stars": b.stars, "empty_share": round(b.empty_share, 3)}
                                     for b in r.bursts],
                          "batches": [{"created": str(d), "accounts": n} for d, n in r.clusters]}, indent=2))
        return 0
    render(r, console)
    return 0


if __name__ == "__main__":
    sys.exit(main())
