#!/usr/bin/env python3
"""Reconcile a release's Git branch, PR and staging sources independently."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

GH_REPO = "lyra-os-linux/lyraos-desktop-linuxtoys"
UPSTREAM = "psygreg/linuxtoys"
STAGING = "home:rodrigosbrito:lyra:staging"
PACKAGE = "linuxtoys"
FILES = ("_service", "linuxtoys.spec", "linuxtoys.changes",
         "linuxtoys-disable-self-update.patch", "linuxtoys-update-self")
PR_FIELDS = "number,url,state,headRefOid,isCrossRepository,baseRefName,body"
START = "<!-- linuxtoys-staging:start -->"
END = "<!-- linuxtoys-staging:end -->"


def run(*args: str | Path, cwd: Path | None = None) -> str:
    return subprocess.check_output([str(arg) for arg in args], cwd=cwd, text=True).strip()


def log(message: str) -> None:
    print(message, flush=True)


def version(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value):
        raise ValueError(f"unsupported release version: {value!r}")
    return tuple(int(part) for part in value.split("."))


def packaged(repo: Path) -> str:
    match = re.search(r"^Version:\s+(\S+)\s*$", (repo / "linuxtoys.spec").read_text(), re.M)
    if match is None:
        raise ValueError("missing RPM Version")
    version(match[1])
    return match[1]


def gh_json(*args: str) -> object:
    return json.loads(run("gh", *args))


def find_pr(branch: str) -> dict | None:
    prs = gh_json("pr", "list", "--repo", GH_REPO, "--head", branch,
                  "--state", "all", "--limit", "100", "--json", PR_FIELDS)
    if len(prs) >= 100:
        raise ValueError("PR query truncated; review manually")
    prs = [pr for pr in prs if not pr["isCrossRepository"] and pr["baseRefName"] == "main"]
    if len(prs) > 1:
        raise ValueError("multiple PRs for this release; review manually")
    pr = prs[0] if prs else None
    if pr and pr["state"] == "CLOSED":
        raise ValueError("release PR was closed without merging; refusing publication")
    return pr


def branch_exists(repo: Path, branch: str) -> bool:
    result = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", "origin",
                             f"refs/heads/{branch}"], cwd=repo, capture_output=True, text=True)
    if result.returncode not in (0, 2):
        raise RuntimeError(f"cannot query branch: {result.stderr}")
    return result.returncode == 0


def digest(path: Path, algorithm: str) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def prepare(repo: Path, release: dict, latest: str, tarball: Path) -> None:
    checksum = digest(tarball, "sha256")
    tag = release["tag_name"]
    services = ET.Element("services")
    for name, params in [
        ("download_url", {"protocol": "https", "host": "github.com",
                          "path": f"/{UPSTREAM}/releases/download/{tag}/{tarball.name}"}),
        ("verify_file", {"file": f"_service:download_url:{tarball.name}",
                         "verifier": "sha256", "checksum": checksum}),
    ]:
        service = ET.SubElement(services, "service", name=name)
        for key, value in params.items():
            ET.SubElement(service, "param", name=key).text = value
    ET.indent(services)
    (repo / "_service").write_text(ET.tostring(services, encoding="unicode") + "\n")
    spec = repo / "linuxtoys.spec"
    spec.write_text(re.sub(r"^(Version:\s+).*", rf"\g<1>{latest}", spec.read_text(), flags=re.M))
    bullets = []
    for line in (release.get("body") or "").splitlines():
        if line.strip().startswith(("- ", "* ")):
            text = line.strip()[2:].replace("`", "").replace("**", "")
            text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
            bullets.append(textwrap.fill(text, width=75, initial_indent="  * ", subsequent_indent="    "))
    changes = repo / "linuxtoys.changes"
    # strftime's textual fields follow the C locale in a fresh Python process.
    date = datetime.now(timezone.utc).strftime("%a %b %d %H:%M:%S UTC %Y")
    changes.write_text("-" * 67 + f"\n{date} - Lyra OS Release <rodrigo@lyraos.com.br>\n\n"
                       f"- Update to upstream LinuxToys {latest}:\n" + "\n".join(bullets)
                       + "\n- Keep upstream self-update paths disabled in favor of signed RPM updates.\n\n"
                       + changes.read_text())


def release_source(tarball: Path, source: Path) -> Path:
    source.mkdir()
    with tarfile.open(tarball) as archive:
        archive.extractall(source, filter="data")
    entries = list(source.iterdir())
    return entries[0] if len(entries) == 1 and entries[0].is_dir() else source


def validate(repo: Path, work: Path, tarball: Path, latest: str) -> None:
    if packaged(repo) != latest:
        raise ValueError("existing release commit has a different RPM version")
    services = ET.parse(repo / "_service").getroot()
    checksums = services.findall("./service[@name='verify_file']/param[@name='checksum']")
    if len(checksums) != 1 or checksums[0].text != digest(tarball, "sha256"):
        raise ValueError("release tarball does not match the committed checksum")
    if services.findtext("./service[@name='verify_file']/param[@name='verifier']") != "sha256":
        raise ValueError("committed source service does not verify SHA-256")
    if services.findtext("./service[@name='verify_file']/param[@name='file']") != f"_service:download_url:{tarball.name}":
        raise ValueError("committed checksum targets a different tarball")
    source = release_source(tarball, work / "src")
    with (repo / "linuxtoys-disable-self-update.patch").open("rb") as patch:
        subprocess.run(["patch", "-p1", "--dry-run", "-d", str(source)], stdin=patch, check=True)
    log("running packaging contract test and full local RPM build")
    # Do not recursively run the pipeline integration tests from inside a run.
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                    "-p", "test_linuxtoys_packaging.py", "-v"], cwd=repo, check=True)
    top = work / "rpmbuild"
    for directory in ("SOURCES", "SPECS", "BUILD", "RPMS", "SRPMS", "BUILDROOT"):
        (top / directory).mkdir(parents=True, exist_ok=True)
    for path in (tarball, repo / FILES[3], repo / FILES[4]):
        shutil.copyfile(path, top / "SOURCES" / path.name)
    shutil.copyfile(repo / "linuxtoys.spec", top / "SPECS/linuxtoys.spec")
    subprocess.run(["rpmbuild", "--define", f"_topdir {top}", "-bb",
                    str(top / "SPECS/linuxtoys.spec")], check=True)


def manifest(repo: Path) -> dict[str, str]:
    return {name: digest(repo / name, "md5") for name in FILES}


def staging_receipt(expected: dict[str, str]) -> str | None:
    directory = ET.fromstring(run("osc", "api", f"/source/{STAGING}/{PACKAGE}"))
    if directory.tag != "directory" or not directory.get("rev") or not directory.get("srcmd5"):
        raise ValueError("invalid OBS source directory response")
    actual = {entry.attrib["name"]: entry.attrib["md5"] for entry in directory.findall("entry")}
    if any(actual.get(name) != checksum for name, checksum in expected.items()):
        return None
    return f"revision {directory.attrib['rev']}, srcmd5 {directory.attrib['srcmd5']}"


def update_pr(pr: dict, head: str, receipt: str | None, work: Path) -> None:
    state = (f"Sources verified in OBS staging `{STAGING}/{PACKAGE}` ({receipt})."
             if receipt else f"OBS staging `{STAGING}/{PACKAGE}`: pending source publication/verification.")
    block = f"{START}\nGit commit: `{head}`.\n\n{state}\n\nProduction promotion remains manual. This confirms sources only, not build results.\n{END}"
    old = pr["body"] or ""
    # Migrate the previous pipeline's premature success assertion.
    old = re.sub(r"^Also published to OBS staging .*?\n(?:\n)?", "", old, flags=re.M)
    old = old.replace("OBS staging publication is pending verification.\n\n", "")
    if START in old:
        if old.count(START) != 1 or old.count(END) != 1:
            raise ValueError("invalid publication status block in PR")
        body = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, old, flags=re.S)
    else:
        body = old.rstrip() + "\n\n" + block
    if body != pr["body"]:
        body_file = work / "pr-body.md"
        body_file.write_text(body)
        run("gh", "pr", "edit", str(pr["number"]), "--repo", GH_REPO, "--body-file", body_file)


def reconcile(work: Path) -> None:
    repo = work / "repo"
    run("gh", "repo", "clone", GH_REPO, repo, "--", "--quiet")
    run("git", "config", "user.email", "rodrigo@lyraos.com.br", cwd=repo)
    run("git", "config", "user.name", "Lyra OS Release", cwd=repo)
    release_file = work / "release.json"
    run("curl", "--fail", "--silent", "--show-error", "--location", "--output", release_file,
        f"https://api.github.com/repos/{UPSTREAM}/releases/latest")
    release = json.loads(release_file.read_text())
    tag = release["tag_name"]
    latest = tag.removeprefix("v")
    version(latest)  # Also confines branch names, filenames and download URLs.
    current = packaged(repo)
    if version(current) > version(latest):
        log("main is newer than the latest release; not downgrading")
        return
    branch = f"packaging/linuxtoys-{latest}"
    pr = find_pr(branch)
    exists = branch_exists(repo, branch)
    if not exists and not pr and current == latest:
        log("main is up to date; no release branch/PR to resume")
        return
    resumed = exists or pr is not None
    if resumed:
        ref = f"refs/heads/{branch}" if exists else f"refs/pull/{pr['number']}/head"
        run("git", "fetch", "origin", ref, cwd=repo)
        run("git", "checkout", "--detach", "FETCH_HEAD", cwd=repo)
        if pr and run("git", "rev-parse", "HEAD", cwd=repo) != pr["headRefOid"]:
            raise ValueError("branch and PR disagree; refusing to publish a different commit")
    else:
        run("git", "checkout", "-b", branch, cwd=repo)

    # Existing Git history never proves OBS delivery. Compare the five source
    # inputs, including the patch and updater wrapper, against a fresh OBS read.
    receipt = staging_receipt(manifest(repo)) if resumed else None
    if packaged(repo) != latest and resumed:
        raise ValueError("existing branch/PR does not package this release")
    if receipt is None:
        if pr:
            update_pr(pr, run("git", "rev-parse", "HEAD", cwd=repo), None, work)
        tarball = work / f"linuxtoys-{latest}.tar.xz"
        run("curl", "--fail", "--silent", "--show-error", "--location", "--output", tarball,
            f"https://github.com/{UPSTREAM}/releases/download/{tag}/{tarball.name}")
        if not resumed:
            prepare(repo, release, latest, tarball)
        validate(repo, work, tarball, latest)
    if not resumed:
        run("git", "add", "_service", "linuxtoys.spec", "linuxtoys.changes", cwd=repo)
        run("git", "commit", "-m", f"packaging: update LinuxToys to {latest}\n\nValidated tarball checksum, patch, packaging contracts and local RPM build.", cwd=repo)
        run("git", "push", "-u", "origin", branch, cwd=repo)
    head = run("git", "rev-parse", "HEAD", cwd=repo)
    if pr is None:
        body_file = work / "pr-body.md"
        body_file.write_text(f"Automated update: LinuxToys {current} -> {latest}.\n\n"
                             "Packaging contracts and a local RPM build gate any missing source publication.\n\n"
                             "OBS staging publication is pending verification.\n\n"
                             f"Release notes: https://github.com/{UPSTREAM}/releases/tag/{tag}\n")
        run("gh", "pr", "create", "--repo", GH_REPO, "--head", branch, "--base", "main",
            "--title", f"Update LinuxToys to {latest}", "--body-file", body_file)
        pr = find_pr(branch)
        if pr is None:
            raise ValueError("created PR could not be read back")
    # Recheck approval state and commit immediately before the OBS write.
    pr = gh_json("pr", "view", str(pr["number"]), "--repo", GH_REPO, "--json", PR_FIELDS)
    if pr["state"] not in ("OPEN", "MERGED") or pr["headRefOid"] != head:
        raise ValueError("PR closed or changed during validation; refusing publication")
    if receipt is None:
        checkout = work / "obs-staging"
        run("osc", "co", STAGING, PACKAGE, "-o", checkout)
        if version(packaged(checkout)) > version(latest):
            raise ValueError("OBS staging is newer; refusing a downgrade")
        # Another run may have completed between the API read and checkout.
        expected = manifest(repo)
        if not all((checkout / name).is_file() and digest(checkout / name, "md5") == checksum
                   for name, checksum in expected.items()):
            for name in FILES:
                missing = not (checkout / name).exists()
                shutil.copyfile(repo / name, checkout / name)
                if missing:
                    run("osc", "add", name, cwd=checkout)
            run("osc", "commit", "--skip-local-service-run", "-m",
                f"Update LinuxToys to {latest} (Git {head})", *FILES, cwd=checkout)
    receipt = staging_receipt(manifest(repo))
    if receipt is None:
        raise ValueError("OBS sources do not match the release commit; publication remains incomplete")
    update_pr(pr, head, receipt, work)
    log(f"done: {latest} sources verified in {STAGING}/{PACKAGE}, {receipt}; PR: {pr['url']}")


def main() -> int:
    try:
        if sys.version_info < (3, 12):
            raise ValueError("Python 3.12 or newer is required")
        for tool in ("git", "gh", "osc", "curl", "patch", "rpmbuild"):
            if shutil.which(tool) is None:
                raise ValueError(f"required tool '{tool}' not found")
        root = Path(os.environ.get("AUTO_UPDATE_WORKDIR", "/tmp/lyra-linuxtoys-auto-update"))
        root.mkdir(parents=True, exist_ok=True)
        with (root / "run.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Never delete the caller's WORKDIR or another running clone.
            with tempfile.TemporaryDirectory(prefix="run-", dir=root) as temporary:
                reconcile(Path(temporary))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, ET.ParseError,
            tarfile.TarError, KeyError) as error:
        print(f"error: publication incomplete: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
