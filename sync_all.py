import json
import os
import subprocess
import sys
import time
from pathlib import Path

TOKEN = os.environ["GITLAB_TOKEN"]
TIMEOUT = 900
WORK = Path("repos")
SKIP = set()
if Path("skip.txt").exists():
    SKIP = {line.strip() for line in Path("skip.txt").read_text().splitlines() if line.strip()}


def run(args, cwd=None, timeout=TIMEOUT):
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
        )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"


def main():
    inventory = json.loads(Path(sys.argv[1]).read_text())
    ok = 0
    failed = 0
    for repo in inventory:
        rid = str(repo["gitlab_id"])
        if rid in SKIP:
            continue
        name = repo["github_url"].rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        path = WORK / name
        gitlab = f"https://oauth2:{TOKEN}@gitlab.com/{repo['gitlab_path']}.git"

        if not (path / "HEAD").exists():
            if path.exists():
                subprocess.run(["rm", "-rf", str(path)], check=False)
            good, output = run(["git", "clone", "--mirror", "--quiet", repo["github_url"], str(path)])
            if not good:
                print(f"FAIL {rid} {repo['gitlab_path']} clone: {output[:200]}", flush=True)
                failed += 1
                continue

        good, output = run(["git", "-C", str(path), "fetch", "--prune", "--quiet", "origin"])
        if not good:
            print(f"FAIL {rid} {repo['gitlab_path']} fetch: {output[:200]}", flush=True)
            failed += 1
            continue

        good, output = run(["git", "-C", str(path), "push", "--mirror", "--force", "--quiet", gitlab])
        if not good:
            print(f"FAIL {rid} {repo['gitlab_path']} push: {output[:200]}", flush=True)
            failed += 1
            continue

        ok += 1
        print(f"OK {rid} {repo['gitlab_path']}", flush=True)

    print(f"DONE ok={ok} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
