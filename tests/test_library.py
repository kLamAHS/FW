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
