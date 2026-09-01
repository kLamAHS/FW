"""The relief renderer: what the ground actually looks like.

Everything else the generator makes is geometry — outlines, courses, points — and the
browser draws geometry well. Relief is not geometry. A mountain range is not a shape with
a fill; it is two million shaded samples, and the difference between a map that reads as
a physical place and one that reads as a diagram is almost entirely here.

Three things are happening, and the order matters:

  the surface   the simulation lattice is coarse on purpose — it is scaffolding, not the
                map. It is interpolated up to the render size, warped so the interpolation
                does not show as a quilt, and given fine detail the simulation had no
                business carrying. The coastline then falls out of *that* surface rather
                than the lattice, which is why it has bays a cell wide instead of steps.

  the light     a single low sun, plus a weaker fill from the opposite side so nothing
                goes to solid black. Lambertian, from the surface normal — no trigonometry
                anywhere, because the normal and the light are both just vectors and the
                dot product of two vectors is arithmetic.

  the colour    a hypsometric tint: a muted physical-atlas ramp from sea through the
                greens of a river plain to the greys and white of a summit. Saturation is
                kept deliberately low. Bright terrain colours are the fastest way to make
                a map look like a video game, and the reference maps a novelist has in
                mind are printed ones.

The per-pixel loop is the hottest thing in the codebase by an order of magnitude, so the
colour arithmetic is not done per pixel: tint and shade are each quantised to 256 steps
and every one of the 65,536 combinations is worked out once into a lookup table. What is
left in the loop is four array reads, a dozen flops, one square root and a slice.
"""

from __future__ import annotations

import hashlib
import math
from array import array
from dataclasses import dataclass
from functools import lru_cache

from fw.core.mapgen import noise
from fw.core.mapgen.grid import Field, Grid

# Render pixels per lattice cell. The simulation is invisible scaffolding; this is how
# much finer the thing the writer looks at is than the thing the generator computed on.
SCALE = 8

# How far the sampling point wanders, in lattice cells. Interpolating a coarse lattice
# leaves creases along it, and although each one is far too faint to see, the *pattern*
# they make is not: the eye finds a repeating pattern in a landscape immediately. The
# warp is what destroys the pattern. What it cannot do is remove the creases themselves,
# which is the interpolation's job — see the note on Catmull-Rom in `_surface`.
WARP = 0.75
WARP_SCALE = 9.0          # lattice cells per unit of the warp field
WARP2 = WARP * 2.0        # the warp field is centred on a half, so double the reach

# Fine relief the simulation never carried, added at draw time and scaled by local slope,
# so flat ground stays flat and a mountainside gets its texture.
DETAIL = 0.055
DETAIL_SCALE = 3.4
DETAIL_STRIDE = 2         # lattice cells per detail sample, before interpolation
SHALLOW = 0.02            # height above the waterline over which detail fades in

# The sun. Low in the north-west, which is the convention every printed relief map uses:
# lit from the top-left, because a reader shown light from below reads every valley as a
# ridge. Written as a unit vector because the alternative is an azimuth and an altitude
# and two libm calls.
LIGHT = (-0.5906, -0.5906, 0.5499)
FILL_LIGHT = (0.4082, 0.4082, 0.8165)     # weak, from the opposite side
FILL_SHARE = 0.30
# A second key, low from the north-east (V2 §19). One light and a fill leaves every
# slope facing north-east reading the same flat value, so a range running NW-SE — the
# commonest strike this generator makes — has one modelled flank and one dead one.
# Weak and cross-lit, it separates those faces without turning the picture into two
# suns: the combined vector is renormalised below, so adding it changes the modelling,
# never the overall exposure.
CROSS_LIGHT = (0.6559, -0.6559, 0.3746)
CROSS_SHARE = 0.16

# How deeply a hollow shades itself, and how brightly a crest catches the light —
# from the curvature of the ground, which is what tells a valley from a plain when
# both face the same way (V2 §19, §20).
#
# Measured across CURVE_REACH of a lattice cell, NOT across one pixel. A second
# difference taken pixel to pixel measures the Catmull-Rom interpolant's own bending
# between samples, not the land's: it drew the lattice as a crosshatch over every
# flat, which is the interpolation seam made visible and exactly the artefact the
# Catmull-Rom was chosen to avoid. Sampled over a fixed fraction of a cell the term
# reads the landform, and needs no scale correction — the distance is the same
# distance at every resolution.
VALLEY_SHADE = 5.2
RIDGE_LIGHT = 3.4
CURVE_REACH = 0.75
# The most either may move the shading, as a share of the whole range. Curvature is
# noisy at the top end and an unclamped term draws the fbm grain, not the landform.
CURVE_LIMIT = 0.16

# Distance haze (V2 §20): the high ground is seen through more air, so it loses a
# little contrast toward the paper. Baked into the blend table, which is indexed by
# height already, so it costs nothing per pixel. Kept low — this is a map, and a map
# whose mountains disappear is a map that has forgotten what it is for.
HAZE = 0.10
HAZE_FROM = 0.55          # the share of the height range where haze starts
HAZE_TINT = (0.902, 0.894, 0.871)

# How much the shading darkens and lightens the tint. Relief shading that runs to black
# looks like a photograph of a model; a printed map holds a narrow band.
SHADE_FLOOR = 0.62
SHADE_CEILING = 1.34

# Vertical exaggeration of the height field when computing the normal. Real relief on a
# continent is imperceptibly gentle at map scale; every physical atlas ever printed
# exaggerates it, and so does this.
RELIEF_GAIN = 20.0

# The hypsometric ramp, as (height above sea level from 0 to 1, red, green, blue).
# Deliberately desaturated — see the note above.
LAND_RAMP = (
    (0.000, 0.639, 0.667, 0.545),      # the wet green of a delta
    (0.055, 0.678, 0.702, 0.545),
    (0.150, 0.741, 0.741, 0.573),      # plain
    (0.300, 0.796, 0.769, 0.596),      # dry grass
    (0.450, 0.808, 0.741, 0.588),      # foothills
    (0.600, 0.769, 0.678, 0.561),      # upland
    (0.740, 0.702, 0.639, 0.588),      # rock
    (0.870, 0.706, 0.690, 0.678),      # bare grey
    (1.000, 0.902, 0.902, 0.898),      # summit
)

# Sea colour by depth. Shallow water is what makes a coast legible: a shelf reads as a
# shore even where the land behind it is flat, and a map whose ocean is one flat blue has
# thrown away the only cue that says which side of the line the water is on. Kept narrow
# and low in contrast — a wide bright shelf reads as a halo drawn round the land rather
# than as water over a slope.
SEA_RAMP = (
    (0.000, 0.478, 0.565, 0.604),      # the shore
    (0.140, 0.404, 0.502, 0.565),      # shallows
    (0.520, 0.333, 0.443, 0.529),      # the shelf proper
    (1.000, 0.290, 0.396, 0.494),      # deep
)

# The depth, below sea level in the same units as the height field, at which the sea
# ramp bottoms out. This IS the bathymetry's own depth — `generate` imports it — and
# for a phase it was not: the model built a shelf 0.22 deep while the ramp saturated
# at 0.10, so the outer half of every shelf rendered as one flat colour and the
# shallow band hugged the whole coast at a uniform width. That band was the "halo".
SHELF_DEPTH = 0.22

# Where the drainage is carved into the drawn surface (V2 §3). The erosion's valleys
# are real but sub-visible at the lattice scale; incising the render surface along
# the persisted flow makes them read as structure. Depth in height units, faded near
# the waterline so the raster shore stays where the vector coast says it is.
CARVE = 0.035
# In root space, because that is how the field arrives: the terrain store keeps
# sqrt(cells drained), the same discharge exponent the erosion cuts with.
CARVE_FLOW_LOW = 5.0         # sqrt(flow) at which a channel starts to show
CARVE_FLOW_HIGH = 22.4       # sqrt(flow) at which it is as deep as it gets
CARVE_FADE = 0.05            # height above sea over which carving fades in

# Texture restraint (V2 §3: "empty terrain is important"). The fine detail is scaled
# by how rugged the ground actually is, so lowland plains go quiet instead of
# carrying the same fractal grain as a mountainside — the northern porridge was too
# much energy, not too little.
QUIET_FLOOR = 0.30           # detail share kept on perfectly smooth ground
ROUGH_FULL = 0.045           # local relief at which detail runs at full strength

# Standing water: where the persisted marsh field is at its deepest, the ground is
# open water rather than reeds — the same reading hydrology draws its meres from,
# so the raster and the lake rings agree about where the water is.
LAKE_MARSH = 0.22
LAKE_FULL = 0.30
LAKE_TINT = (0.478, 0.565, 0.604)     # the sea ramp's own shore colour
# How completely open water covers the ground under it. Near enough to one that the
# hillshade stops showing through: every other body of water on the map is flat
# colour (the sea branch never touches the blend table), and a lake that alone kept
# its hillshade read as a hollow in the ground rather than as water lying in one.
LAKE_DEEPEST = 0.96

# The coast's character in the shallows (V2 §4), keyed by the shoreline field's
# class codes (shore.CODE). What breaks the uniform halo: a beach gets a sand line,
# a delta a mud fan, a marsh shore bleeds into the water, and a cliff or fjord coast
# drops straight into deep colour with no bright band at all.
SAND_TINT = (0.749, 0.716, 0.596)
MUD_TINT = (0.639, 0.667, 0.545)      # the land ramp's own delta green
SAND_DEPTH = 0.014                    # how deep the sand line reaches
MUD_DEPTH = 0.018
REED_DEPTH = 0.022
HARD_SHORE_PUSH = 64                  # ramp steps a cliff/fjord shore skips ahead
CALM_SHORE_PULL = 22                  # ramp steps an estuary/shelter holds back
# Both faded out with depth, and inside the class spread's own reach, so the effect
# dies before the spread does — a constant push showed its boundary as blocks of
# lighter water three cells off every sheltered islet.
HARD_FADE_DEPTH = 0.11
CALM_FADE_DEPTH = 0.06
_DELTA, _ESTUARY, _MARSH_SHORE, _FJORD, _CLIFF = 1, 2, 3, 4, 5
_ROCKY, _SHELTERED, _BEACH = 6, 7, 8

# Woodland. Drawn as a darkening of the ground it stands on rather than as a fill, and
# broken up at the scale of a stand of trees, because a wood on a physical map is a
# texture over the relief and not a green shape laid on top of it. A flat fill hides the
# hillshade underneath, which throws away the only thing telling the reader that the wood
# is on a hillside.
CANOPY_TINT = (0.290, 0.365, 0.259)      # the colour a closed canopy tends towards
CANOPY_DEEPEST = 0.72                    # how far towards it the darkest wood goes
CANOPY_STIPPLE = 0.38                    # how much of the cover is texture
CANOPY_GRAIN = 2.6                       # lattice cells per unit of that texture
CANOPY_STRIDE = 1

# Marsh reads as a paler, greyer ground with a horizontal grain in it — the convention
# every printed map uses, and one that survives being small.
MARSH_TINT = (0.639, 0.671, 0.616)
MARSH_DEEPEST = 0.55

# Paper. Ink sits on a sheet with a tooth to it, and a perfectly even wash is the one
# thing in this renderer that no amount of relief work fixes: flat colour reads as a
# screen rather than as a printed sheet, and the eye finds the flatness before it finds
# the mountains. So every pixel is nudged by a fraction of a step — under one percent of
# luminance, far below where it reads as noise, and just enough that no two neighbouring
# pixels of open ground are quite the same colour.
#
# It is applied at the *index*, never to the three channels. Shifting which row a pixel
# reads from a table that already exists costs one list lookup; nudging three channels
# costs three multiplies and three clamps — measured at 0.10 µs/px against 0.56, which
# on a two-megapixel render is the difference between a rounding error and doubling the
# cost of `_paint`. It also cannot push a channel out of range, because every row of
# every table was already clamped when the table was built.
#
# Both numbers below are set to the same measured swing: plus or minus two channel units,
# which on land runs from 0.4% to 1.4% of the tint depending where on the ramp the
# pixel sits. They are expressed differently because the two hooks are: on land the
# grain moves the shade INDEX, and three rows of the shade table happen to be worth
# two channel units; on water there is no shade axis to move along, so the swing is
# stated directly. Set the water's from the land's measurement rather than by eye —
# at the three units this started at, the grain was 3.8% of the deep sea's own value
# against 1% of the land's, and the water read as noise while the ground read as paper.
GRAIN = 3                 # shade steps of swing either way; three rows ≈ two units
SEA_GRAIN = 2.0           # channel units of swing on water, which has no shade axis
_GRAIN_SHIFT = 5          # a digest byte, right-shifted, is the sea's grain bucket
_GRAIN_LEVELS = 1 << (8 - _GRAIN_SHIFT)   # derived, so the two can never disagree
_GRAIN_DIGEST = 64        # bytes per digest, and so pixels covered by one

_SHADE_STEPS = 256
_TINT_STEPS = 256


@dataclass
class Relief:
    """A rendered image, and the surface it was rendered from."""

    width: int
    height: int
    pixels: bytearray
    scale: int
    surface: array               # the interpolated height field, render resolution
    land: bytearray              # 1 where the render says land, at render resolution

    def sample(self, x: int, y: int) -> tuple[int, int, int]:
        k = (y * self.width + x) * 3
        return self.pixels[k], self.pixels[k + 1], self.pixels[k + 2]

    def land_share(self) -> float:
        return sum(self.land) / len(self.land) if self.land else 0.0


def render(grid: Grid, *, elevation: Field, seed: str, scale: int = SCALE,
           sea_level: float = 0.0, detail: float = DETAIL,
           relief_gain: float = RELIEF_GAIN, canopy: Field | None = None,
           marsh: Field | None = None, flow: Field | None = None,
           shoreline: Field | None = None) -> Relief:
    """Shade a height field into an image several times the lattice's resolution.

    `elevation` is one continuous field over the whole map, sea floor included, and
    `sea_level` is where the water stands in it. There is deliberately no land mask: a
    mask is a decision made at lattice resolution, and a coastline drawn from one can
    never be finer than the lattice however much the picture is enlarged. Reading the
    shore off the interpolated surface instead means it is a contour of a continuous
    field — so it has inlets and spits a few pixels across, which is what a coastline
    looks like, and the shelf shading is real depth rather than distance from a mask.

    `flow` is the erosion's own drainage, when the world carries it: valleys are
    carved into the drawn surface along it, which is what makes the network legible
    as structure rather than trusting the lattice-scale cuts to survive upsampling.
    """
    surface, size, width, shore_codes = _surface(
        grid, elevation, seed, scale, detail, sea_level, flow, shoreline)
    cover = _cover(grid, canopy, marsh, seed, scale, width)
    grain = _grain(seed, width * width)
    return _paint(surface, width, scale, sea_level, relief_gain, grain, cover,
                  shore_codes)


def _cover(grid: Grid, canopy: Field | None, marsh: Field | None, seed: str,
           scale: int, width: int) -> tuple[array, array] | None:
    """Woodland and marsh, upsampled and given a grain, ready to darken the ground.

    Interpolated the same way the surface is, and then broken up by a texture at the
    scale of a stand of trees. That texture is what stops a wood being a shape: at the
    edge of one, where the density is falling through the middle of its range, the grain
    is what decides cell by cell whether there are trees here — so the boundary comes out
    as a scatter thinning into open ground rather than as a line, which is what the edge
    of a wood looks like from above and what a fill can never be.
    """
    if canopy is None and marsh is None:
        return None
    size = grid.size
    inverse = 1.0 / scale
    limit = size - 1.001
    flat_canopy = ([value for row in canopy for value in row]
                   if canopy is not None else None)
    flat_marsh = ([value for row in marsh for value in row]
                  if marsh is not None else None)
    stipple = noise.field(f"{seed}|canopy-grain", size, wavelength=CANOPY_GRAIN,
                          octaves=3, stride=CANOPY_STRIDE)
    flat_stipple = [value for row in stipple for value in row]

    woods = array("f", bytes(4 * width * width))
    fens = array("f", bytes(4 * width * width))
    for py in range(width):
        v = (py + 0.5) * inverse - 0.5
        if v < 0.0:
            v = 0.0
        elif v > limit:
            v = limit
        j = int(v)
        fy = v - j
        base = py * width
        row = j * size
        below = row + size
        for px in range(width):
            u = (px + 0.5) * inverse - 0.5
            if u < 0.0:
                u = 0.0
            elif u > limit:
                u = limit
            i = int(u)
            fx = u - i
            a, b = row + i, below + i
            if flat_canopy is not None:
                top = flat_canopy[a] + (flat_canopy[a + 1] - flat_canopy[a]) * fx
                low = flat_canopy[b] + (flat_canopy[b + 1] - flat_canopy[b]) * fx
                density = top + (low - top) * fy
                if density > 0.0:
                    top = flat_stipple[a] + (flat_stipple[a + 1] - flat_stipple[a]) * fx
                    low = flat_stipple[b] + (flat_stipple[b + 1] - flat_stipple[b]) * fx
                    grain = top + (low - top) * fy
                    density *= (1.0 - CANOPY_STIPPLE) + 2.0 * CANOPY_STIPPLE * grain
                    woods[base + px] = 1.0 if density > 1.0 else density
            if flat_marsh is not None:
                top = flat_marsh[a] + (flat_marsh[a + 1] - flat_marsh[a]) * fx
                low = flat_marsh[b] + (flat_marsh[b + 1] - flat_marsh[b]) * fx
                fens[base + px] = top + (low - top) * fy
    return woods, fens


# ---- the surface ---------------------------------------------------------------


def _surface(grid: Grid, elevation: Field, seed: str, scale: int, detail: float,
             sea_level: float, flow: Field | None = None,
             shoreline: Field | None = None
             ) -> tuple[array, int, int, bytearray | None]:
    """Interpolate the lattice up, warping the sample point and adding fine relief.

    Two things are going on and they fix different halves of the same problem.

    The interpolation is cubic, because the shading downstream is a derivative of this
    surface: a bilinear upsample has a derivative that jumps at every cell boundary, so
    the hillshade draws the lattice as a quilt of facets whatever else is done. Catmull-
    Rom is continuous in the first derivative, which removes them.

    The warp then displaces the sample point by a fraction of a cell, by a field that
    itself varies smoothly. That destroys the *regularity* of what is left without
    disturbing the shape: a coastline gains inlets a pixel or two across, and a ridge
    stops being a polyline. It was doing both jobs before and could only do one.

    Both the warp and the detail are computed on their own coarse grids and interpolated,
    not evaluated per pixel. A two-megapixel render is two million samples, each of which
    would be a dozen hashes; the fields they come from vary over several lattice cells,
    so sampling them at that resolution buys nothing but minutes.
    """
    size = grid.size
    width = size * scale
    inverse = 1.0 / scale

    warp_x, warp_y, warp_span = _warp_fields(size, seed)
    fine, fine_span = _detail_field(size, seed)
    flat = [value for row in elevation for value in row]
    # The drainage, softened to a carve strength per cell, and the ground's own
    # ruggedness — both sampled at the same warped point as the height, so the
    # valleys land in the valleys and the quiet lands on the plains.
    carve = _carve_field(size, flat, flow) if flow is not None else None
    rough = _ruggedness(size, flat)
    # The coast's class codes ride along, nearest-sampled at the same warped point
    # as the height — a label cannot be blended, but it can be warped, and it has
    # to be: the drawn shore comes off the warped surface, so an unwarped class
    # boundary shows as cell-scale rectangles against it.
    flat_shore = ([value for row in shoreline for value in row]
                  if shoreline is not None else None)
    codes = bytearray(width * width) if flat_shore is not None else None

    surface = array("f", bytes(4 * width * width))
    warp_reach = 1.0 / WARP_SCALE
    detail_reach = 1.0 / DETAIL_STRIDE
    limit = size - 1.001
    warp_limit = warp_span - 1.001
    fine_limit = fine_span - 1.001

    for py in range(width):
        v = (py + 0.5) * inverse - 0.5
        base = py * width
        wv = v * warp_reach
        # Everything that depends only on the row is hoisted: the warp field's row index
        # and fraction are the same for every pixel across, and this loop runs a couple
        # of million times.
        if wv < 0.0:
            wv = 0.0
        elif wv > warp_limit:
            wv = warp_limit
        wj = int(wv)
        wfy = wv - wj
        wrow = wj * warp_span
        wnext = wrow + warp_span
        for px in range(width):
            u = (px + 0.5) * inverse - 0.5
            wu = u * warp_reach
            if wu < 0.0:
                wu = 0.0
            elif wu > warp_limit:
                wu = warp_limit
            wi = int(wu)
            wfx = wu - wi
            a, b = wrow + wi, wnext + wi
            top = warp_x[a] + (warp_x[a + 1] - warp_x[a]) * wfx
            low = warp_x[b] + (warp_x[b + 1] - warp_x[b]) * wfx
            dx = (top + (low - top) * wfy - 0.5) * WARP2
            top = warp_y[a] + (warp_y[a + 1] - warp_y[a]) * wfx
            low = warp_y[b] + (warp_y[b + 1] - warp_y[b]) * wfx
            dy = (top + (low - top) * wfy - 0.5) * WARP2

            sx = u + dx
            sy = v + dy
            if sx < 0.0:
                sx = 0.0
            elif sx > limit:
                sx = limit
            if sy < 0.0:
                sy = 0.0
            elif sy > limit:
                sy = limit
            i, j = int(sx), int(sy)
            fx, fy = sx - i, sy - j
            # Catmull-Rom, and not bilinear, because shading is a *derivative* of this
            # surface and bilinear's derivative is piecewise constant: it jumps at every
            # cell boundary, so the hillshade draws the simulation lattice as a quilt of
            # hard-edged facets. Measured on the generator's own field, the luminance
            # step across a cell boundary came out three and a half times the step
            # inside one; with this it is 1.2, which is nothing.
            #
            # The warp above cannot fix it and the docstring used to claim it did. The
            # warp moves the boundaries and destroys the *pattern* they make, which is
            # worth having, but a displaced discontinuity is still a discontinuity.
            # Easing the fractions instead — the obvious one-line answer — is worse: it
            # makes the derivative zero at the boundaries rather than continuous, and
            # trades the quilt for a regular waffle. Four cubic weights an axis is the
            # thing that actually works, and it is all multiply-add.
            ax = ((-0.5 * fx + 1.0) * fx - 0.5) * fx
            bx = ((1.5 * fx - 2.5) * fx) * fx + 1.0
            cx = ((-1.5 * fx + 2.0) * fx + 0.5) * fx
            dx = (0.5 * fx - 0.5) * fx * fx
            ay = ((-0.5 * fy + 1.0) * fy - 0.5) * fy
            by = ((1.5 * fy - 2.5) * fy) * fy + 1.0
            cy = ((-1.5 * fy + 2.0) * fy + 0.5) * fy
            dy = (0.5 * fy - 0.5) * fy * fy
            # The clamp on sx and sy already puts i and j inside [0, size - 2], so only
            # the outer two taps of the four can leave the lattice.
            left = i - 1 if i else 0
            right = i + 2 if i + 2 < size else size - 1
            middle = j * size
            above = middle - size if j else middle
            under = middle + size
            lower = under + size if j + 2 < size else under
            height = (
                ay * (flat[above + left] * ax + flat[above + i] * bx
                      + flat[above + i + 1] * cx + flat[above + right] * dx)
                + by * (flat[middle + left] * ax + flat[middle + i] * bx
                        + flat[middle + i + 1] * cx + flat[middle + right] * dx)
                + cy * (flat[under + left] * ax + flat[under + i] * bx
                        + flat[under + i + 1] * cx + flat[under + right] * dx)
                + dy * (flat[lower + left] * ax + flat[lower + i] * bx
                        + flat[lower + i + 1] * cx + flat[lower + right] * dx))

            # Bilinear taps into the per-cell carve and ruggedness fields, at the
            # same warped point the height came from.
            a = j * size + i
            b = a + size
            if flat_shore is not None:
                near = flat_shore[(b if fy > 0.5 else a) + (1 if fx > 0.5 else 0)]
                if near > 0.5:
                    codes[base + px] = int(near + 0.5)
            if carve is not None:
                top = carve[a] + (carve[a + 1] - carve[a]) * fx
                low = carve[b] + (carve[b + 1] - carve[b]) * fx
                cut = top + (low - top) * fy
                if cut > 0.0:
                    over_sea = height - sea_level
                    if over_sea > 0.0:
                        # Faded in above the waterline, so carving a mouth cannot
                        # move the raster shore off the drawn coastline.
                        if over_sea < CARVE_FADE:
                            cut *= over_sea / CARVE_FADE
                        height -= cut
            top = rough[a] + (rough[a + 1] - rough[a]) * fx
            low = rough[b] + (rough[b + 1] - rough[b]) * fx
            ruggedness = top + (low - top) * fy
            grain = detail * (QUIET_FLOOR + (1.0 - QUIET_FLOOR) * ruggedness)

            gx, gy = sx * detail_reach, sy * detail_reach
            if gx > fine_limit:
                gx = fine_limit
            if gy > fine_limit:
                gy = fine_limit
            gi, gj = int(gx), int(gy)
            gfx, gfy = gx - gi, gy - gj
            a = gj * fine_span + gi
            b = a + fine_span
            top = fine[a] + (fine[a + 1] - fine[a]) * gfx
            low = fine[b] + (fine[b + 1] - fine[b]) * gfx
            # Detail fades out below the waterline. The sea on a physical map is not
            # textured, and fractal grain left running through it reads as cloud.
            over = height - sea_level
            if over > 0.0:
                if over < SHALLOW:
                    over /= SHALLOW
                else:
                    over = 1.0
                surface[base + px] = (height
                                      + (top + (low - top) * gfy - 0.5) * grain * over)
            else:
                surface[base + px] = height
    return surface, size, width, codes


def _carve_field(size: int, flat: list[float],
                 flow: Field) -> list[float]:
    """How deep the drawn surface is cut at each cell, from the drainage through it.

    `flow` arrives in root space (see the terrain store), which is also the shape a
    carve wants: linear between the two marks there means a trunk is markedly
    deeper than the stream that feeds it without the headwaters vanishing entirely.
    """
    span = CARVE_FLOW_HIGH - CARVE_FLOW_LOW
    out = [0.0] * (size * size)
    k = 0
    for row in flow:
        for value in row:
            if value > CARVE_FLOW_LOW:
                t = (value - CARVE_FLOW_LOW) / span
                if t > 1.0:
                    t = 1.0
                # smoothstep, so the carve eases in rather than kinking.
                out[k] = CARVE * t * t * (3.0 - 2.0 * t)
            k += 1
    return out


def _ruggedness(size: int, flat: list[float]) -> list[float]:
    """Local relief per cell, 0 on a billiard table and 1 in real mountains.

    What the fine detail is scaled by: fractal grain everywhere reads as porridge,
    and the difference between country that has texture and country that does not is
    itself information — it is how a reader tells the highlands from the plain.
    """
    out = [0.0] * (size * size)
    inverse = 1.0 / ROUGH_FULL
    for j in range(size):
        base = j * size
        above = base - size if j else base
        below = base + size if j + 1 < size else base
        for i in range(size):
            left = i - 1 if i else 0
            right = i + 1 if i + 1 < size else i
            lowest = highest = flat[base + i]
            for k in (above + left, above + i, above + right,
                      base + left, base + right,
                      below + left, below + i, below + right):
                value = flat[k]
                if value < lowest:
                    lowest = value
                elif value > highest:
                    highest = value
            t = (highest - lowest) * inverse
            out[base + i] = 1.0 if t > 1.0 else t
    return out


def _warp_fields(size: int, seed: str) -> tuple[list[float], list[float], int]:
    """Two smooth fields that displace the sample point, on a coarse grid of their own."""
    span = int(size / WARP_SCALE) + 3
    xs = [noise.fbm(f"{seed}|warpx", cx, cy, octaves=3)
          for cy in range(span) for cx in range(span)]
    ys = [noise.fbm(f"{seed}|warpy", cx, cy, octaves=3)
          for cy in range(span) for cx in range(span)]
    return xs, ys, span


def _detail_field(size: int, seed: str) -> tuple[list[float], int]:
    """Fine relief, sampled every couple of lattice cells and interpolated between."""
    span = int(size / DETAIL_STRIDE) + 3
    values = [noise.fbm(f"{seed}|grain", cx / DETAIL_SCALE, cy / DETAIL_SCALE, octaves=4)
              for cy in range(span) for cx in range(span)]
    return values, span


# ---- the light and the colour --------------------------------------------------


def _paint(surface: array, width: int, scale: int, sea_level: float,
           relief_gain: float, grain: bytes,
           cover: tuple[array, array] | None = None,
           shore_codes: bytearray | None = None) -> Relief:
    """Shade and tint the surface, one pass, through a precomputed blend table."""
    # The three lights are summed into one vector before the loop, and that vector is
    # renormalised: shading is linear in the light, so a key, a fill and a cross key
    # dotted separately and added is exactly one dot product against their sum — three
    # times the arithmetic for the same picture. Renormalising is what keeps the
    # exposure where the ramp was measured; without it, adding a light just makes the
    # map brighter and the shade table's top rows unreachable.
    cx, cy, cz = CROSS_LIGHT
    fx, fy, fz = FILL_LIGHT
    lx, ly, lz = LIGHT
    fill = FILL_SHARE
    cross = CROSS_SHARE
    keep = 1.0 - fill - cross
    sx = lx * keep + fx * fill + cx * cross
    sy = ly * keep + fy * fill + cy * cross
    sz = lz * keep + fz * fill + cz * cross
    sun = math.sqrt(sx * sx + sy * sy + sz * sz)
    sx, sy, sz = sx / sun, sy / sun, sz / sun
    gain = relief_gain * scale       # the normal is per pixel, so the run is a pixel wide
    # Curvature is read across a fixed fraction of a lattice cell, so its gain is a
    # plain constant — the reach carries the resolution instead.
    reach = int(CURVE_REACH * scale) or 1
    hollow = VALLEY_SHADE
    crest = RIDGE_LIGHT

    highest = max(surface)
    span = highest - sea_level
    if span <= 0.0:
        span = 1.0
    tint_step = (_TINT_STEPS - 1) / span
    depth_step = (_TINT_STEPS - 1) / SHELF_DEPTH

    blend = _blend_table()
    sea_table = _sea_table()
    speck = _grain_steps()

    canopy_r = int(CANOPY_TINT[0] * 255.0 + 0.5)
    canopy_g = int(CANOPY_TINT[1] * 255.0 + 0.5)
    canopy_b = int(CANOPY_TINT[2] * 255.0 + 0.5)
    marsh_r = int(MARSH_TINT[0] * 255.0 + 0.5)
    marsh_g = int(MARSH_TINT[1] * 255.0 + 0.5)
    marsh_b = int(MARSH_TINT[2] * 255.0 + 0.5)
    lake_r = int(LAKE_TINT[0] * 255.0 + 0.5)
    lake_g = int(LAKE_TINT[1] * 255.0 + 0.5)
    lake_b = int(LAKE_TINT[2] * 255.0 + 0.5)
    sand_r = int(SAND_TINT[0] * 255.0 + 0.5)
    sand_g = int(SAND_TINT[1] * 255.0 + 0.5)
    sand_b = int(SAND_TINT[2] * 255.0 + 0.5)
    mud_r = int(MUD_TINT[0] * 255.0 + 0.5)
    mud_g = int(MUD_TINT[1] * 255.0 + 0.5)
    mud_b = int(MUD_TINT[2] * 255.0 + 0.5)

    pixels = bytearray(width * width * 3)
    land = bytearray(width * width)
    root = math.sqrt
    last = width - 1
    top_shade = _SHADE_STEPS - 1
    top_tint = _TINT_STEPS - 1

    for py in range(width):
        base = py * width
        above = (base - width) if py else base
        below = (base + width) if py < last else base
        # The curvature's own rows, a fixed fraction of a cell away.
        over = (py - reach) if py >= reach else 0
        under = (py + reach) if py + reach <= last else last
        over *= width
        under *= width
        out = base * 3
        for px in range(width):
            k = base + px
            height = surface[k]
            if height <= sea_level:
                depth = sea_level - height
                shelf = int(depth * depth_step)
                code = shore_codes[k] if shore_codes is not None else 0
                if code:
                    if code in (_CLIFF, _FJORD) and depth < HARD_FADE_DEPTH:
                        # A wall into the water: no bright band at all.
                        shelf += int(HARD_SHORE_PUSH
                                     * (1.0 - depth / HARD_FADE_DEPTH))
                    elif (code in (_ESTUARY, _SHELTERED)
                          and depth < CALM_FADE_DEPTH):
                        shelf -= int(CALM_SHORE_PULL
                                     * (1.0 - depth / CALM_FADE_DEPTH))
                if shelf > top_tint:
                    shelf = top_tint
                elif shelf < 0:
                    shelf = 0
                # The water needs its own hook: the sea branch never reads the blend
                # table, and grain that stops at the waterline is a seam along every
                # coast — the one place on the map the eye is already looking.
                index = (shelf * _GRAIN_LEVELS + (grain[k] >> _GRAIN_SHIFT)) * 3
                red = sea_table[index]
                green = sea_table[index + 1]
                blue = sea_table[index + 2]
                if code and depth < REED_DEPTH:
                    if code == _BEACH and depth < SAND_DEPTH:
                        pull = 0.55 * (1.0 - depth / SAND_DEPTH)
                        red += int((sand_r - red) * pull)
                        green += int((sand_g - green) * pull)
                        blue += int((sand_b - blue) * pull)
                    elif code == _DELTA and depth < MUD_DEPTH:
                        pull = 0.6 * (1.0 - depth / MUD_DEPTH)
                        red += int((mud_r - red) * pull)
                        green += int((mud_g - green) * pull)
                        blue += int((mud_b - blue) * pull)
                    elif code == _MARSH_SHORE:
                        pull = 0.45 * (1.0 - depth / REED_DEPTH)
                        red += int((marsh_r - red) * pull)
                        green += int((marsh_g - green) * pull)
                        blue += int((marsh_b - blue) * pull)
                pixels[out] = red
                pixels[out + 1] = green
                pixels[out + 2] = blue
                out += 3
                continue
            land[k] = 1
            left = surface[k - 1] if px else height
            right = surface[k + 1] if px < last else height
            up = surface[above + px]
            down = surface[below + px]
            # The normal of the surface, unnormalised: (-dz/dx, -dz/dy, 1) with the
            # height exaggerated. No trigonometry — a normal is a vector, a light is a
            # vector, and shading is their dot product.
            nx = (left - right) * gain
            ny = (up - down) * gain
            inverse = 1.0 / root(nx * nx + ny * ny + 1.0)
            level = (nx * sx + ny * sy + sz) * inverse
            # How the ground bends, read across a fraction of a cell. A slope tells
            # the light where it faces; only the curvature tells a valley floor from
            # an open plain tilted the same way, and it is the valleys this map most
            # needs to show. Positive is a hollow — the pixel lies below the mean of
            # the ground around it — and darkens; negative is a crest, and catches.
            west = surface[base + (px - reach if px >= reach else 0)]
            east = surface[base + (px + reach if px + reach <= last else last)]
            bend = ((west + east + surface[over + px] + surface[under + px]) * 0.25
                    - height)
            if bend > 0.0:
                lit = bend * hollow
                level -= lit if lit < CURVE_LIMIT else CURVE_LIMIT
            else:
                lit = -bend * crest
                level += lit if lit < CURVE_LIMIT else CURVE_LIMIT
            if level < 0.0:
                level = 0.0
            shade = int(level * top_shade) + speck[grain[k]]
            if shade > top_shade:
                shade = top_shade
            elif shade < 0:
                shade = 0
            tint = int((height - sea_level) * tint_step)
            if tint < 0:
                tint = 0
            elif tint > top_tint:
                tint = top_tint
            index = (tint * _SHADE_STEPS + shade) * 3
            red, green, blue = blend[index], blend[index + 1], blend[index + 2]
            if cover is not None:
                fen = cover[1][k]
                if fen > 0.01:
                    pull = fen * MARSH_DEEPEST
                    red += int((marsh_r - red) * pull)
                    green += int((marsh_g - green) * pull)
                    blue += int((marsh_b - blue) * pull)
                    if fen > LAKE_MARSH:
                        # The deep core of a broad fen is open water — the same
                        # reading hydrology draws its meres from, so the raster
                        # and the lake rings agree about where the water stands.
                        #
                        # Standing water takes the sea's own shallow colour and the
                        # sea's flatness with it: the pull runs toward the LIT lake
                        # tint, so the hillshade under it is cancelled rather than
                        # showing through. A lake was the only water on the map that
                        # was hillshaded, and a shaded pond in a lit valley reads as
                        # a dent in the ground rather than as water lying in it.
                        t = (fen - LAKE_MARSH) / (LAKE_FULL - LAKE_MARSH)
                        if t > 1.0:
                            t = 1.0
                        wet = t * t * (3.0 - 2.0 * t) * LAKE_DEEPEST
                        red += int((lake_r - red) * wet)
                        green += int((lake_g - green) * wet)
                        blue += int((lake_b - blue) * wet)
                wood = cover[0][k]
                if wood > 0.01:
                    # The wood *darkens* the ground rather than replacing it, so the
                    # hillshade under it survives and a forest on a hillside still reads
                    # as being on a hillside.
                    pull = wood * CANOPY_DEEPEST
                    red += int((canopy_r - red) * pull)
                    green += int((canopy_g - green) * pull)
                    blue += int((canopy_b - blue) * pull)
            pixels[out] = red
            pixels[out + 1] = green
            pixels[out + 2] = blue
            out += 3

    return Relief(width=width, height=width, pixels=pixels, scale=scale,
                  surface=surface, land=land)


def _ramp(stops, steps: int) -> list[tuple[float, float, float]]:
    """Resample a list of colour stops to an evenly spaced table."""
    out: list[tuple[float, float, float]] = []
    for n in range(steps):
        t = n / (steps - 1)
        low = stops[0]
        high = stops[-1]
        for a, b in zip(stops, stops[1:], strict=False):
            if a[0] <= t <= b[0]:
                low, high = a, b
                break
        width = high[0] - low[0]
        f = (t - low[0]) / width if width else 0.0
        out.append((low[1] + (high[1] - low[1]) * f,
                    low[2] + (high[2] - low[2]) * f,
                    low[3] + (high[3] - low[3]) * f))
    return out


@lru_cache(maxsize=1)
def _blend_table() -> bytes:
    """Every tint crossed with every shade level, worked out once — and kept.

    Cached because it is pure and was being rebuilt on every render: a quarter of a
    megabyte of identical arithmetic per request, for a table that never changes.

    Sixty-five thousand colours is a quarter of a megabyte and a few milliseconds. Doing
    the same arithmetic inside the pixel loop is two million multiplies and clamps per
    channel, which on a two-megapixel render is most of the render.
    """
    tints = _ramp(LAND_RAMP, _TINT_STEPS)
    out = bytearray(_TINT_STEPS * _SHADE_STEPS * 3)
    n = 0
    top = _TINT_STEPS - 1
    hz = HAZE_TINT
    for index, (r, g, b) in enumerate(tints):
        # The high ground is seen through more air. The table is indexed by height, so
        # the haze is applied here rather than per pixel — the whole term costs 256
        # lerps once instead of two million in the loop. It fades in above HAZE_FROM
        # so the lowlands, which is most of every map, are untouched.
        share = index / top
        if share > HAZE_FROM:
            veil = HAZE * (share - HAZE_FROM) / (1.0 - HAZE_FROM)
            r += (hz[0] - r) * veil
            g += (hz[1] - g) * veil
            b += (hz[2] - b) * veil
        for step in range(_SHADE_STEPS):
            level = step / (_SHADE_STEPS - 1)
            factor = SHADE_FLOOR + (SHADE_CEILING - SHADE_FLOOR) * level
            for channel in (r, g, b):
                value = int(channel * factor * 255.0 + 0.5)
                out[n] = 0 if value < 0 else (255 if value > 255 else value)
                n += 1
    return bytes(out)


@lru_cache(maxsize=1)
def _sea_table() -> bytes:
    """Every depth crossed with every level of the paper's grain.

    Water has no shade axis to borrow — the sea is drawn from depth alone — so the
    grain is crossed into the depth table instead, which keeps it at one lookup in the
    loop. Depth is clamped before the lookup, so most of the open sea sits pinned at
    the deepest row: nudging the *index* there would be nudging against a stop, and
    the deep water would come out glassy while the shallows had a tooth.
    """
    out = bytearray(_TINT_STEPS * _GRAIN_LEVELS * 3)
    n = 0
    middle = (_GRAIN_LEVELS - 1) * 0.5
    for r, g, b in _ramp(SEA_RAMP, _TINT_STEPS):
        for level in range(_GRAIN_LEVELS):
            lift = (level - middle) / middle * SEA_GRAIN
            for channel in (r, g, b):
                value = int(channel * 255.0 + 0.5 + lift)
                out[n] = 0 if value < 0 else (255 if value > 255 else value)
                n += 1
    return bytes(out)


def _grain(seed: str, count: int) -> bytes:
    """The tooth of the paper: one deterministic byte per pixel.

    Bulk digests, not `noise.unit` per pixel. The noise field is a smooth function of
    position and this wants precisely the opposite — an uncorrelated speck at pixel
    scale, which is what a hash gives away for nothing. One 64-byte digest covers 64
    pixels, so a two-megapixel sheet costs about thirty thousand digests: 0.017 µs a
    pixel, against 2.1 for a cold `noise.unit`.

    Keyed on the world's seed, so the paper is the same sheet every time this world is
    drawn and a different one for the next world.
    """
    key = f"{seed}|paper".encode()
    out = bytearray()
    block = 0
    while len(out) < count:
        out += hashlib.blake2b(key + block.to_bytes(4, "big"),
                               digest_size=_GRAIN_DIGEST).digest()
        block += 1
    return bytes(out[:count])


@lru_cache(maxsize=1)
def _grain_steps() -> tuple[int, ...]:
    """A digest byte read as a signed nudge, in shade steps."""
    span = GRAIN * 2 + 1
    return tuple(value * span // 256 - GRAIN for value in range(256))
