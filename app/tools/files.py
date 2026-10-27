"""File-oriented tools with project-root sandboxing."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from ..project import Project, ProjectError


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".aibuilder",
    "node_modules",
    "vendor",
    "__pycache__",
    ".idea",
    ".vscode",
    ".cache",
    "dist",
    "build",
}
INTERNAL_FILES = {"chat_history.json", "project.json"}
MAX_FILE_BYTES = 2_000_000


def ok(text: str, **meta) -> dict:
    return {"ok": True, "text": text, "images": [], "meta": meta}


def fail(text: str) -> dict:
    return {"ok": False, "text": text, "images": [], "meta": {}}


def _check_writable(project: Project, path: Path) -> None:
    try:
        rel = path.relative_to(project.root)
    except ValueError:
        raise ProjectError("Path is outside the project.") from None
    parts = rel.parts
    if parts and parts[0] == Project.STATE_DIRNAME:
        raise ProjectError("The .aibuilder directory is managed by the app and is read-only.")
    if len(parts) == 1 and rel.name in INTERNAL_FILES:
        raise ProjectError(f"'{rel.name}' is managed by the app and is read-only.")
    if path.name.endswith((".tmp",)) and len(parts) == 1:
        raise ProjectError("Temporary app files are read-only.")


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ProjectError(
            f"File is too large to read ({path.stat().st_size} bytes). Use start_line/end_line."
        )
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ProjectError("File appears to be binary.")
    return raw.decode("utf-8", errors="replace")


def read_file(project: Project, args: dict) -> dict:
    try:
        path = project.safe_path(args.get("path", ""))
        if not path.exists():
            return fail(f"File not found: {project.rel(path)}")
        if path.is_dir():
            return fail(f"'{project.rel(path)}' is a directory; use list_files.")
        text = _read_text(path)
        lines = text.splitlines()
        total = len(lines)
        start = max(1, int(args.get("start_line") or 1))
        end_arg = args.get("end_line")
        end = min(total, int(end_arg)) if end_arg else min(total, start + 2499)
        if start > total:
            return fail(f"start_line {start} is past the end of the file ({total} lines).")
        selected = lines[start - 1:end]
        numbered = "\n".join(f"{start + i:>5}| {line}" for i, line in enumerate(selected))
        header = f"File: {project.rel(path)}  ({total} lines total)"
        if end < total:
            header += f"\nShowing lines {start}-{end}. Use start_line/end_line for more."
        if start > 1:
            header += f"\n(line numbers shown on the left are not part of the file)"
        return ok(header + "\n" + numbered, path=project.rel(path), lines=total, truncated=end < total)
    except (ProjectError, OSError) as exc:
        return fail(str(exc))


def write_file(project: Project, args: dict) -> dict:
    try:
        path = project.safe_path(args.get("path", ""))
        _check_writable(project, path)
        content = str(args.get("content") or "")
        if len(content) > 4_000_000:
            return fail("Content is too large (over 4 MB).")
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        action = "Overwrote" if existed else "Created"
        return ok(
            f"{action} {project.rel(path)} ({len(content)} bytes, {line_count} lines).",
            path=project.rel(path),
            lines=line_count,
        )
    except (ProjectError, OSError) as exc:
        return fail(str(exc))


def edit_file(project: Project, args: dict) -> dict:
    try:
        path = project.safe_path(args.get("path", ""))
        _check_writable(project, path)
        if not path.exists():
            return fail(f"File not found: {project.rel(path)}")
        old = str(args.get("old_string") or "")
        new = str(args.get("new_string") or "")
        if not old:
            return fail("old_string must not be empty. Use write_file to create a file.")
        if old == new:
            return fail("old_string and new_string are identical.")
        original = _read_text(path)
        content = original
        count = content.count(old)
        used_old, used_new = old, new
        if count == 0 and "\r\n" in content:
            alt_old = old.replace("\r\n", "\n").replace("\n", "\r\n")
            alt_new = new.replace("\r\n", "\n").replace("\n", "\r\n")
            alt_count = content.count(alt_old)
            if alt_count:
                count = alt_count
                used_old, used_new = alt_old, alt_new
        if count == 0:
            return fail(
                "old_string was not found. Copy the exact text from read_file, including "
                "whitespace and indentation. The line-number prefix must not be included."
            )
        replace_all = bool(args.get("replace_all"))
        if count > 1 and not replace_all:
            positions = [m.start() for m in re.finditer(re.escape(used_old), content)]
            line_numbers = [content.count("\n", 0, pos) + 1 for pos in positions[:20]]
            return fail(
                f"old_string matches {count} times (lines {', '.join(map(str, line_numbers))}). "
                f"Add more surrounding context to make it unique, or pass replace_all=true."
            )
        updated = content.replace(used_old, used_new) if replace_all else content.replace(used_old, used_new, 1)
        path.write_text(updated, encoding="utf-8", newline="")
        first_pos = content.find(used_old)
        line_no = content.count("\n", 0, first_pos) + 1 if first_pos >= 0 else 1
        context = _context_snippet(updated, line_no, 3)
        return ok(
            f"Edited {project.rel(path)}: replaced {count if replace_all else 1} occurrence(s) "
            f"at line {line_no}.\n{context}",
            path=project.rel(path),
            replacements=count if replace_all else 1,
        )
    except (ProjectError, OSError) as exc:
        return fail(str(exc))


def _context_snippet(text: str, line_no: int, radius: int = 3) -> str:
    lines = text.splitlines()
    start = max(1, line_no - radius)
    end = min(len(lines), line_no + radius)
    return "\n".join(f"{i:>5}| {lines[i - 1]}" for i in range(start, end + 1))


def _include_matches(name: str, rel: str, include: str | None) -> bool:
    if not include:
        return True
    patterns = [include]
    if "{" in include and "}" in include:
        prefix, rest = include.split("{", 1)
        middle, suffix = rest.split("}", 1)
        patterns = [prefix + part.strip() + suffix for part in middle.split(",")]
    return any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern)
        for pattern in patterns
    )


def _iter_files(project: Project, base: Path, include: str | None = None):
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            try:
                rel = path.relative_to(project.root).as_posix()
            except ValueError:
                continue
            if not _include_matches(name, rel, include):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            yield path, rel


def grep(project: Project, args: dict) -> dict:
    pattern = str(args.get("pattern") or "")
    if not pattern:
        return fail("pattern must not be empty.")
    case_sensitive = bool(args.get("case_sensitive", True))
    use_regex = bool(args.get("regex", True))
    include = args.get("include") or None
    context_lines = max(0, min(8, int(args.get("context_lines") or 0)))
    max_results = max(1, min(500, int(args.get("max_results") or 100)))
    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern if use_regex else re.escape(pattern), flags)
    except re.error as exc:
        return fail(f"Invalid regular expression: {exc}")

    try:
        base = project.safe_path(args.get("path") or ".")
    except ProjectError as exc:
        return fail(str(exc))
    if not base.exists():
        return fail(f"Path not found: {project.rel(base)}")
    if base.is_file():
        files = [(base, project.rel(base))]
    else:
        files = list(_iter_files(project, base, include))

    matches: list[str] = []
    total = 0
    scanned = 0
    for path, rel in files:
        scanned += 1
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:4096]:
            continue
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if regex.search(line):
                total += 1
                if len(matches) < max_results:
                    if context_lines:
                        start = max(0, index - context_lines)
                        end = min(len(lines), index + context_lines + 1)
                        for j in range(start, end):
                            marker = ":" if j == index else "-"
                            snippet = lines[j][:500]
                            matches.append(f"{rel}{marker}{j + 1}{marker}{snippet}")
                        matches.append("--")
                    else:
                        matches.append(f"{rel}:{index + 1}: {line[:500]}")
    if not matches:
        return ok(f"No matches for {pattern!r} in {scanned} file(s).", matches=0, scanned=scanned)
    header = f"{total} match(es) for {pattern!r} in {scanned} file(s):"
    if total > max_results:
        header += f" (showing first {max_results})"
    return ok(header + "\n" + "\n".join(matches), matches=total, scanned=scanned)


def search(project: Project, args: dict) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        return fail("query must not be empty.")
    try:
        base = project.safe_path(args.get("path") or ".")
    except ProjectError as exc:
        return fail(str(exc))
    if not base.exists():
        return fail(f"Path not found: {project.rel(base)}")
    include = args.get("include") or None
    max_results = max(1, min(1000, int(args.get("max_results") or 200)))
    has_glob = any(ch in query for ch in "*?[")
    needle = query.lower()
    results: list[str] = []

    def matches(name: str, rel: str) -> bool:
        if has_glob:
            return fnmatch.fnmatch(name, query) or fnmatch.fnmatch(rel, query)
        return needle in name.lower() or needle in rel.lower()

    if base.is_file():
        return ok(f"file: {project.rel(base)}", results=1)

    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for dirname in list(dirnames):
            path = Path(dirpath) / dirname
            rel = path.relative_to(project.root).as_posix()
            if matches(dirname, rel) and len(results) < max_results:
                results.append(rel + "/")
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(project.root).as_posix()
            if not _include_matches(name, rel, include):
                continue
            if matches(name, rel) and len(results) < max_results:
                results.append(rel)
    if not results:
        return ok(f"No files or folders matching {query!r}.")
    return ok(
        f"{len(results)} result(s) matching {query!r}:\n" + "\n".join(results),
        results=len(results),
    )


def list_files(project: Project, args: dict) -> dict:
    try:
        base = project.safe_path(args.get("path") or ".")
    except ProjectError as exc:
        return fail(str(exc))
    if not base.exists():
        return fail(f"Path not found: {project.rel(base)}")
    if base.is_file():
        return ok(f"file: {project.rel(base)}")
    depth = max(1, min(6, int(args.get("depth") or 2)))
    max_entries = max(1, min(800, int(args.get("max_entries") or 300)))
    root_depth = len(base.parts)
    entries: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        current = Path(dirpath)
        level = len(current.parts) - root_depth
        if level >= depth:
            dirnames[:] = []
        rel_dir = current.relative_to(project.root).as_posix()
        indent = "  " * level
        if current != base:
            entries.append(f"{indent}{current.name}/")
        for name in sorted(filenames):
            if len(entries) >= max_entries:
                truncated = True
                break
            if name.startswith(".") and name not in (".htaccess", ".gitignore"):
                continue
            entries.append(f"{indent}  {name}")
        if truncated:
            break
    text = f"Listing {project.rel(base)} (depth {depth}):\n" + "\n".join(entries)
    if truncated:
        text += f"\n… listing truncated at {max_entries} entries."
    return ok(text, entries=len(entries))
