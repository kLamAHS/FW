"""CLI adapter tests — the argument handling the test suite cannot reach through HTTP."""

from __future__ import annotations

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
