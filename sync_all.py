import json
import os
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

TOKEN = os.environ["GITLAB_TOKEN"]
GITLAB_NAMESPACE = os.environ.get("GITLAB_NAMESPACE", "uni.universefire")
TIMEOUT = 900
WORK = Path("repos")
SKIP = set()
if Path("skip.txt").exists():
    SKIP = {line.strip() for line in Path("skip.txt").read_text().splitlines() if line.strip()}


def api_request(method, url, payload=None):
    import urllib.request

    data = None
    headers = {"PRIVATE-TOKEN": TOKEN, "User-Agent": "fork-sync"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read().decode()
        return json.loads(body) if body else None


def ensure_project(repo):
    if repo.get("gitlab_id"):
        return repo
    path = repo["gitlab_path"]
    source = repo["github_url"]
    name = source.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    namespace, slug = path.split("/", 1)
    namespace_id = api_request("GET", f"https://gitlab.com/api/v4/namespaces/{urllib.parse.quote(namespace, safe='')}")["id"]
    project = api_request(
        "POST",
        "https://gitlab.com/api/v4/projects",
        {
            "name": name,
            "path": slug,
            "namespace_id": namespace_id,
            "description": "fork-sync:mirrored from GitHub",
            "visibility": "private",
            "initialize_with_readme": False,
        },
    )
    repo["gitlab_id"] = project["id"]
    repo["gitlab_path"] = project["path_with_namespace"]
    return repo


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
        repo = ensure_project(repo)
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
