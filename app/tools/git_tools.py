"""Git tools, restricted to files inside the project's public web root."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from ..project import Project, ProjectError
from .files import fail, ok


MAX_FILES = 200


def _run(repo: str, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _output(proc: subprocess.CompletedProcess) -> str:
    parts = []
    if proc.stdout.strip():
        parts.append(proc.stdout.strip())
    if proc.stderr.strip():
        parts.append(proc.stderr.strip())
    return "\n".join(parts)


def _existing_probe(path: Path) -> Path:
    probe = path if path.is_dir() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe


def _repo_root(path: Path) -> str | None:
    probe = _existing_probe(path)
    if not probe.exists():
        return None
    proc = _run(str(probe), ["rev-parse", "--show-toplevel"], timeout=20)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _normalise_files(raw) -> list[str]:
    if isinstance(raw, str):
        items = [item.strip() for item in re.split(r"[\n,]", raw) if item.strip()]
    elif isinstance(raw, (list, tuple)):
        items = [str(item).strip() for item in raw if str(item).strip()]
    else:
        items = []
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result[:MAX_FILES]


def git_commit(project: Project, args: dict) -> dict:
    files = _normalise_files(args.get("files"))
    message = str(args.get("message") or "").strip()
    if not files:
        return fail("git_commit requires a non-empty 'files' list.")
    if not message:
        return fail("git_commit requires a non-empty commit message.")
    if len(message) > 20_000:
        return fail("Commit message is too long.")

    if not shutil.which("git"):
        return fail("git was not found on PATH.")

    paths: list[Path] = []
    try:
        for item in files:
            paths.append(project.web_path(item))
    except ProjectError as exc:
        return fail(str(exc))

    repos: dict[str, str] = {}
    for path in paths:
        repo = _repo_root(path)
        if not repo:
            return fail(
                f"{project.rel(path)} is not inside a git repository. "
                "Initialize git in the public web directory or one of its parents."
            )
        repos[repo] = repo
    if len(repos) != 1:
        return fail(
            "The specified files belong to different git repositories: "
            + ", ".join(sorted(repos))
            + ". Commit them separately."
        )
    repo = next(iter(repos))
    repo_path = Path(repo).resolve()

    rels: list[str] = []
    for path in paths:
        resolved = path.resolve()
        try:
            rel = resolved.relative_to(repo_path).as_posix()
        except ValueError:
            return fail(
                f"{project.rel(path)} is not inside the git repository at {repo}."
            )
        rels.append(":(literal)" + rel)

    add = _run(repo, ["add", "-A", "--", *rels])
    if add.returncode != 0:
        return fail("git add failed:\n" + _output(add))

    status = _run(repo, ["status", "--porcelain", "--", *rels])
    if status.returncode != 0:
        return fail("git status failed:\n" + _output(status))
    if not status.stdout.strip():
        return fail(
            "No changes to commit for the specified files. "
            "Check the paths or edit the files first."
        )

    commit = _run(repo, ["commit", "-m", message, "--", *rels])
    if commit.returncode != 0:
        return fail("git commit failed:\n" + _output(commit))

    head = _run(repo, ["rev-parse", "--short", "HEAD"], timeout=20)
    short = head.stdout.strip() if head.returncode == 0 else "?"
    committed = _run(
        repo, ["log", "-1", "--format=%h %s", "--", *rels], timeout=20
    ).stdout.strip()
    file_lines = "\n".join(f"  - {project.rel(path)}" for path in paths)
    text = (
        f"Committed {short} in {repo}\n"
        f"Message: {message}\n"
        f"Files:\n{file_lines}"
    )
    if committed:
        text += f"\nLatest commit: {committed}"
    output = _output(commit)
    if output:
        text += "\n\n" + output[:3000]
    return ok(
        text,
        repo=repo,
        commit=short,
        files=[project.rel(path) for path in paths],
    )
