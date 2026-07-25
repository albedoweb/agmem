"""First-run E2E — the paths a brand-new user hits in the first 15 minutes.

No mocks: real tmp dir, real `.agmem/`, real index. These would have caught
the dead init-guard (read_config() never raises, so the "Not initialized"
hint never fired) and the hidden-ancestor bug (a repo under ~/.dotfiles/x
indexed 0 files because _should_skip judged absolute path components).
"""

from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from agmem.cli import app

runner = CliRunner()


@pytest.fixture()
def fresh_repo(tmp_path, monkeypatch):
    """Empty git repo in a tmp dir, cwd switched into it."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestUninitializedGuard:
    """Every data command must exit 1 with the init hint before `agmem init`."""

    @pytest.mark.parametrize("argv", [
        ["context", "anything"],
        ["remember", "some fact"],
        ["list"],
        ["recall", "anything"],
    ])
    def test_hint_fires(self, fresh_repo, argv):
        result = runner.invoke(app, argv)
        assert result.exit_code == 1
        assert "Not initialized" in result.output
        # And nothing was silently created.
        assert not (fresh_repo / ".agmem" / "memories.jsonl").exists()


class TestInitIdempotent:
    def test_first_init_creates_config(self, fresh_repo):
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert (fresh_repo / ".agmem" / "config.yaml").exists()

    def test_second_init_is_not_an_error(self, fresh_repo):
        assert runner.invoke(app, ["init"]).exit_code == 0
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "already initialized" in result.output


class TestInitIndexContext:
    """The quickstart from README, end to end."""

    def test_happy_path(self, fresh_repo):
        (fresh_repo / "billing.py").write_text(
            'def compute_invoice_total(items):\n    """Sum line items."""\n    return sum(items)\n',
            encoding="utf-8",
        )
        assert runner.invoke(app, ["init"]).exit_code == 0
        assert runner.invoke(app, ["index"]).exit_code == 0
        result = runner.invoke(app, ["context", "compute_invoice_total billing"])
        assert result.exit_code == 0
        assert "billing.py" in result.output

    def test_remember_then_context(self, fresh_repo):
        assert runner.invoke(app, ["init"]).exit_code == 0
        assert runner.invoke(
            app, ["remember", "Always use UTC in billing timestamps", "--kind", "rule"]
        ).exit_code == 0
        result = runner.invoke(app, ["context", "billing timestamps UTC"])
        assert result.exit_code == 0
        assert "UTC" in result.output


class TestAgmemDirHygiene:
    """`.agmem/` may be committed by choice — runtime files must never be."""

    def test_init_ships_runtime_gitignore(self, fresh_repo):
        assert runner.invoke(app, ["init"]).exit_code == 0
        gi = fresh_repo / ".agmem" / ".gitignore"
        assert gi.exists()
        body = gi.read_text()
        for runtime in ("_hot.md", "_ask_session.json", "embeddings/"):
            assert runtime in body

    def test_reindex_of_unchanged_repo_is_a_noop_diff(self, fresh_repo):
        """Unchanged content ⇒ byte-identical memories.jsonl, so a committed
        .agmem/ doesn't produce a full-file diff after every reindex."""
        (fresh_repo / "app.py").write_text("def main():\n    pass\n", encoding="utf-8")
        assert runner.invoke(app, ["init"]).exit_code == 0
        assert runner.invoke(app, ["index"]).exit_code == 0
        store = fresh_repo / ".agmem" / "memories.jsonl"
        first = store.read_bytes()
        assert runner.invoke(app, ["index"]).exit_code == 0
        assert store.read_bytes() == first


class TestHiddenAncestorRepo:
    """A repo living UNDER a dot-directory (~/.config/nvim, ~/.dotfiles/x)
    must index normally — only root-RELATIVE dot components are skipped."""

    def test_repo_under_hidden_dir_indexes_files(self, tmp_path, monkeypatch):
        repo = tmp_path / ".dotfiles" / "myrepo"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / "config_loader.py").write_text(
            "def load_settings():\n    return {}\n", encoding="utf-8"
        )
        monkeypatch.chdir(repo)
        assert runner.invoke(app, ["init"]).exit_code == 0
        assert runner.invoke(app, ["index"]).exit_code == 0
        result = runner.invoke(app, ["context", "load_settings config_loader"])
        assert result.exit_code == 0
        assert "config_loader.py" in result.output

    def test_dot_dirs_inside_repo_still_skipped(self, fresh_repo):
        hidden = fresh_repo / ".secrets_dir"
        hidden.mkdir()
        (hidden / "creds.py").write_text("API_KEY = 'x'\n", encoding="utf-8")
        (fresh_repo / "app.py").write_text("def main():\n    pass\n", encoding="utf-8")
        assert runner.invoke(app, ["init"]).exit_code == 0
        assert runner.invoke(app, ["index"]).exit_code == 0
        result = runner.invoke(app, ["list"])
        assert "app.py" in result.output
        assert "creds.py" not in result.output
