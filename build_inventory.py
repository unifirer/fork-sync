import json
import os
import time
import urllib.request

TOKEN = os.environ["GITLAB_TOKEN"]
HEADERS = {"PRIVATE-TOKEN": TOKEN, "User-Agent": "fork-sync"}
LIMIT = 10 * 1024**3


def get_json(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response), response.headers


def main():
    rows = []
    for page in range(1, 20):
        url = (
            "https://gitlab.com/api/v4/projects?per_page=100"
            f"&page={page}&membership=true&statistics=true"
            "&order_by=id&sort=desc"
        )
        projects, headers = get_json(url)
        if not projects:
            break
        for project in projects:
            source = project.get("import_url") or ""
            size = int(project.get("statistics", {}).get("repository_size") or 0)
            if "github.com" in source and size < LIMIT:
                rows.append(
                    {
                        "gitlab_id": project["id"],
                        "gitlab_path": project["path_with_namespace"],
                        "github_url": source,
                        "repository_size_bytes": size,
                    }
                )
        if len(projects) < 100:
            break
        time.sleep(0.5)
    print(json.dumps(rows))


if __name__ == "__main__":
    main()
