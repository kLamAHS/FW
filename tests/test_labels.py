"""A map you can read (C11).

The generator named twenty-six natural features and drew every one of them without a
label. Only regions and settlements got any text at all, and regions got theirs at their
centroid — which for a crescent is in the sea.

What is asserted here is the four properties that make label placement worth doing on
the server rather than in the browser: a name sits inside the thing it names, no two
names sit on each other, text is never upside down, and the same map labels itself the
same way twice however its features arrived.
"""

from __future__ import annotations

import pytest

from fw.core.mapgen import cartography, labels, shapes


def ring(points):
    return tuple((float(x), float(y)) for x, y in points)


CRESCENT = ring([
    (100, 20), (170, 45), (200, 100), (170, 155), (100, 180), (120, 150),
    (135, 100), (120, 50),
])
SQUARE = ring([(0, 0), (200, 0), (200, 200), (0, 200)])
STRIP = ring([(0, 0), (400, 0), (400, 40), (0, 40)])


class TestANameSitsOnWhatItNames:
    def test_the_spine_of_a_crescent_is_inside_it(self):
        """A centroid is not. That is the whole reason this exists."""
        spine = labels.spine(CRESCENT)
        assert spine, "no spine at all"
        assert all(shapes.contains(CRESCENT, point) for point in spine)

    def test_the_spine_of_a_long_march_runs_its_length(self):
        spine = labels.spine(STRIP)
        assert labels._length(spine) > 300, "the name would sit in a corner"
        assert all(abs(y - 20) < 8 for _x, y in spine), "not down the middle"

    def test_the_spine_of_a_square_is_level_and_central(self):
        """The true medial axis of a square is an X through its centre.

        A walk that simply follows the ridge dives into a corner and comes out as a
        diagonal name across a perfectly level country.
        """
        spine = labels.spine(SQUARE)
        assert all(abs(y - 100) < 12 for _x, y in spine), spine
        assert labels._length(spine) > 80

    def test_a_shape_with_no_area_gets_no_spine(self):
        assert labels.spine(ring([(5, 5), (5, 5), (5, 5)])) == ()

    def test_the_pole_of_a_crescent_is_inside_it(self):
        """Where a name goes when it will not follow a line. A centroid may not be."""
        assert shapes.contains(CRESCENT, labels.pole(CRESCENT))

    def test_a_name_too_long_for_its_shape_is_set_level_at_the_pole(self):
        """A crescent's arms curl away from any line a name could run along.

        Leaving the country unnamed is worse than a name that overhangs its border,
        which is what an atlas does with a small country.
        """
        placed, missed = labels.solve([labels.Wanted(
            key="r", text="The Principality of Somewhere Rather Long", kind="region",
            tier=0, role="label-region", size=14.0, ring=CRESCENT)])
        assert not missed and not placed[0].path
        assert shapes.contains(CRESCENT, (placed[0].x, placed[0].y))

    def test_a_region_label_is_placed_along_its_spine(self):
        placed, missed = labels.solve([labels.Wanted(
            key="r", text="The Vale of Renn", kind="region", tier=0,
            role="label-region", size=14.0, ring=CRESCENT)])
        assert not missed
        assert placed[0].x and placed[0].y


class TestTextIsNeverUpsideDown:
    def test_a_reach_running_east_to_west_is_reversed(self):
        """Otherwise the name prints backwards, which is unmistakable and awful."""
        backwards = tuple((float(x), 100.0 + x / 8) for x in range(400, 0, -20))
        placed, _ = labels.solve([labels.Wanted(
            key="r", text="The River Renn", kind="river", tier=1, role="label-water",
            size=11.0, line=backwards)])
        assert placed and placed[0].path
        assert placed[0].path[0][0] < placed[0].path[-1][0]

    def test_a_reach_already_running_west_to_east_is_left_alone(self):
        forwards = tuple((float(x), 100.0 + x / 8) for x in range(0, 400, 20))
        placed, _ = labels.solve([labels.Wanted(
            key="r", text="The River Renn", kind="river", tier=1, role="label-water",
            size=11.0, line=forwards)])
        assert placed[0].path[0][0] < placed[0].path[-1][0]

    def test_a_level_reach_needs_no_path_at_all(self):
        """A `textPath` along a straight line draws exactly like plain text.

        It gives up letter-spacing and hinting to do it, so a name that does not bend
        is left as ordinary text.
        """
        flat = tuple((float(x), 100.0) for x in range(0, 400, 20))
        placed, _ = labels.solve([labels.Wanted(
            key="r", text="The River Renn", kind="river", tier=1, role="label-water",
            size=11.0, line=flat)])
        assert placed and not placed[0].path

    def test_a_reach_too_short_for_its_name_is_not_labelled(self):
        placed, missed = labels.solve([labels.Wanted(
            key="r", text="The Extremely Long River Of Many Names", kind="river",
            tier=1, role="label-water", size=11.0,
            line=((0.0, 0.0), (10.0, 0.0)))])
        assert not placed
        assert [(m.key, m.reason) for m in missed] == [("r", "no room")]


class TestTwoNamesNeverSitOnEachOther:
    def test_a_second_label_in_the_same_place_is_moved_or_dropped(self):
        want = [
            labels.Wanted(key="a", text="Rennford", kind="capital", tier=0,
                          role="label", size=12.0, point=(100.0, 100.0), weight=9),
            labels.Wanted(key="b", text="Millbrook", kind="town", tier=1,
                          role="label", size=12.0, point=(100.0, 100.0)),
        ]
        placed, _missed = labels.solve(want)
        boxes = [box for label in placed for box in label.boxes]
        for n, one in enumerate(boxes):
            for other in boxes[n + 1:]:
                assert not (one[0] < other[2] and other[0] < one[2]
                            and one[1] < other[3] and other[1] < one[3])

    def test_a_curved_label_reserves_its_whole_length(self):
        """A straight spine is two points.

        Boxing only its vertices reserved the two ends of a country's name and left
        every letter between them free, so a town's label sat squarely in the middle of
        "THE KINGDOM OF RENN" and both were drawn.
        """
        placed, _ = labels.solve([labels.Wanted(
            key="a", text="The Kingdom of Renn", kind="region", tier=0,
            role="label-region", size=20.0, ring=STRIP)])
        assert placed
        spread = max(b[2] for b in placed[0].boxes) - min(b[0] for b in placed[0].boxes)
        assert spread > labels.width_of("The Kingdom of Renn", placed[0].size) * 0.85

    def test_a_first_tier_name_gets_the_room(self):
        """Which names survive a crowded map is decided by what they are."""
        crowd = [labels.Wanted(key=f"t{n}", text=f"Village {n}", kind="village",
                               tier=2, role="label", size=12.0,
                               point=(100.0, 100.0 + n))
                 for n in range(6)]
        crowd.append(labels.Wanted(key="cap", text="Rennford", kind="capital", tier=0,
                                   role="label", size=15.0, point=(100.0, 100.0)))
        placed, _ = labels.solve(crowd)
        assert "cap" in {label.key for label in placed}


class TestTheSameMapLabelsItselfTheSameWay:
    def test_the_order_they_arrived_in_does_not_matter(self):
        want = [labels.Wanted(key=f"t{n}", text=f"Town {n}", kind="town", tier=1,
                              role="label", size=12.0, point=(100.0 + n * 9, 100.0))
                for n in range(12)]
        first, _ = labels.solve(list(want))
        second, _ = labels.solve(list(reversed(want)))
        assert [(p.key, p.x, p.y) for p in first] == [(p.key, p.x, p.y) for p in second]

    def test_solving_twice_gives_the_same_answer(self):
        want = [labels.Wanted(key="a", text="Renn", kind="region", tier=0,
                              role="label-region", size=18.0, ring=CRESCENT)]
        assert labels.solve(want)[0] == labels.solve(want)[0]


class TestTextMetrics:
    def test_a_narrow_letter_is_not_a_wide_one(self):
        assert labels.width_of("iiii", 10) < labels.width_of("mmmm", 10)

    def test_width_scales_with_the_type_size(self):
        assert labels.width_of("Renn", 20) == pytest.approx(
            labels.width_of("Renn", 10) * 2)


# --------------------------------------------------------------- the draw plan

def feature(**kw):
    base = {"id": kw.get("id", "g1"), "entity_id": kw.get("entity_id", "e1"),
            "name": "", "type_key": "settlement", "kind": "point",
            "coordinates": [10.0, 10.0], "layer": "settlements", "style": {},
            "approximate": False, "generated": True, "control": {}}
    base.update(kw)
    return base


class TestTheDrawPlan:
    def test_the_frame_holds_everything(self):
        plan = cartography.draw([
            feature(id="a", coordinates=[10.0, 10.0]),
            feature(id="b", entity_id="e2", coordinates=[800.0, 600.0]),
        ])
        assert plan.bounds.x < 10 and plan.bounds.y < 10
        assert plan.bounds.x + plan.bounds.width > 800
        assert plan.bounds.y + plan.bounds.height > 600

    def test_an_empty_map_still_has_a_frame(self):
        """A world with nothing drawn must not produce a zero-sized view."""
        plan = cartography.draw([])
        assert plan.bounds.width > 0 and plan.bounds.height > 0

    def test_a_hamlet_a_city_and_a_castle_are_not_the_same_dot(self):
        plan = cartography.draw([
            feature(id="a", name="Rennford", style={"rank": "capital"}),
            feature(id="b", entity_id="e2", name="Redwater", style={"rank": "hamlet"},
                    coordinates=[200.0, 200.0]),
            feature(id="c", entity_id="e3", name="Greygate", layer="castles",
                    type_key="holding", style={"rank": "keep"},
                    coordinates=[400.0, 400.0]),
        ])
        shapes_by_name = {icon.name: icon.shape for icon in plan.icons}
        assert shapes_by_name == {"Rennford": "star", "Redwater": "dot",
                                  "Greygate": "keep"}
        sizes = {icon.name: icon.radius for icon in plan.icons}
        assert sizes["Rennford"] > sizes["Redwater"]

    def test_one_name_per_thing_not_per_shape(self):
        """A river reaches the sea as four segments and wants one name.

        Labelling geometry rather than entities wrote "Generated roads" across the map
        nine times and pushed out the towns that could not find room around them.
        """
        segments = [feature(id=f"s{n}", entity_id="river", name="The River Renn",
                            kind="line", layer="waterways",
                            coordinates=[[float(n * 100), 50.0],
                                         [float(n * 100 + 90), 60.0]])
                    for n in range(4)]
        plan = cartography.draw(segments)
        assert [label.text for label in plan.labels] == ["The River Renn"]

    def test_the_legend_lists_only_what_is_on_this_map(self):
        plan = cartography.draw([feature(name="Redwater", style={"rank": "hamlet"})])
        labels_shown = {entry.label for entry in plan.legend}
        assert "Castle or keep" not in labels_shown
        assert "Hamlet" in labels_shown

    def test_the_legend_names_the_authority_it_is_colouring_by(self):
        plan = cartography.draw([feature(
            name="Greyhaven",
            control={"administers": [{"id": "h1", "name": "House Veyne"}]})],
            mode="administers")
        line = next(e for e in plan.legend if e.label == "House Veyne")
        assert line.note == "Administered by"
        assert line.role.startswith("holder-")

    def test_a_house_keeps_its_colour_when_a_row_is_added(self):
        """Assigned by name, not by whichever feature was read first."""
        one = cartography.draw([
            feature(id="a", control={"legally_owns": [{"id": "h1", "name": "Marr"}]}),
            feature(id="b", entity_id="e2",
                    control={"legally_owns": [{"id": "h2", "name": "Veyne"}]}),
        ])
        two = cartography.draw([
            feature(id="b", entity_id="e2",
                    control={"legally_owns": [{"id": "h2", "name": "Veyne"}]}),
            feature(id="c", entity_id="e3",
                    control={"legally_owns": [{"id": "h3", "name": "Aa"}]}),
            feature(id="a", control={"legally_owns": [{"id": "h1", "name": "Marr"}]}),
        ])
        assert one.holders["h1"] == "holder-0" and one.holders["h2"] == "holder-1"
        assert two.holders["h1"] != two.holders["h2"], "two houses, one colour"

    def test_the_map_does_not_write_the_world_s_own_name_across_itself(self):
        """The mainland is named after the world, and the map is *of* that world."""
        land = feature(id="l", entity_id="land", name="The Kingdom of Renn",
                       kind="polygon", layer="land", type_key="terrain_feature",
                       coordinates=[[list(p) for p in SQUARE]])
        assert not cartography.draw([land], world_name="The Kingdom of Renn").labels
        assert cartography.draw([land], world_name="Elsewhere").labels

    def test_a_thing_the_writer_drew_is_labelled_before_one_the_map_made(self):
        theirs = feature(id="a", entity_id="road-a", name="The Iron Road", kind="line",
                         layer="roads", generated=False,
                         coordinates=[[0.0, 0.0], [400.0, 0.0]])
        ours = feature(id="b", entity_id="road-b", name="A Generated Road", kind="line",
                       layer="roads", generated=True,
                       coordinates=[[0.0, 0.0], [400.0, 0.0]])
        plan = cartography.draw([ours, theirs])
        assert plan.labels[0].text == "The Iron Road"

    def test_a_brook_on_the_features_layer_is_not_called_a_road(self):
        plan = cartography.draw([feature(
            id="a", entity_id="b", name="The Blackbrook", kind="line",
            layer="features", coordinates=[[0.0, 0.0], [400.0, 0.0]])])
        assert plan.labels[0].kind == "feature"

    def test_every_role_it_emits_is_one_the_stylesheet_knows(self):
        plan = cartography.draw([
            feature(name="Rennford", style={"rank": "capital"}),
            feature(id="c", entity_id="e3", name="Greygate", layer="castles",
                    type_key="holding", coordinates=[400.0, 400.0]),
        ])
        used = {icon.role for icon in plan.icons}
        used |= {entry.role for entry in plan.legend}
        used |= {label.role for label in plan.labels}
        assert used <= set(cartography.ROLES), used - set(cartography.ROLES)


class TestTheWorkingIsAvailableOnAsk:
    """V2 §50: the solver's scaffolding reaches a debug overlay, and only a debug
    overlay — the ordinary payload stays clean."""

    def test_boxes_are_kept_off_the_ordinary_wire(self):
        placed, _ = labels.solve([labels.Wanted(
            key="t", text="Rennford", kind="town", tier=1, role="label", size=11.0,
            point=(100.0, 100.0), clearance=4.0)])
        assert "boxes" not in placed[0].as_dict()
        told = placed[0].as_dict(debug=True)
        assert told["boxes"] and len(told["boxes"][0]) == 4

    def test_a_drop_names_itself_and_says_why(self):
        _, missed = labels.solve([labels.Wanted(
            key="r", text="The Longest Name Ever Written On A Map", kind="river",
            tier=1, role="label-water", size=11.0,
            line=((0.0, 0.0), (8.0, 0.0)))])
        assert missed[0].text.startswith("The Longest")
        assert missed[0].reason == "no room"
        assert missed[0].as_dict()["reason"] == "no room"
