import json
import os
import shutil
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
    import urllib.error
    import urllib.request

    data = None
    headers = {"PRIVATE-TOKEN": TOKEN, "User-Agent": "fork-sync"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(f"{method} {url}: HTTP {exc.code} {detail}")


def namespace_id(path):
    namespace = path.split("/", 1)[0]
    quoted = urllib.parse.quote(namespace, safe="")
    return api_request("GET", f"https://gitlab.com/api/v4/namespaces/{quoted}")["id"]


def create_project(path, name, description):
    slug = path.split("/", 1)[1]
    return api_request(
        "POST",
        "https://gitlab.com/api/v4/projects",
        {
            "name": name,
            "path": slug,
            "namespace_id": namespace_id(path),
            "description": description,
            "visibility": "private",
            "initialize_with_readme": True,
        },
    )


def initialize_empty_project(project):
    """A GitLab project with no repository cannot accept git push. Seed it."""
    pid = project["id"]
    body = {
        "branch": "main",
        "commit_message": "Initialize repository for fork-sync",
        "actions": [
            {"action": "create", "file_path": "fork-sync-init.txt", "content": "initialized\n"}
        ],
    }
    try:
        api_request("POST", f"https://gitlab.com/api/v4/projects/{pid}/repository/commits", body)
        return project
    except RuntimeError:
        pass
    # GitLab can leave a project with no provisioned git repository. Recreate it.
    path = project["path_with_namespace"]
    name = project["name"]
    description = project.get("description") or "fork-sync:mirrored from GitHub"
    api_request("DELETE", f"https://gitlab.com/api/v4/projects/{pid}")
    time.sleep(2)
    project = create_project(path, name, description)
    unprotect_default_branch(project)
    return project


def unprotect_default_branch(project):
    """New projects protect main; a mirror push must overwrite it."""
    pid = project["id"]
    try:
        branches = api_request("GET", f"https://gitlab.com/api/v4/projects/{pid}/protected_branches")
    except RuntimeError:
        return
    for branch in branches or []:
        try:
            api_request("DELETE", f"https://gitlab.com/api/v4/projects/{pid}/protected_branches/{branch['name']}")
        except RuntimeError:
            pass


def ensure_project(repo):
    source = repo["github_url"]
    name = source.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    description = "fork-sync:mirrored from GitHub"
    if repo.get("gitlab_id"):
        project = api_request("GET", f"https://gitlab.com/api/v4/projects/{repo['gitlab_id']}?statistics=true")
        if project.get("empty_repo"):
            project = initialize_empty_project(project)
    else:
        project = create_project(repo["gitlab_path"], name, description)
        unprotect_default_branch(project)
    repo["gitlab_id"] = project["id"]
    repo["gitlab_path"] = project["path_with_namespace"]
    return repo


def sync_lfs(path, gitlab):
    good, output = run(["git", "-C", str(path), "lfs", "fetch", "--all", "origin"], timeout=3600)
    if not good:
        return False, f"lfs fetch: {output[:200]}"
    good, output = run(["git", "-C", str(path), "lfs", "push", "--all", gitlab], timeout=3600)
    if not good:
        return False, f"lfs push: {output[:200]}"
    return True, ""


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
        return result.returncode == 0, result.stdout.replace(TOKEN, "***").strip()
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"


def main():
    if not shutil.which("git-lfs"):
        raise RuntimeError("git-lfs is required; add git-lfs/setup action")
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
        if not good and "LFS objects are missing" in output:
            good_lfs, lfs_error = sync_lfs(path, gitlab)
            if good_lfs:
                good, output = run(["git", "-C", str(path), "push", "--mirror", "--force", "--quiet", gitlab])
            else:
                output = lfs_error
        if not good:
            print(f"FAIL {rid} {repo['gitlab_path']} push: {output[:200]}", flush=True)
            failed += 1
            continue

        ok += 1
        print(f"OK {rid} {repo['gitlab_path']}", flush=True)

    print(f"DONE ok={ok} failed={failed}", flush=True)


if __name__ == "__main__":
    main()
