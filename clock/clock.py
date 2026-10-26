"""Clock: target paper workflow ko fixed interval pe chalata hai (24x7).

GitHub ka cron 5-min/hourly jobs ko 3-6 ghante late chalata tha, isliye
ye ek lamba job hai jo time pe workflow_dispatch karta hai.
Usage: python clock/clock.py <workflow.yml> <every_min> <offset_min> <loop_min>
  e.g. paper.yml 60 7 340  -> har ghante :07 pe, 340 min tak, phir agla clock.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GITHUB_TOKEN"]


def dispatch(wf):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/actions/workflows/{wf}/dispatches",
        data=json.dumps({"ref": "main"}).encode(), method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"{e.code} {e.read()[:200]!r}"
    except Exception as e:
        return f"error {e}"


def main():
    wf, every, offset, loop = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
    end = time.time() + loop * 60
    fired = set()
    while time.time() < end:
        now = datetime.now(timezone.utc)
        m = now.hour * 60 + now.minute
        key = (now.date(), m)
        if (m - offset) % every == 0 and key not in fired:
            fired.add(key)
            print(f"{now:%m-%d %H:%M} UTC  {wf} -> {dispatch(wf)}", flush=True)
        time.sleep(15)
    # agla clock start karo (concurrency group me queue hoga)
    print("next clock ->", dispatch("clock.yml"), flush=True)


if __name__ == "__main__":
    main()
