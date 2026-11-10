"""Run starcheck on a made-up repo with made-up stargazers (no GitHub calls). Used to record the README GIF.
"acme/hyperfast-ai" doesn't exist; its 'bought' stars are generated below."""
import random
import sys
import time
from datetime import datetime, timedelta, timezone

from starcheck import cli
from starcheck.github import Repo, Star

rnd = random.Random(3)
T0 = datetime(2025, 3, 1, tzinfo=timezone.utc)
stars = []
for i in range(2100):  # a slow, real audience
    when = T0 + timedelta(minutes=rnd.randint(0, 600 * 24 * 60))
    if rnd.random() < 0.12:  # lurkers: real people with empty profiles, not suspicious on their own
        stars.append(Star(f"dev{i}", when, when - timedelta(days=rnd.randint(60, 3000)), 0, 0, 0, rnd.randint(1, 40),
                          0, False))
        continue
    stars.append(Star(f"dev{i}", when, when - timedelta(days=rnd.randint(100, 4000)), rnd.randint(1, 400),
                      rnd.randint(0, 60), rnd.randint(1, 80), rnd.randint(1, 900), rnd.randint(0, 4), rnd.random() < .65))
for k, (day, n, made) in enumerate([(120, 700, 112), (300, 900, 290), (301, 400, 60)]):  # three purchases
    for i in range(n):
        when = T0 + timedelta(days=day, minutes=rnd.randint(0, 1400))
        stars.append(Star(f"x{k}-{i}", when, T0 + timedelta(days=made, hours=rnd.randint(0, 23)), 0, 0,
                          rnd.choice([0, 0, 1]), rnd.randint(1, 6), 0, False))
stars.sort(key=lambda s: s.starred_at)
repo = Repo("acme/hyperfast-ai", len(stars), 41, 12, 9, 4, T0)


def fake_fetch(owner, name, limit, progress):
    for done in range(0, len(stars) + 1, 400):
        progress(done, len(stars))
        time.sleep(0.25)
    progress(len(stars), len(stars))
    return repo, stars


cli.fetch = fake_fetch
sys.exit(cli.main(sys.argv[1:] or ["acme/hyperfast-ai"]))
