import json
import os
import time
import urllib.parse
import urllib.request

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("SYNC_PAT")
GITLAB_TOKEN = os.environ["GITLAB_TOKEN"]
GITHUB_USER = os.environ.get("GITHUB_USER", "unifirer")
GITLAB_NAMESPACE = os.environ.get("GITLAB_NAMESPACE", "uni.universefire")
LIMIT_BYTES = 10 * 1024**3
MARKER = "fork-sync:"


def api_get(url, headers):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response), response.headers


def github_key(url):
    value = (url or "").strip().lower()
    if not value:
        return ""
    value = value.replace("git@github.com:", "https://github.com/")
    parts = urllib.parse.urlparse(value)
    path = parts.path if parts.scheme else value.split("github.com/", 1)[-1]
    segments = [part for part in path.strip("/").split("/") if part]
    if len(segments) < 2:
        return ""
    return f"{segments[0]}/{segments[1].removesuffix('.git')}"


def github_forks():
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN or SYNC_PAT is required")
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "fork-sync",
    }
    forks = []
    for page in range(1, 101):
        url = (
            "https://api.github.com/user/repos?per_page=100"
            f"&page={page}&affiliation=owner"
            "&sort=created&direction=desc"
        )
        batch, _ = api_get(url, headers)
        if not batch:
            break
        for repo in batch:
            if not repo.get("fork"):
                continue
            size_bytes = int(repo.get("size") or 0) * 1024
            if size_bytes >= LIMIT_BYTES:
                continue
            forks.append(
                {
                    "name": repo["name"],
                    "full_name": repo["full_name"],
                    "owner": repo["owner"]["login"],
                    "clone_url": repo["clone_url"],
                    "size_bytes": size_bytes,
                }
            )
        if len(batch) < 100:
            break
        time.sleep(0.2)
    return forks


def gitlab_projects():
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN, "User-Agent": "fork-sync"}
    by_source = {}
    by_path = {}
    for page in range(1, 101):
        url = (
            "https://gitlab.com/api/v4/projects?per_page=100"
            f"&page={page}&membership=true&statistics=true"
            "&order_by=id&sort=desc"
        )
        batch, _ = api_get(url, headers)
        if not batch:
            break
        for project in batch:
            by_path[project["path_with_namespace"].lower()] = project
            key = github_key(project.get("import_url"))
            if key:
                by_source[key] = project
        if len(batch) < 100:
            break
        time.sleep(0.2)
    return by_source, by_path


def is_ours(project, key):
    description = project.get("description") or ""
    if description.startswith(MARKER):
        return True
    return github_key(project.get("import_url")) == key


def choose_gitlab_path(fork, by_path, used):
    base = f"{GITLAB_NAMESPACE}/{fork['name']}"
    candidates = [base, f"{base}-{fork['owner']}"]
    candidates.extend(f"{base}-{fork['owner']}-{index}" for index in range(2, 20))
    for candidate in candidates:
        lowered = candidate.lower()
        if lowered in used:
            continue
        project = by_path.get(lowered)
        if project is None:
            return candidate, None
        key = github_key(fork["clone_url"])
        if is_ours(project, key):
            return project["path_with_namespace"], project
    raise RuntimeError(f"no free GitLab path for {fork['full_name']}")


def main():
    forks = github_forks()
    by_source, by_path = gitlab_projects()
    used = set()
    rows = []
    for fork in forks:
        key = github_key(fork["clone_url"])
        project = by_source.get(key)
        if project is None:
            candidate = by_path.get(f"{GITLAB_NAMESPACE}/{fork['name']}".lower())
            if candidate is not None and is_ours(candidate, key):
                project = candidate
        if project is None:
            path, project = choose_gitlab_path(fork, by_path, used)
        else:
            path = project["path_with_namespace"]
        used.add(path.lower())
        rows.append(
            {
                "github_full_name": fork["full_name"],
                "github_url": fork["clone_url"],
                "gitlab_id": project["id"] if project else None,
                "gitlab_path": path,
                "repository_size_bytes": fork["size_bytes"],
            }
        )
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
