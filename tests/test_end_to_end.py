"""The brief's own end-to-end workflow (spec §107).

§107 asks for a demonstration that data entered early becomes useful later — that the
systems genuinely interact rather than sitting in separate forms. It lists twenty steps:
create a kingdom, draw its map, define regions, add rivers and roads, create towns, assign
them to lords, create houses, create characters and genealogy, establish feudal obligations,
define resources, create trade routes, add historical events, establish title succession,
create a novel and a scene, see the scene's relevant relationships and secrets, trigger a
king's death, calculate succession, explore political consequences, and run continuity
checks.

This test walks all twenty against a world built from nothing, and asserts at each step that
the *earlier* data is doing work. It is the strongest single check that the model is
interconnected rather than a set of disconnected tables.
"""

from __future__ import annotations

from fw.core.continuity.engine import ContinuityEngine
from fw.core.derive.dependency import DependencyAnalyst
from fw.core.derive.scene_context import SceneContextEngine
from fw.core.genealogy.kinship import Genealogy
from fw.core.geo.routing import Router
from fw.core.seed.renn import RENNISH
from fw.core.succession.engine import SuccessionEngine
from fw.core.world import World


def test_the_specs_twenty_step_workflow():
    # 1. Create a kingdom.
    w = World.create(name="Aramor", calendar=RENNISH)
    d = w.day
    realm = w.add_entity("realm", "The Kingdom of Aramor", exists_from=d(1))

    # 2-3. Draw its map; define regions.
    highlands = w.add_entity("region", "The Highlands")
    lowlands = w.add_entity("region", "The Lowlands")
    for region in (highlands, lowlands):
        w.assert_fact(region, "located_in", realm)
    w.add_geometry(highlands.id, "polygon", [[[0, 0], [200, 0], [200, 200], [0, 200], [0, 0]]],
                   layer="regions")
    w.add_geometry(lowlands.id, "polygon",
                   [[[200, 0], [420, 0], [420, 200], [200, 200], [200, 0]]], layer="regions")

    # 4. Add rivers and roads.
    river = w.add_entity("waterway", "The Aral")
    road = w.add_entity("road", "The King's Road", exists_from=d(60))

    # 5. Create towns.
    capital = w.add_entity("settlement", "Aramor City", exists_from=d(10))
    port = w.add_entity("settlement", "Saltgate", exists_from=d(30))
    mine_town = w.add_entity("settlement", "Deepdelve", exists_from=d(45))
    for town, region in ((capital, lowlands), (port, lowlands), (mine_town, highlands)):
        w.assert_fact(town, "located_in", region)
    w.assert_fact(capital, "capital_of", realm)
    w.assert_fact(river, "flows_through", capital)

    w.add_route_segment(capital.id, port.id, 40, entity_id=road.id, built_on=d(60))
    w.add_route_segment(capital.id, mine_town.id, 120, entity_id=road.id,
                        terrain="mountain", quality=0.5, built_on=d(60))
    for town, (x, y) in ((capital, (260, 90)), (port, (390, 60)), (mine_town, (80, 150))):
        w.add_geometry(town.id, "point", [x, y], layer="settlements")

    # 7. Create noble houses.
    crown = w.add_entity("dynasty", "House Aramor", exists_from=d(1))
    hill = w.add_entity("house", "House Stonefell", exists_from=d(20))
    shore = w.add_entity("house", "House Tidewell", exists_from=d(25))

    # 6. Assign towns to regional lords.
    w.assert_fact(crown, "legally_owns", capital, valid_from=d(10))
    w.assert_fact(hill, "legally_owns", mine_town, valid_from=d(45))
    w.assert_fact(shore, "legally_owns", port, valid_from=d(30))

    # 9. Establish feudal obligations.
    w.assert_fact(hill, "vassal_of", crown, valid_from=d(45))
    w.assert_fact(shore, "vassal_of", crown, valid_from=d(30))

    # 8. Create characters and genealogy.
    def person(name, born, died=None, gender="male", house=None):
        p = w.add_entity("person", name, exists_from=d(*born) if isinstance(born, tuple)
                         else d(born),
                         exists_to=(d(*died) if isinstance(died, tuple)
                                    else d(died)) if died else None)
        w.assert_fact(p, "gender", value=gender)
        if house:
            w.assert_fact(p, "member_of", house)
        return p

    founder = person("Queen Isolde", 100, 160, "female", crown)
    king = person("King Halvard", 130, (196, 3, 10), "male", crown)
    sister = person("Princess Adela", 133, None, "female", crown)
    heir = person("Prince Rion", 165, None, "male", crown)
    daughter = person("Princess Wren", 168, None, "female", crown)
    nephew = person("Lord Bern", 170, None, "male", crown)

    w.assert_fact(founder, "parent_of", king)
    w.assert_fact(founder, "parent_of", sister)
    w.assert_fact(king, "parent_of", heir)
    w.assert_fact(king, "parent_of", daughter)
    w.assert_fact(sister, "parent_of", nephew)

    # 10. Define agricultural and mineral resources.
    iron = w.add_entity("resource", "Iron")
    grain = w.add_entity("resource", "Grain")
    w.assert_fact(highlands, "produces", iron, strength="high")
    w.assert_fact(lowlands, "produces", grain, strength="high")
    w.assert_fact(mine_town, "exports", iron)
    w.assert_fact(mine_town, "imports", grain)
    w.assert_fact(mine_town, "depends_on", capital,
                  note="Deepdelve eats what the Lowlands send up the King's Road.")

    # 11. Create trade routes.
    run = w.add_entity("trade_route", "The Iron Run")
    w.assert_fact(run, "connects", mine_town)
    w.assert_fact(run, "connects", port)

    # 12. Add historical events.
    famine = w.add_event("The Long Hunger", type_key="event", start_day=d(190),
                         location_id=highlands.id,
                         participants=[(hill.id, "afflicted")])
    revolt = w.add_event("The Deepdelve Revolt", type_key="rebellion", start_day=d(193),
                         location_id=mine_town.id,
                         participants=[(hill.id, "belligerent"), (crown.id, "belligerent")])
    w.link_cause(famine.id, revolt.id, note="Hunger in the mines became a rising.")

    # 13. Establish title succession.
    throne = w.add_title("Monarch of Aramor", rank=100, territory_id=realm.id,
                         succession_law="male_preference_primogeniture",
                         dynasty_root_id=founder.id, created_on=d(1))
    w.grant_title(throne.id, founder.id, from_day=d(120), to_day=d(160))
    w.grant_title(throne.id, king.id, from_day=d(160), to_day=d(196, 3, 10))

    # 14-15. Create a novel and a scene.
    novel = w.add_work("The Long Hunger", kind="novel")
    chapter = w.add_chapter(novel, "The Reckoning", position=1)

    secret = w.add_secret("The grain was diverted",
                          truth="The Crown sold the Highlands' relief grain to Saltgate.",
                          about_id=crown.id, severity="critical")
    w.set_knowledge(heir.id, secret.id, "knows", acquired_on=d(191))
    w.set_knowledge(nephew.id, secret.id, "suspects", acquired_on=d(194))
    w.set_knowledge(daughter.id, secret.id, "unaware")

    w.assert_fact(heir, "trusts", nephew, strength="distrusts")
    w.assert_fact(nephew, "feels_about", heir, strength="hates",
                  note="He watched the Highlands starve.")
    w.assert_fact(heir, "private_goal", value="To keep the diversion buried")
    w.assert_fact(nephew, "private_goal", value="To see the diversion revealed")

    scene = w.add_scene("The Reckoning at Aramor City", chapter_id=chapter, position=1,
                        day=d(196, 3, 20), location_id=capital.id, pov_id=nephew.id,
                        participants=[heir.id, nephew.id, daughter.id])
    w.analyze()

    # ---------------------------------------------------------------- assertions

    # 16. See scene-relevant relationships and secrets — none of which were
    #     entered as "scene data". They are the world, read through the scene.
    context = SceneContextEngine(w).build(scene.id)
    assert {p.name for p in context.participants} == {
        "Prince Rion", "Lord Bern", "Princess Wren"
    }
    relationships = " ".join(r.describe() for r in context.relationships)
    assert "Lord Bern" in relationships and "Prince Rion" in relationships
    secrets = " ".join(s.describe() for s in context.secrets)
    assert "Prince Rion knows" in secrets
    assert "Lord Bern suspects" in secrets
    # Dramatic irony: someone in the room knows what the room's subject does not.
    # This one is structural, so it is asserted exactly.
    assert any("does not" in t for t in context.tensions), context.tensions
    # The goal-conflict reading is a deliberately shallow heuristic offered as a prompt
    # (§14), so it is asserted as "found something" rather than by exact wording.
    assert any("incompatible things" in t for t in context.tensions), context.tensions
    # The location's control, asserted in step 6, surfaces here in step 16.
    assert any("House Aramor" in note for note in context.world_state_notes)

    # 17-18. Trigger the king's death and calculate succession. The genealogy from
    #        step 8 and the law from step 13 do the work; nothing else was entered.
    engine = SuccessionEngine(w)
    death = d(196, 3, 10)
    line = engine.compute(throne.id, death)
    assert line.names() == [
        "Prince Rion", "Princess Wren", "Princess Adela", "Lord Bern"
    ]
    assert line.heir.name == "Prince Rion"

    # The king's own line is exhausted before the crown moves sideways to his sister,
    # and Adela outranks her own son. That ordering is the whole point of a depth-first
    # primogeniture walk, and getting it wrong is the classic succession-engine bug.
    assert line.names().index("Princess Wren") < line.names().index("Princess Adela")
    assert line.names().index("Princess Adela") < line.names().index("Lord Bern")

    # The same family under a different law produces a different heir.
    assert engine.compute(
        throne.id, death, law_key="ultimogeniture"
    ).names()[0] == "Princess Adela"

    # A hypothesis that never touches canon.
    without_heir = engine.compute(throne.id, death, assume_dead={heir.id})
    assert without_heir.names()[:2] == ["Princess Wren", "Princess Adela"]
    assert engine.compute(throne.id, death).names()[0] == "Prince Rion"

    # 19. Explore political consequences.
    causal = w.consequences_of(famine.id)
    assert revolt.id in [eid for eid, _ in causal]

    impact = DependencyAnalyst(w).what_if_removed(capital.id, d(196))
    consequences = " ".join(c["text"] for c in impact["consequences"])
    assert "Deepdelve" in consequences        # the dependency asserted in step 10
    assert impact["consequences"], "removing the capital should have consequences"

    why = DependencyAnalyst(w).why_it_matters(heir.id, death)
    findings = " ".join(f["text"] for f in why["findings"])
    assert "in line for" in findings           # from steps 8 and 13
    assert all(f["evidence"] for f in why["findings"])

    # 20. Run continuity checks. A world built correctly should come back clean.
    report = ContinuityEngine(w).run()
    assert report.errors == [], "\n".join(v.message for v in report.errors)

    # And the checks have teeth: introduce a contradiction and it is caught.
    w.add_event("A posthumous council", start_day=d(200),
                participants=[(king.id, "chair")])
    broken = ContinuityEngine(w).run()
    assert any(v.rule_key == "dead_character_acts" for v in broken.errors)

    w.close()


def test_data_entered_early_is_useful_later():
    """§107's actual point, stated as one assertion.

    Roads entered for the map answer travel questions; travel answers feed continuity;
    genealogy entered for the family tree drives succession; succession feeds the
    dashboard. Nothing below was entered twice.
    """
    w = World.create(name="Small world", calendar=RENNISH)
    d = w.day

    a = w.add_entity("settlement", "Northkeep")
    b = w.add_entity("settlement", "Southgate")
    w.add_route_segment(a.id, b.id, 300, quality=1.0, terrain="plain")

    rider = w.add_entity("person", "A courier", exists_from=d(100))
    w.add_scene("Departure", day=d(200, 1, 1), location_id=a.id, participants=[rider.id])
    w.add_scene("Arrival", day=d(200, 1, 2), location_id=b.id, participants=[rider.id])

    # The road was entered to draw a map. It now answers a travel question…
    days = Router(w).travel_time(a.id, b.id, profile="horse")
    assert days is not None and days > 5

    # …and that travel answer catches a continuity error in the manuscript.
    report = ContinuityEngine(w).run()
    assert any(v.rule_key == "impossible_journey" for v in report.violations)

    # Parentage entered for a family tree answers a kinship question in plain language.
    parent = w.add_entity("person", "A parent", exists_from=d(80))
    w.assert_fact(parent, "parent_of", rider)
    assert Genealogy(w).relationship_between(rider.id, parent.id) == "parent"

    w.close()
