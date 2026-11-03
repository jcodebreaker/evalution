"""What is watched of a project: the same rules from git and from a walk, whatever its layout."""

import subprocess

import pytest

from evalution.code import Code, CodeError
from evalution.config import CodeConfig

WATCHED = {
    "README.md": "# app",
    "services/core/compaction.py": "x = 1",
    "web/src/App.tsx": "x",
}
LEFT_OUT = {
    ".env": "KEY=1",
    "config/credentials.json": "{}",
    "deploy/server.pem": "k",
    "tests/test_core.py": "x",
    "services/core/core_test.go": "x",
    "web/src/App.test.tsx": "x",
    "uv.lock": "x",
    "web/package-lock.json": "{}",
    "dist/bundle.js": "x",
    "node_modules/lib/index.js": "x",
    "web/app.min.js": "x",
    "assets/logo.png": "x",
    "services/skipme/generated.py": "x",
    ".github/workflows/ci.yml": "x",
    ".aws/settings.txt": "x",
}


def _project(folder):
    for rel, text in {**WATCHED, **LEFT_OUT}.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text)
    (folder / "services" / "core" / "blob.dat").write_bytes(b"\0" * 10)
    (folder / "services" / "core" / "huge.py").write_text("x" * 1_000_001)
    return Code(CodeConfig(folder, ("services/skipme/**",)))


def _git(folder, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize("checkout", [False, True])
def test_every_source_file_is_watched_and_nothing_else(tmp_path, checkout) -> None:
    code = _project(tmp_path)
    if checkout:
        (tmp_path / ".gitignore").write_text("private.py\ndist/\n")
        (tmp_path / "private.py").write_text("token = 1")
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "add", "-f", "README.md", "services/core/compaction.py", ".env")
        _git(tmp_path, "commit", "-qm", "first")
        # Untracked and not ignored counts; a tracked secrets file still never does.
        (tmp_path / "services" / "core" / "memory.py").write_text("y = 1")
    seen = set(code.hashes())
    expected = set(WATCHED) | ({"services/core/memory.py"} if checkout else set())
    assert seen == expected
    with pytest.raises(CodeError):
        code.read(".env")
    if checkout:
        with pytest.raises(CodeError):
            code.read("private.py")
        (tmp_path / "services" / "core" / "compaction.py").write_text("x = 2")
        (tmp_path / ".env").write_text("KEY=2")
        commit = code.commit()
        text = code.diff(commit)
        assert "x = 2" in text and "--- new file services/core/memory.py" in text
        assert "KEY" not in text
        assert "memory.py" not in code.diff(commit, {"services/core/compaction.py"})
        assert code.diff(commit, {"nothing.py"}) == "(no watched file changed)"


def test_a_checkout_watches_agent_instructions_in_hidden_folders(tmp_path) -> None:
    code = _project(tmp_path)
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "reviewer.md").write_text("x")
    assert ".agents/reviewer.md" not in code.files(100)
    _git(tmp_path, "init", "-q")
    assert ".agents/reviewer.md" in code.files(100)
