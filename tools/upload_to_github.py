# -*- coding: utf-8 -*-
"""网络无法直连 github.com 时，用 GitHub API 上传/更新仓库内容。

用法：
    python tools/upload_to_github.py --owner <用户名> --repo <仓库名> --message "说明"
前置：安装 GitHub CLI 并 gh auth login（脚本用 gh auth token 取令牌）
"""
import argparse, base64, json, os, subprocess, sys, urllib.error, urllib.request

API = "https://api.github.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IGNORE_DIRS = {".git", "build", "dist", "__pycache__", "译文输出", "_uploads", "tools_tmp"}
IGNORE_FILES = {"设置.json", "历史记录.json", "pdf工具.log", "shim_requests.log"}
IGNORE_EXT = {".pyc", ".spec", ".bak"}


def token():
    for cmd in (["gh", "auth", "token"], [r"C:\Program Files\GitHub CLI\gh.exe", "auth", "token"]):
        try:
            t = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
            if t:
                return t
        except Exception:
            continue
    sys.exit("未找到令牌：请先安装 GitHub CLI 并执行 gh auth login")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default="chore: update")
    a = ap.parse_args()

    tk = token()

    def call(method, path, payload=None, ok=()):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(API + path, data=data, method=method, headers={
            "Authorization": "Bearer " + tk, "Accept": "application/vnd.github+json",
            "Content-Type": "application/json", "User-Agent": "upload"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            if e.code in ok:
                return {"_error": e.code, "_body": body}
            raise SystemExit("HTTP %s %s: %s" % (e.code, path, body))

    files = []
    for root, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for n in names:
            if n in IGNORE_FILES or os.path.splitext(n)[1].lower() in IGNORE_EXT:
                continue
            full = os.path.join(root, n)
            files.append((os.path.relpath(full, ROOT).replace("\\", "/"), full))

    ref = call("GET", "/repos/%s/%s/git/ref/heads/%s" % (a.owner, a.repo, a.branch))
    parent = ref["object"]["sha"]
    base_tree = call("GET", "/repos/%s/%s/git/commits/%s" % (a.owner, a.repo, parent))["tree"]["sha"]

    tree = []
    for rel, full in files:
        raw = open(full, "rb").read()
        blob = call("POST", "/repos/%s/%s/git/blobs" % (a.owner, a.repo),
                    {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print("  +", rel)

    new_tree = call("POST", "/repos/%s/%s/git/trees" % (a.owner, a.repo),
                    {"base_tree": base_tree, "tree": tree})
    commit = call("POST", "/repos/%s/%s/git/commits" % (a.owner, a.repo),
                  {"message": a.message, "tree": new_tree["sha"], "parents": [parent]})
    call("PATCH", "/repos/%s/%s/git/refs/heads/%s" % (a.owner, a.repo, a.branch),
         {"sha": commit["sha"], "force": True})
    print("done:", commit["sha"][:8], "->", "https://github.com/%s/%s" % (a.owner, a.repo))


if __name__ == "__main__":
    main()
