"""CLI adapter tests — the argument handling the test suite cannot reach through HTTP."""

from __future__ import annotations

from pathlib import Path

import pytest

from fw.cli.main import main
from fw.core.world import World


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Run the CLI from a directory holding a world at the default path."""
    monkeypatch.chdir(tmp_path)
    world = World.create(tmp_path / "world.fwworld", name="CLI world")
    yield world
    world.close()


class TestRestoreCommand:
    def test_a_bare_revision_id_is_not_mistaken_for_a_path(self, home, capsys):
        """`fw restore 123` must restore revision 123, not open a file named 123."""
        e = home.add_entity("settlement", "Lost")
        home.delete_entity(e.id)
        revision = home.recently_deleted()[0]["revision_id"]
        home.close()

        main(["restore", str(revision)])
        assert "Restored Lost" in capsys.readouterr().out
        reopened = World.open("world.fwworld")
        assert reopened.entity_named("Lost") is not None
        reopened.close()

    def test_a_refusal_is_a_message_not_a_traceback(self, home, capsys):
        home.close()
        with pytest.raises(SystemExit) as excinfo:
            main(["restore", "999999"])
        assert "no revision" in str(excinfo.value)

    def test_list_shows_what_can_come_back(self, home, capsys):
        e = home.add_entity("settlement", "Gone")
        home.delete_entity(e.id)
        home.close()
        main(["restore", "--list"])
        assert "Gone" in capsys.readouterr().out

    def test_list_with_a_numeric_path_is_not_hijacked(self, tmp_path, monkeypatch,
                                                      capsys):
        """`fw restore 2024 --list` names a world *file* called 2024 — the bare-number
        convenience must not rewrite it into a revision id."""
        monkeypatch.chdir(tmp_path)
        world = World.create(tmp_path / "2024", name="Numeric world")
        e = world.add_entity("settlement", "Yearling")
        world.delete_entity(e.id)
        world.close()

        main(["restore", "2024", "--list"])
        assert "Yearling" in capsys.readouterr().out

    def test_a_numeric_path_naming_a_real_file_stays_a_path(self, tmp_path,
                                                            monkeypatch, capsys):
        """`fw restore 2024` with a world file named 2024 in the directory must not
        mutate the default world on a guessed revision id."""
        monkeypatch.chdir(tmp_path)
        default = World.create(tmp_path / "world.fwworld", name="Default")
        casualty = default.add_entity("settlement", "Casualty")
        default.delete_entity(casualty.id)
        default.close()
        World.create(tmp_path / "2024", name="Numeric world").close()

        with pytest.raises(SystemExit) as excinfo:
            main(["restore", "2024"])
        assert "revision id" in str(excinfo.value)
        # and the default world was left alone
        untouched = World.open("world.fwworld")
        assert untouched.entity_named("Casualty") is None
        untouched.close()


class TestServeLibraryChoice:
    """Where the Worlds screen looks for saves, per how the server was started."""

    def _args(self, path="world.fwworld", library=None):
        import argparse
        return argparse.Namespace(path=path, library=library)

    def test_explicit_library_always_wins(self, tmp_path):
        from fw.cli.main import _library_dir
        world = World.create(tmp_path / "solo.fwworld", name="Solo")
        try:
            assert _library_dir(self._args(library="elsewhere"), world) == Path("elsewhere")
        finally:
            world.close()

    def test_a_served_file_makes_its_own_directory_the_library(self, tmp_path):
        """Switching away from a directly-served world must never be a one-way door:
        the file has to appear in the listing it switched from."""
        from fw.cli.main import _library_dir
        from fw.core.library import Library
        world = World.create(tmp_path / "solo.fwworld", name="Solo")
        try:
            directory = _library_dir(self._args(path=str(tmp_path / "solo.fwworld")), world)
            assert directory == tmp_path.resolve()
            assert [w.name for w in Library(directory).worlds()] == ["Solo"]
        finally:
            world.close()

    def test_the_bare_launcher_uses_worlds(self):
        from fw.cli.main import _library_dir
        assert _library_dir(self._args(), None) == Path("worlds")
