"""Reading the writer's world, once, before anything is drawn.

The map used to rediscover the world six separate times per plan and keep the answer
nowhere. `read_world` is the one answer; nothing downstream of it touches `World` again,
which is what makes a stage's output a function of its input rather than of when it ran.
"""

from fw.core.mapgen.source.claims import (
    BASIS_CONFIDENCE,
    Basis,
    Claim,
    Claims,
    Reading,
    known,
    settle,
    unstated,
)
from fw.core.mapgen.source.graph import BorderEdge, BorderGraph
from fw.core.mapgen.source.read import read_world
from fw.core.mapgen.source.reading import (
    EventReading,
    HouseReading,
    Key,
    RegionReading,
    ResourceReading,
    RouteReading,
    SettlementReading,
    TitleReading,
    WaterReading,
    WorldReading,
    key_for,
)

# `scan` itself stays a module rather than a re-exported function: the two
# would share a name here and the function would shadow the module, so
# `from ... import scan` would hand back the wrong thing.
from fw.core.mapgen.source.scan import FeatureRole, Mention

__all__ = [
    "BASIS_CONFIDENCE", "Basis", "BorderEdge", "BorderGraph", "Claim", "Claims",
    "EventReading", "FeatureRole", "HouseReading", "Key", "Mention", "Reading",
    "RegionReading", "ResourceReading", "RouteReading", "SettlementReading",
    "TitleReading", "WaterReading", "WorldReading", "key_for", "known", "read_world",
    "settle", "unstated",
]
