"""Every reference to a doc of this repository resolves (`docs/README.md`).

A path `docs/<dir>/X.md` names a file that exists, when a doc of that name exists here at
all (one that does not is Vera's, or not written yet). A relative markdown link resolves.
`registry/` and `corpora/` are left out: their text is frozen or hashed (`CLAUDE.md` §6.11).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH_REF = re.compile(r"(?<![\w/.-])(?:\.\./)*docs/([a-z]+)/([A-Z_]+)\.md")
MD_LINK = re.compile(r"\]\((?!https?:|#|mailto:)([^)\s#]+)(?:#[^)]*)?\)")


def _files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    return [
        f
        for f in listed
        if f.endswith((".md", ".py", ".yaml", ".yml", ".toml"))
        and not f.startswith(("registry/", "corpora/"))
        and (ROOT / f).is_file()
    ]


def test_every_doc_path_names_the_file_it_means() -> None:
    here = {p.stem: p.parent.name for p in (ROOT / "docs").glob("*/*.md")}
    wrong = []
    for rel in _files():
        text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for m in PATH_REF.finditer(text):
            folder, name = m[1], m[2]
            if name in here and here[name] != folder:
                wrong.append(f"{rel}: docs/{folder}/{name}.md is docs/{here[name]}/{name}.md")
    assert not wrong, "\n".join(wrong)


def test_every_relative_link_resolves() -> None:
    broken = []
    for rel in _files():
        if not rel.endswith(".md"):
            continue
        path = ROOT / rel
        for m in MD_LINK.finditer(path.read_text(encoding="utf-8", errors="replace")):
            if not (path.parent / m[1]).exists():
                broken.append(f"{rel}: ({m[1]})")
    assert not broken, "\n".join(broken)


def test_every_doc_is_in_the_index() -> None:
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    missing = [
        str(p.relative_to(ROOT / "docs")).replace("\\", "/")
        for p in (ROOT / "docs").glob("*/*.md")
        if str(p.relative_to(ROOT / "docs")).replace("\\", "/") not in index
    ]
    assert not missing, f"docs/README.md does not list {missing}"
