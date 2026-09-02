"""The world library: saves, the launcher's data, and the launcher's API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fw.api.app import create_app
from fw.core.library import Library, LibraryError
from fw.core.world import World


class TestLibrary:
    def test_create_lists_and_reopens(self, tmp_path):
        library = Library(tmp_path / "worlds")
        path = library.create("The Shattered Coast")
        assert path.name == "the-shattered-coast.fwworld"

        worlds = library.worlds()
        assert [w.name for w in worlds] == ["The Shattered Coast"]
        assert worlds[0].entities == 0
        assert worlds[0].problem == ""

        reopened = World.open(library.path_of(worlds[0].file))
        assert reopened.name == "The Shattered Coast"
        reopened.close()

    def test_same_name_twice_makes_two_files(self, tmp_path):
        library = Library(tmp_path)
        first = library.create("Mirrors")
        second = library.create("Mirrors")
        assert first.name != second.name
        assert len(library.worlds()) == 2

    def test_the_example_world_is_opt_in(self, tmp_path):
        library = Library(tmp_path)
        path = library.create("Tour", example=True)
        world = World.open(path)
        assert world.name == "The Kingdom of Renn"
        assert world.count_entities() > 20
        world.close()

    def test_the_example_world_arrives_with_its_map_grown(self, tmp_path):
        """This button says "show me what this does", so it has to.

        It used to hand over the seed alone: three province outlines, a river and two
        roads floating on blank paper, with no coast and no ground. The example world
        is the only map most people will ever see this project draw.
        """
        world = World.open(Library(tmp_path).create("Tour", example=True))
        try:
            assert world.terrain() is not None, "no ground under it"
            assert world.db.scalar("SELECT count(*) FROM geometry") > 100
        finally:
            world.close()

    def test_nameless_worlds_are_refused(self, tmp_path):
        with pytest.raises(LibraryError, match="name"):
            Library(tmp_path).create("   ")

    def test_hostile_file_names_are_refused(self, tmp_path):
        library = Library(tmp_path / "worlds")
        library.create("Safe")
        for hostile in ("../outside.fwworld", "a/b.fwworld", "..", "",
                        "safe.txt", "/etc/passwd", "..\\up.fwworld"):
            with pytest.raises(LibraryError):
                library.path_of(hostile)

    def test_a_corrupt_save_is_listed_not_hidden(self, tmp_path):
        library = Library(tmp_path)
        library.create("Good")
        (tmp_path / "broken.fwworld").write_bytes(b"not a database at all")
        listed = {w.file: w for w in library.worlds()}
        assert listed["broken.fwworld"].problem != ""
        assert listed["good.fwworld"].problem == ""


class TestLauncherApi:
    @pytest.fixture
    def client(self, tmp_path) -> TestClient:
        """A server started with no world open — the run.bat experience."""
        return TestClient(create_app(library=Library(tmp_path / "worlds")))

    def test_no_world_open_is_a_409_not_a_crash(self, client):
        response = client.get("/api/world")
        assert response.status_code == 409
        assert "open" in response.json()["detail"]

    def test_create_open_and_switch(self, client):
        assert client.get("/api/worlds").json()["worlds"] == []

        made = client.post("/api/worlds", json={"name": "My Saga"})
        assert made.status_code == 201
        assert client.get("/api/world").json()["name"] == "My Saga"

        # a second world, then switch back to the first
        client.post("/api/worlds", json={"name": "Another"})
        listing = client.get("/api/worlds").json()
        assert listing["open"] == "another.fwworld"
        assert {w["file"] for w in listing["worlds"]} == {
            "my-saga.fwworld", "another.fwworld"}

        client.post("/api/worlds/open", json={"file": "my-saga.fwworld"})
        assert client.get("/api/world").json()["name"] == "My Saga"

    def test_a_new_world_is_actually_usable(self, client):
        """The empty world must not be a broken shell: create, edit, search."""
        client.post("/api/worlds", json={"name": "Blank"})
        made = client.post("/api/entities", json={
            "type_key": "person", "name": "First Person"})
        assert made.status_code == 201
        assert client.get("/api/search", params={"q": "First"}).json()
        assert client.get("/api/continuity").status_code == 200

    def test_the_example_world_arrives_seeded(self, client):
        client.post("/api/worlds", json={"name": "Tour", "example": True})
        world = client.get("/api/world").json()
        assert world["name"] == "The Kingdom of Renn"
        assert world["counts"]["total"] > 20

    def test_traversal_and_ghosts_are_refused(self, client):
        assert client.post("/api/worlds/open",
                           json={"file": "../../etc/passwd"}).status_code == 404
        assert client.post("/api/worlds/open",
                           json={"file": "nothing.fwworld"}).status_code == 404
        assert client.post("/api/worlds", json={"name": "  "}).status_code == 400

    def test_single_file_servers_have_no_library(self, renn):
        client = TestClient(create_app(renn))
        info = client.get("/api/worlds").json()
        assert info["library"] is None
        assert client.post("/api/worlds",
                           json={"name": "X"}).status_code == 400


class TestLibraryEdgeCases:
    def test_an_epic_name_becomes_a_file_not_an_oserror(self, tmp_path):
        library = Library(tmp_path)
        path = library.create("The " + "Very " * 80 + "Long Chronicle")
        assert path.exists()
        assert len(path.name.encode()) < 255

    def test_a_filesystem_refusal_is_a_library_error(self, tmp_path):
        """A library path that cannot be a directory (here: it is a file) must come
        back as a message, not an OSError traceback. (A chmod-based test would lie
        under root, which ignores permission bits.)"""
        squatter = tmp_path / "occupied"
        squatter.write_text("not a directory")
        with pytest.raises(LibraryError, match="could not create"):
            Library(squatter).create("Anything")

    def test_a_dangling_symlink_does_not_take_down_the_listing(self, tmp_path):
        library = Library(tmp_path)
        library.create("Solid")
        (tmp_path / "ghost.fwworld").symlink_to(tmp_path / "vanished.fwworld")
        listed = {w.file: w for w in library.worlds()}
        assert listed["solid.fwworld"].problem == ""
        assert listed["ghost.fwworld"].problem != ""

    def test_switching_leaves_the_old_connection_briefly_alive(self, tmp_path):
        """Routes run in a threadpool: a request resolved against the old world can
        still be mid-query when the switch lands, so the old connection must not be
        closed out from under it."""
        from fastapi.testclient import TestClient

        from fw.api.app import create_app

        client = TestClient(create_app(library=Library(tmp_path / "worlds")))
        client.post("/api/worlds", json={"name": "First"})
        old_world = client.app.state.holder.world
        client.post("/api/worlds", json={"name": "Second"})
        # the retired world answers queries during the grace period
        assert old_world.db.scalar("SELECT count(*) FROM entity") == 0

    def test_an_over_long_name_is_a_clean_400_over_http(self, tmp_path):
        from fastapi.testclient import TestClient

        from fw.api.app import create_app

        client = TestClient(create_app(library=Library(tmp_path / "worlds")))
        response = client.post("/api/worlds", json={"name": "x" * 300})
        # the slug cap makes this creatable; what must never happen is a 500
        assert response.status_code in (201, 400)
