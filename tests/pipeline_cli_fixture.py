#!/usr/bin/env python3
"""Local CLI protocol fixture. Unknown commands fail; never contact services."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

root = Path(os.environ["PIPELINE_FIXTURE"])
state_file = root / "state.json"
state = json.loads(state_file.read_text())
tool = Path(sys.argv[0]).name
args = sys.argv[1:]


def save():
    state_file.write_text(json.dumps(state))


def event(name):
    state["events"].append(name)
    save()


def fail(point):
    if state.get("fail") == point:
        state["fail"] = None
        save()
        print("injected failure: " + point, file=sys.stderr)
        sys.exit(1)


def option(name):
    return args[args.index(name) + 1]


def real_git(*command):
    result = subprocess.run([os.environ["PIPELINE_REAL_GIT"], *command])
    if result.returncode:
        sys.exit(result.returncode)


if tool == "git":
    if args[0] == "ls-remote":
        fail("branch_query")
    real_git(*args)
    if args[0] == "push":
        event("git_pushed")
        fail("after_push")
elif tool == "curl":
    destination = Path(option("--output") if "--output" in args else option("-o"))
    if args[-1].endswith("releases/latest"):
        destination.write_text(json.dumps(state["release"]))
        if "-w" in args:
            print("200", end="")
    else:
        assert args[-1].startswith("https://github.com/psygreg/linuxtoys/releases/download/")
        shutil.copyfile(root / "release.tar.xz", destination)
        event("download")
elif tool in ("patch", "rpmbuild"):
    event(tool)
    fail(tool)
elif tool == "gh" and args[:2] == ["repo", "clone"]:
    assert args[2] == "lyra-os-linux/lyraos-desktop-linuxtoys"
    real_git("clone", "--quiet", str(root / "origin.git"), args[3])
elif tool == "gh" and args[:2] == ["pr", "list"]:
    fail("pr_list")
    if "--jq" in args:
        print(len(state["prs"]))
    else:
        print(json.dumps(state["prs"]))
elif tool == "gh" and args[:2] == ["pr", "create"]:
    fail("pr_create")
    revision = ["--git-dir", str(root / "origin.git"), "rev-parse", "refs/heads/" + option("--head")] if "--head" in args else ["rev-parse", "HEAD"]
    head = subprocess.check_output([os.environ["PIPELINE_REAL_GIT"], *revision], text=True).strip()
    body = Path(option("--body-file")).read_text() if "--body-file" in args else option("--body")
    pr = {"number": 1, "url": "https://example.invalid/pull/1", "state": "OPEN",
          "headRefOid": head, "baseRefName": "main", "isCrossRepository": False, "body": body}
    assert not state["prs"], "duplicate PR created"
    state["prs"].append(pr)
    real_git("--git-dir", str(root / "origin.git"), "update-ref", "refs/pull/1/head", head)
    event("pr_created")
    fail("after_pr")
    print(pr["url"])
elif tool == "gh" and args[:2] == ["pr", "view"]:
    if state.get("close_before_obs"):
        state["prs"][0]["state"] = "CLOSED"
        save()
    print(json.dumps(state["prs"][0]))
elif tool == "gh" and args[:2] == ["pr", "edit"]:
    fail("pr_edit")
    state["prs"][0]["body"] = Path(option("--body-file")).read_text()
    event("pr_edited")
    fail("after_pr_edit")
elif tool == "osc" and args[0] == "api":
    assert args[1] == "/source/home:rodrigosbrito:lyra:staging/linuxtoys"
    fail("obs_read")
    directory = ET.Element("directory", rev=str(state["revision"]), srcmd5="fixture-source-md5")
    for path in sorted((root / "obs").iterdir()):
        ET.SubElement(directory, "entry", name=path.name, md5=hashlib.md5(path.read_bytes()).hexdigest())
    print(ET.tostring(directory, encoding="unicode"))
elif tool == "osc" and args[0] == "co":
    assert args[1:3] == ["home:rodrigosbrito:lyra:staging", "linuxtoys"]
    if state.get("complete_before_checkout"):
        head = state["prs"][0]["headRefOid"]
        for name in ("_service", "linuxtoys.spec", "linuxtoys.changes", "linuxtoys-disable-self-update.patch", "linuxtoys-update-self"):
            content = subprocess.check_output([os.environ["PIPELINE_REAL_GIT"], "--git-dir", str(root / "origin.git"), "show", f"{head}:{name}"])
            (root / "obs" / name).write_bytes(content)
        state["revision"] += 1
        state["complete_before_checkout"] = False
        event("concurrent_delivery")
    shutil.copytree(root / "obs", option("-o"))
elif tool == "osc" and args[0] == "add":
    event("obs_add")
elif tool == "osc" and args[0] == "commit":
    fail("osc_commit")
    for name in ("_service", "linuxtoys.spec", "linuxtoys.changes", "linuxtoys-disable-self-update.patch", "linuxtoys-update-self"):
        if Path(name).exists():
            shutil.copyfile(name, root / "obs" / name)
    state["revision"] += 1
    event("obs_committed")
    if state.get("fail") == "corrupt_obs":
        state["fail"] = None
        save()
        (root / "obs/linuxtoys.changes").write_text("wrong sources")
    fail("after_osc")
else:
    raise SystemExit(f"unsupported fixture command: {tool} {args}")
