"""Neufert-derived per-room dimensional standards.

Keyed by the finished room NAME slicer.py actually emits (see
`zones.ZONE_DISPLAY` and the `_slice_*` composite cutters in slicer.py) —
not by the coarser `Category` or macro `ZoneId`. A macro zone like
"kitchen_laundry" covers two rooms (Kitchen, Laundry) with unrelated
dimensional envelopes, and Category "service" covers both Garage and
Laundry likewise, so neither is fine-grained enough for this table.

Values are drawn from Neufert's Architects' Data (residential room
minimums), mainly p44 table 1a (USA FHA minimum room sizes). Some entries
have no direct Neufert figure and are `# DERIVED (not in Neufert)` from
Neufert's component clearances (door swing, appliance runs, fixture
envelopes) instead — still sourced, just not a single published number.
Anything still not confidently sourced either way stays `# GUESS`.

Pure data: no imports from any other app module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# Below 1200mm two people cannot pass; 1200mm is also the wheelchair
# door-opening minimum (Neufert). Confirmed, not a guess.
DOOR_CLEAR_WIDTH_M = 0.9  # 900mm doorset: the wheelchair-access doorset minimum


# ---------------------------------------------------------------------------
# THE ARCHITECT'S ROOM AREA TABLE (min, max m2).
#
# SOURCE: the project architect, review round 3 (2026-08-01), supplied alongside
# his size diagnosis -- "you don't separate the rooms correctly, that's why we
# get the out-of-size issue" -- and a site-coverage target of ~40% (192 m2 on
# this 480 m2 plot). It is his table, not Neufert's and not the Posobie's: the
# entries below are a practitioner's buildable ranges for this house type, and
# where they disagree with a norm minimum the norm still wins (the norm is a
# floor; this table is a working envelope on top of it).
#
# This is the FIRST maximum area anywhere in the codebase. Until Phase 1 the
# only ceiling was solver.AREA_HI, a multiple of a per-zone TARGET rather than
# anything about the room type -- which is why a 21.25 m2 corridor and a 15.0 m2
# master bedroom could coexist in one plan and nothing could see it.
#
# Entries the slicer does not currently emit (Pantry, Technical Room, Terrace,
# Balcony) are recorded for completeness and deliberately NOT added to ROOMS --
# test_standards_cover_every_room_name_slicer_can_emit asserts ROOMS is exactly
# the emittable set, and that invariant is worth more than the placeholder.
ARCHITECT_AREA_BANDS: dict[str, tuple[float, float]] = {
    "Foyer": (4, 10),
    "Mudroom": (3, 8),
    "Living": (20, 45),
    "Dining": (12, 25),
    "Kitchen": (10, 22),
    "Pantry": (2, 6),                 # not emitted by the slicer
    "Master Bedroom": (16, 30),
    "Master Bathroom": (5, 12),
    "Walk-in Closet": (4, 12),
    "Bedroom 2": (12, 20),
    "Bedroom 3": (12, 20),
    # BATHROOM: HIS NUMBER, VERBATIM -- and it stands. It was briefly blamed for
    # an infeasibility that turned out to be OUR envelope, so read this before
    # touching it; the temptation to "just raise it" is the whole point.
    #
    # _slice_children puts the Bathroom in the middle band spanning the children
    # zone's full WIDTH -- and that is FORCED, not a style choice. Brute force
    # over all 96580 partitions of a rectangle into three rectangles: exactly
    # 16320 give all three rooms a wall on one shared vertical face (what CB3 /
    # _force_vertical_cover_center needs, since the corridor must lie strictly E
    # or W of the zone), and ALL 16320 are three full-width horizontal bands.
    # There is no 3-room cut with a narrow Bathroom. So the zone width times the
    # Bathroom's minimum DEPTH is what has to fit under this 9 m2 ceiling.
    #
    # That depth used to ceil-snap to 2.5 m, capping the zone at 9/2.5 = 3.6 ->
    # 3.5 m and making 8.75 / 10.00 the only realisable areas -- with nothing in
    # between, exact tiling (COVERAGE_MIN = 1.00) was INFEASIBLE at every target
    # 184-220 on both presets. The 2.5 came from a min_h_m of 2.2 that the
    # Bathroom RoomStandard itself flagged as "not separately sourced". Sourcing
    # it properly (the fixture run: 1.7 m deep, see the RoomStandard below) drops
    # the snap to 2.0, admits a 4.0 m wide zone, and both presets solve with the
    # Bathroom at 4.0 x 2.0 = 8.00 m2 -- inside his band, with headroom.
    #
    # A 10.0 override was written here and then REVERTED. Changing his numbers is
    # his ruling to make, and it turned out we never needed it.
    "Bathroom": (4, 9),
    "Guest WC": (1.5, 3.5),
    "Office": (10, 20),
    "Laundry": (4, 10),
    "Corridor": (6, 25),
    # GARAGE OVERRIDE -- his table says 18, this table says 29.2.
    # His 18 m2 floor is a SINGLE bay. The brief specifies a double garage and
    # the Garage entry below already encodes the two-car figure (the example
    # program's own 5.4 x 5.4 = 29.16 -> 29.2 m2). Adopting 18 verbatim would
    # have silently downgraded the house to one car -- measured in the Phase 0
    # audit, where the garage fell 31.50 -> 24.50 m2 at target 192 and to 18.00
    # at the packing floor. His table has NO two-car row, so this override is
    # PENDING HIS CONFIRMATION; if he says 18 is deliberate, drop the override
    # and the two-car guarantee with it.
    "Garage": (29.2, 40),
    "Technical Room": (3, 8),         # not emitted by the slicer
    "Terrace": (10, 40),              # a projection, not a room in `rooms[]`
    "Balcony": (3, 12),               # not emitted by the slicer
}


# ---------------------------------------------------------------------------
# THE ARCHITECT'S THREE-VALUE MODEL AND DISTRIBUTION PRIORITY (round 4,
# 2026-08-04). Both rulings are quoted VERBATIM so nobody re-litigates them from
# a paraphrase, and the translation follows each quote.
#
# RULING 1 -- three values, not two:
#   "Eger sisteme yalniz Min ve Max verilse, alqoritm riyazi olaraq otaqlari ya
#    tam minimumda saxlayacaq (ev sixilacaq), ya da tesadufi olaraq maksimuma
#    qeder sisirdecek. Her otaq ucun Minimum, Ideal (Optimal) ve Maksimum
#    deyerler teyin etmekdir. Alqoritm elave sahani bolusdurende hedefi hemise
#    'Ideal saha' gostericisine catdirmaq olmalidir. Ideal olcuye catdiqdan
#    sonra sistem dayanmalidir."
#   -- Given only a Min and a Max the algorithm will mathematically either hold
#   every room at its exact minimum (the house shrinks) or inflate it at random
#   up to the maximum. Each room needs a Minimum, an IDEAL (optimum) and a
#   Maximum. When the algorithm distributes surplus area its goal must always be
#   to bring the room up to its ideal area. Once the ideal size is reached the
#   system must stop.
#
# RULING 2 -- distribution priority by room type:
#   "Sistemin butun artigi tekce komekci otaga vermesi menteqsizdir... otaq
#    novune gore prioritet sirasi."
#   -- It is illogical for the system to hand the entire surplus to the
#   auxiliary room alone; there must be a priority order by room type.
#     Tier 1 (social and living): Kitchen, Living Room, Master Bedroom -- expand
#       these first, because people spend most of the day there.
#     Tier 2 (private): children's bedrooms, office.
#     Tier 3 (kept at minimum): Laundry, Walk-in Closet, and other ancillary
#       rooms.
#
# MEASURED BEHAVIOUR BEFORE THIS COMMIT was the EXACT INVERSE of Ruling 2, in
# all three composite zones (roomy @192, gW_eN and gE_eN, workers=1, seed=1):
# Kitchen sat at its 10.00 floor while the Laundry took +4.00; Bedroom 2 and 3
# sat at 12.00 while the Bathroom took +3.92; the Master Bedroom took +0.50
# while the Walk-in Closet took +3.30. The coverage term is indifferent about
# WHICH room grows, so the composite cut decided -- and it decided by taking the
# first grid-legal candidate, which is the one that pins the ancillary strip to
# its own minimum DEPTH and hands it the zone's whole cross-dimension.
#
# RULING 3 -- corridor proportion -- is NOT implemented here and is deliberately
# out of scope for this commit; it is recorded in CLAUDE.md with its numbers.
# ---------------------------------------------------------------------------
#
# ROUND 5, 2026-08-04 -- HIS ANSWERS TO THE TWO OPEN QUESTIONS, PLUS THE
# COVERAGE INSTRUCTION. The two ">>> OPEN QUESTION" markers below are kept and
# marked ANSWERED rather than deleted, so the reasoning that produced the
# question survives next to his answer.
#
# RULING 4 -- site coverage:
#   "faiz defecesini 45e qaldirin goren ne effekt verecek"
#   -- raise the percentage to 45 and see what effect it has.
# 45% of the 480 m2 plot = 216 m2. The percentage he is raising is the ~40%
# figure of his round 3 (192/480), NOT the legal cap: Site.max_coverage_ratio
# stays 0.5 (240 m2), which is the ceiling the plan may not cross rather than a
# target. See SITE_COVERAGE_TARGET.


# The architect's SITE COVERAGE TARGET as a fraction of the plot (Ruling 4
# above). It is a DESIGN figure, and the only thing in the model it can actually
# drive is program.footprint_target_m2, a BRIEF number.
#
# *** RECORDED, NOT ADOPTED -- WE SHIPPED THE RUNG BELOW IT. *** He asked for
# 45% and asked what effect it has; the effect was measured in full on all three
# rungs of the footprint ladder (CLAUDE.md carries the table) and
# program_roomy.json now sits at 208.0 (43.33%), not 216.0 (45%). The arithmetic,
# because this is a deliberate departure from his number and must stay auditable:
#   - tier-1 m2 of shortfall closed per m2 of footprint bought is 0.77 for
#     201.50 -> 208.00 but only 0.375 for 208.00 -> 216.00;
#   - of the 8.00 m2 the top rung costs, 5.00 is waste -- half overshoots Living
#     PAST its ideal (35.00 against 32.50) and half inflates the Garage to its
#     40.00 ceiling (tier-3 excess 29.11 -> 31.61, where 208 leaves it untouched);
#   - at 216.00, footprint + terrace = 240.00, exactly the legal cap with zero
#     margin; at 208.00 it is 230.50, leaving 9.50;
#   - at 216.00 test_solver.py's center-cover negative control stops
#     discriminating (cover OFF already gives Corridor<->Bathroom 2.00 m and
#     direct access, proven at OPTIMAL, not a timeout). It still discriminates at
#     208. Returning it to strict-xfail purely to go green is not a move we make.
# THE HONEST COUNTER-ARGUMENT, recorded so the choice can be reopened: 216.00 is
# the only rung where the Kitchen ever leaves its 10.00 floor, and the Kitchen is
# the room Ruling 2 names FIRST. It is outweighed only because the Kitchen cannot
# reach its 16.00 ideal at ANY footprint -- see the composite-cut finding in
# CLAUDE.md -- so 8.00 m2 buys it 2.00 m2 and then it stalls again anyway.
#
# DO NOT CONFUSE IT WITH Site.max_coverage_ratio (default 0.5). That one is the
# hard legal cap (`fp.area <= floor(ratio * plot_cells)`) and is unchanged by
# this ruling. Measured 2026-08-04, roomy 20x24: at footprint_target_m2 = 192 the
# three candidate binders sit at 221.00 m2 (FOOTPRINT_HI), 240.00 m2
# (max_coverage_ratio) and 192.00 m2 (fp_dev's pull), and the solve still lands
# on 201.50 -- so NEITHER cap was ever the binder. See solver.py's fp_dev block.
SITE_COVERAGE_TARGET = 0.45

# Room name -> priority tier, per Ruling 2 above.
#
# >>> OPEN QUESTION FOR THE ARCHITECT (1 of 2) — DINING'S TIER. *** ANSWERED
# >>> 2026-08-04, round 5: TIER 1, confirmed. *** His words:
# >>>   "Yemekxana (Dining Room) mehz Tier 1-e aiddir. Onu Tier 1 kimi
# >>>    saxlamaginiz tamamile dogrudur, cunki o, metbex ve salon ile birlikde
# >>>    evin esas sosial ucluyunu teskil edir."
# >>> -- The dining room belongs to Tier 1. Keeping it as Tier 1 is entirely
# >>> correct, because together with the kitchen and the living room it forms the
# >>> house's core social trio. The inference below turned out to match his
# >>> intent; it is kept because it is why the question was asked, and because
# >>> the +7.50 m2 it holds is still the table's most consequential single entry.
#
# The three rooms in tier 1 and the two categories in tier 2 are HIS, verbatim.
# Tier 3 is the only list with an explicit catch-all ("and other ancillary
# rooms"), so every room he did not name falls there by his own wording --
# EXCEPT ONE.
#
# DINING IS THE ONE JUDGEMENT CALL, and it is a question for him (see the
# provenance report). He labelled tier 1 "social and living" and then listed
# three rooms; a dining room matches that CATEGORY but is absent from the LIST,
# and it is neither ancillary (tier 3's own examples are Laundry and Walk-in
# Closet) nor private (tier 2 is children's bedrooms and the office). Assigning
# it by his category rather than inventing a fourth tier is the least inventive
# reading available, but it is an inference, not his word. It is also the most
# consequential open question in this table: Dining holds +7.50 m2 above its
# floor today, and moving it to tier 2 or tier 3 would redirect roughly that
# much to the Kitchen instead.
PRIORITY_TIER: dict[str, int] = {
    # --- tier 1, social and living: HIS LIST, verbatim ---------------------
    "Kitchen": 1,
    "Living": 1,
    "Master Bedroom": 1,
    "Dining": 1,               # HIS WORD too, round 5 -- see above (was inferred)
    # --- tier 2, private: HIS LIST, verbatim -------------------------------
    "Bedroom 2": 2,
    "Bedroom 3": 2,
    "Bedroom": 2,              # slicer fallback names for the same room type
    "Children Bedroom": 2,
    "Office": 2,
    # --- tier 3, kept at minimum: his two examples, then his catch-all -----
    "Laundry": 3,              # his example
    "Walk-in Closet": 3,       # his example
    "Master Bathroom": 3,
    "Bathroom": 3,
    "Guest WC": 3,
    "Foyer": 3,
    "Mudroom": 3,
    "Corridor": 3,
    "Garage": 3,
    "Pantry": 3,
    "Technical Room": 3,
    "Terrace": 3,
    "Balcony": 3,
}

DEFAULT_TIER = 3  # an unlisted room is ancillary until he says otherwise


# Fraction of the architect's OWN band a room's ideal sits at, per tier.
#
# >>> OPEN QUESTION FOR THE ARCHITECT (2 of 2) — THE TWO FRACTIONS. f(1) = 1/2
# >>> and f(2) = 1/4 are OURS, not his. Only the tier ORDER is his, and f(3) = 0
# >>> is his wording ("kept at minimum") rather than a dial.
# >>> *** ANSWERED 2026-08-04, round 5: APPROVED AS-IS. *** His words:
# >>>   "Teklif etdiyiniz formula (Min + diapazonun faizi) alqoritmik baximdan
# >>>    cox agilli helldir... bu faiz bolgusu (Tier 1 ucun 50% elave ve s.) ela
# >>>    baslangicdir. Lakin gelecek tenzimlemelerde otaqlarin oz xarakterine
# >>>    gore... bu duslu bir az daha ferdilesdire bilerik. Helelik bu formul ile
# >>>    davam etmek tamamile meqbuldur."
# >>> -- The formula you propose (Min + a percentage of the range) is a very
# >>> clever solution algorithmically; this percentage split (50% extra for
# >>> Tier 1 and so on) is an excellent starting point. In future adjustments we
# >>> could individualise it a little more according to each room's own
# >>> character. For now, continuing with this formula is entirely acceptable.
# >>> ==> KEEP f AS A PER-TIER CONSTANT. A per-ROOM-CHARACTER f is his stated
# >>> FUTURE refinement and is explicitly NOT for now; do not start it here.
#
# WHY A RULE AND NOT A SOURCED TABLE. He gave no ideal column, and neither
# Neufert nor the Posobie supplies one either -- and that is not for want of
# looking. Neufert's residential figures are FURNITURE-CLEARANCE MINIMA (p44
# table 1a is literally "minimum room sizes"), which is exactly what
# standards.py already spends them on; re-deriving a "Master Bedroom" from a
# 2.00x1.60 bed with 0.75 m passes, a 0.60 m wardrobe run with 0.90 m dressing
# space and a seating corner lands at ~16.3 m2 -- i.e. it reproduces HIS 16 m2
# FLOOR, not an ideal above it. A furniture layout answers "how small may this
# room be", which is a different question from "how big should it be". So the
# ideal column cannot be sourced from the same place the floors were.
#
# WHAT WAS REJECTED, and why it matters: program_roomy.json's own per-space
# targets (living 30, dining 14, kitchen_laundry 20 ...) are the obvious
# alternative and they are WRONG for this job. They are the LLM's reading of one
# client brief, they are per ZONE rather than per room type, and reconcile.py
# rescales them to the footprint -- so an ideal built on them would vary with
# the footprint and with the brief. That is Phase 1's deleted adherence term
# wearing a new hat (see solver.py), and it is the specific thing the brief for
# this commit told us not to rebuild.
#
# THE RULE, stated so it can be argued with: ideal = floor + f(tier) x (ceiling
# - floor), rounded to 0.25 m2 (the 0.5 m grid's area quantum). It combines the
# only two pieces of room-type data he has actually given -- the band and the
# tier -- with one monotone parameter each. f(3) = 0 is HIS WORDING ("kept at
# minimum"), not a choice. f(1) = 1/2 and f(2) = 1/4 were a halving sequence and
# a GUESS -- the ORDER his, the two numbers ours. He was asked, and approved both
# the rule and the two numbers on 2026-08-04 (round 5, quoted above), so they are
# no longer a guess; what remains open is only his future per-room-character
# refinement, which he deferred.
IDEAL_BAND_FRACTION: dict[int, float] = {1: 0.50, 2: 0.25, 3: 0.00}

# Objective/scoring weight per grid CELL (0.25 m2) of deviation from ideal, by
# tier. Consumed by BOTH solver.py's zone term and slicer.py's cut scorer, from
# here, so the two cannot drift into disagreeing about the priority order.
#
# The shape is Ruling 1 + Ruling 2 in one piecewise-linear preference: a room's
# marginal value of area is TIER_W_BELOW while it is under its ideal and
# -TIER_W_ABOVE once it is over. The kink AT the ideal is "ideal olcuye
# catdiqdan sonra sistem dayanmalidir"; the ordering of the weights is the
# priority list.
#
# FOUR ORDERING PROPERTIES ARE LOAD-BEARING (test_standards asserts them):
#   1. BELOW is strictly decreasing in tier      -> surplus fills tier 1 first.
#   2. ABOVE is strictly INCREASING in tier      -> whatever is left over after
#      everyone reaches ideal settles on tier 1 rather than on the Laundry,
#      which is Ruling 2's actual complaint.
#   3. min(BELOW) > max(ABOVE)                   -> requirement (d): a tier-1
#      room is never starved to trim a tier-3 room's excess.
#   4. max(BELOW) < the net objective cost of growing the footprint by one cell
#      (5760 - 1200 = 4560 raw; see solver.py's fp_dev and coverage terms)
#      -> the term REDISTRIBUTES the house, it never inflates it.
TIER_W_BELOW: dict[int, int] = {1: 1200, 2: 800, 3: 400}
TIER_W_ABOVE: dict[int, int] = {1: 40, 2: 80, 3: 160}


def priority_tier(room: str) -> int:
    """The architect's Ruling 2 priority tier for a room name (1 = expand first,
    3 = keep at minimum). Unlisted rooms are ancillary (DEFAULT_TIER)."""
    return PRIORITY_TIER.get(_BAND_ALIAS.get(room, room), DEFAULT_TIER)


@dataclass(frozen=True)
class RoomStandard:
    min_w_m: float
    min_h_m: float
    min_area_m2: float
    max_aspect: float
    requires_exterior_wall: bool
    requires_circulation_access: bool
    # Upper area bound, from ARCHITECT_AREA_BANDS. `inf` means "no ceiling
    # stated" -- never 0, so an unpopulated room can only ever be unbounded, not
    # accidentally impossible. min_area_m2 above stays the NORM floor; the
    # architect's own minimum is applied separately (see architect_band) so the
    # two sources never get conflated -- that conflation is exactly what
    # [[clear_vs_nominal_dims]] cost us once already.
    max_area_m2: float = float("inf")
    # Neufert p47/p55: no door path may transit this room (worktop-cooker-sink
    # sequence for Kitchen; through-bedroom isn't a legal circulation mode at
    # all). Data only in this task — Task 3's spanning tree consumes it.
    no_through_traffic: bool = False
    allowed_ensuite_parents: tuple[str, ...] = ()
    # Mechanical extraction/venting (e.g. tumble-drier vapour) -- a DIFFERENT
    # requirement from requires_exterior_wall's daylight (KEO) test. Data
    # only for now: no validator gate reads this yet.
    requires_exterior_vent: bool = False


_BEDROOM = RoomStandard(
    # Neufert p44 table 1a (FHA minimum room sizes): min dim 2.44 m, min area 7.43 m2.
    min_w_m=2.44, min_h_m=2.44, min_area_m2=7.43, max_aspect=2.0,
    requires_exterior_wall=True, requires_circulation_access=True,
    no_through_traffic=True,  # p47: through-bedroom is not a legal circulation mode
)

ROOMS: dict[str, RoomStandard] = {
    "Living": RoomStandard(
        # Neufert p44 table 1a (FHA minimum room sizes): min dim 3.51 m, min area 14.9 m2.
        min_w_m=3.51, min_h_m=3.51, min_area_m2=14.9, max_aspect=2.0,
        requires_exterior_wall=True, requires_circulation_access=True,
    ),
    "Dining": RoomStandard(
        # Neufert p44 table 1a (FHA minimum room sizes): min dim 2.54 m, min area 9.3 m2.
        min_w_m=2.54, min_h_m=2.54, min_area_m2=9.3, max_aspect=2.2,
        # GUESS: Neufert doesn't mandate a dedicated exterior wall for dining;
        # commonly borrows the living room's daylight in an open plan.
        requires_exterior_wall=False,
        # Neufert p66: hall/corridor access is explicitly NOT necessary for
        # dining; kitchen access is essential instead (kitchen_laundry-dining
        # is already a hard REQUIRED_ADJ edge, so that's covered separately).
        requires_circulation_access=False,
    ),
    "Kitchen": RoomStandard(
        # min_area raised to min_w*min_h (2.4*3.0=7.2) so the column states the
        # real floor; the axis mins were already the binding constraint.
        min_w_m=2.4, min_h_m=3.0, min_area_m2=7.2, max_aspect=2.5,
        requires_exterior_wall=True, requires_circulation_access=True,
        # Neufert p55: the worktop-cooker-sink work sequence "should never be
        # broken by full-height fitments, doors or passageways."
        no_through_traffic=True,
    ),
    "Laundry": RoomStandard(
        # DERIVED (not in Neufert): 1.2 m appliance run + 1.0 m clearance
        # (min_w/min_area). min_h_m not separately sourced.
        # min_area raised to min_w*min_h (1.8*2.0=3.6). max_aspect 2.5: a
        # galley utility room is legitimately long and narrow (it is the thin
        # strip left when the kitchen keeps the dining side of the zone).
        min_w_m=1.8, min_h_m=2.0, min_area_m2=3.6, max_aspect=2.5,
        # NOT a daylight (KEO) requirement -- SNiP 2.08.01-89 classes Laundry
        # as auxiliary (podsobnoe) and explicitly permits windowless service
        # spaces on mechanical ventilation, unlike habitable rooms/kitchens.
        # Neufert p60's "against an outside wall" is about tumble-drier vapour
        # EXTRACTION, not KEO daylight, so it maps to requires_exterior_vent,
        # not requires_exterior_wall. That vent requirement is recorded here
        # but not yet enforced by any validator gate.
        requires_exterior_wall=False,
        requires_exterior_vent=True,
        requires_circulation_access=False,
        allowed_ensuite_parents=("Kitchen",),
    ),
    "Master Bedroom": RoomStandard(
        # Neufert p44 table 1a (FHA minimum room sizes): min dim 2.84 m, min area 11.15 m2.
        min_w_m=2.84, min_h_m=2.84, min_area_m2=11.15, max_aspect=2.0,
        requires_exterior_wall=True, requires_circulation_access=True,
        no_through_traffic=True,  # p47: through-bedroom is not a legal circulation mode
    ),
    "Master Bathroom": RoomStandard(
        # DERIVED (not in Neufert): 1700 bath + activity space; cf. Neufert
        # prefab bathroom unit 2875x2110mm. min_h_m not separately sourced.
        # min_area raised to min_w*min_h (2.1*2.2=4.62).
        min_w_m=2.1, min_h_m=2.2, min_area_m2=4.62, max_aspect=2.0,
        # GUESS: ensuite baths are commonly internal/mechanically vented.
        requires_exterior_wall=False, requires_circulation_access=False,
        allowed_ensuite_parents=("Master Bedroom",),
    ),
    "Walk-in Closet": RoomStandard(
        # DERIVED (not in Neufert) min_w: 600mm wardrobe + 900mm passage +
        # 600mm wardrobe. min_h_m/max_aspect remain GUESS — Neufert
        # dressing-room minimums vary widely with storage layout.
        # min_area raised to min_w*min_h (2.1*2.0=4.2).
        min_w_m=2.1, min_h_m=2.0, min_area_m2=4.2, max_aspect=2.5,
        requires_exterior_wall=False, requires_circulation_access=False,
        allowed_ensuite_parents=("Master Bedroom",),
    ),
    # Children's-zone slicer output: the un-split fallback is named
    # "Children Bedroom"; the split case names its two beds "Bedroom 2"/
    # "Bedroom 3" (see slicer.py::_slice_children). "Bedroom" is kept too
    # as the generic key. All four share one envelope.
    "Bedroom": _BEDROOM,
    "Bedroom 2": _BEDROOM,
    "Bedroom 3": _BEDROOM,
    "Children Bedroom": _BEDROOM,
    "Bathroom": RoomStandard(
        # ORIENTED, and deliberately so: min_w_m is ALONG the fixture run and
        # min_h_m is the depth in front of it. _slice_children is the only
        # producer of a "Bathroom" and it always lays the room out that way -- a
        # full-width middle band, shallow in y -- so the two are not
        # interchangeable here the way a near-square envelope let them be.
        #
        # Neufert's fixture run: bath 1500x700 end to end, basin >= 550x420
        # beside it, so the run wants ~2.4 m of wall. Depth is the 700 bath plus
        # ~1000 of activity space in front of it = 1.7 m. cf. the Neufert prefab
        # bathroom unit at 2875x2110.
        #
        # WAS 2.1 x 2.2 with min_area 4.62, carrying the note "min_h_m not
        # separately sourced" -- and that unsourced 2.2 was the whole problem.
        # It ceil-snapped the band depth to 2.5 m, which multiplied by the zone
        # width blew through the architect's 9 m2 ceiling at any width >= 4.0 m
        # and made the entire plan INFEASIBLE (see ARCHITECT_AREA_BANDS above).
        # 1.7 snaps to 2.0 and the contradiction disappears. max_aspect 2.0 is
        # what now caps the zone at 4.0 m wide (4.0 x 2.0 is exactly 2.0), so the
        # ceiling is no longer the binding constraint on either side.
        min_w_m=2.4, min_h_m=1.7, min_area_m2=4.08, max_aspect=2.0,
        # GUESS: family bathrooms are commonly internal/mechanically vented.
        requires_exterior_wall=False,
        # Hall bathroom, NOT Jack-and-Jill: _slice_children places it BETWEEN
        # Bedroom 2 and Bedroom 3, so it cannot be an ensuite of one bedroom —
        # the other could only reach it by transiting the first, which
        # Bedroom.no_through_traffic forbids. So it needs circulation access
        # and has no ensuite parent. (Also, the slicer emits "Bedroom 2"/
        # "Bedroom 3", never bare "Bedroom", so the old parent never matched.)
        requires_circulation_access=True,
        allowed_ensuite_parents=(),
    ),
    "Guest WC": RoomStandard(
        # The common уборная (SNiP 2.08.01-89 Posobie, "Санитарные узлы",
        # cl. 3.5). The Posobie distinguishes a РАЗДЕЛЬНЫЙ sanitary unit (a
        # block of bathroom + уборная) from a СОВМЕЩЁННЫЙ (combined) one; this
        # is the уборная half, provided on its own so a guest is not sent
        # through the bedroom wing.
        #
        # DERIVED (not a quoted room minimum). The only ROOM-size figure the
        # Posobie clause gives is the FIXTURE it must hold: "уборная -
        # помещение, рассчитанное на установку унитаза с габаритом в плане не
        # менее 670x400 мм" — a pan of at least 670 x 400 mm. The room minimum
        # is derived from that plus Neufert's activity space: 400 + 2x200 mm
        # lateral = 800 mm wide, 670 + 600 mm in front of the pan = 1270 mm
        # deep. That lands on the 800 x 1200 mm separate-WC figure conventional
        # in this norm family, which is the cross-check, not the source.
        #
        # TWO PROJECT FLOORS then raise it above the norm, and both are worth
        # knowing about before anyone "fixes" these numbers downward:
        #   - validator.MIN_ROOM_M = 1.2 hard-rejects ANY room under 1.2 m on
        #     either axis. A norm-legal 0.80 m WC would fail our own validator.
        #   - GRID_M = 0.5 means the slicer ceil-snaps every minimum, so 1.2 ->
        #     1.5 and the smallest WC this engine can actually build is
        #     1.5 x 1.5 = 2.25 m2.
        # So the built room is ~1.9x the norm's floor area. That is a property
        # of the 0.5 m grid, not generosity, and it is why min_area_m2 below is
        # the DERIVED 1.2 x 1.3 rather than anything larger — the table states
        # the norm, and the grid does what the grid does.
        min_w_m=1.2, min_h_m=1.3, min_area_m2=1.56, max_aspect=2.0,
        # Подсобное (auxiliary) room: SNiP 2.08.01-89 classes it with the
        # service spaces, not the habitable ones, so no KEO daylight duty —
        # exactly the Laundry precedent (51fcd4d). Mechanical extraction
        # instead, which is what requires_exterior_vent records.
        requires_exterior_wall=False,
        requires_exterior_vent=True,
        # THE ENTIRE POINT of this room: a guest must reach it without
        # transiting a bedroom. Entered from circulation, never an ensuite.
        requires_circulation_access=True,
        allowed_ensuite_parents=(),
        # A WC is never a passage.
        no_through_traffic=True,
    ),
    "Office": RoomStandard(
        # Neufert p44 table 1a, "other habitable room": min dim 2.44 m, min area 7.43 m2.
        min_w_m=2.44, min_h_m=2.44, min_area_m2=7.43, max_aspect=2.0,
        requires_exterior_wall=True, requires_circulation_access=True,
    ),
    "Foyer": RoomStandard(
        # DERIVED (not in Neufert) min_w: corridor 1.2 m + door swing.
        # min_h_m/min_area_m2/max_aspect remain GUESS — Neufert gives a
        # range for entrance halls, not one figure.
        min_w_m=1.5, min_h_m=1.8, min_area_m2=3.0, max_aspect=3.0,
        requires_exterior_wall=False, requires_circulation_access=False,
    ),
    "Mudroom": RoomStandard(
        # DERIVED (not in Neufert) min_w: 600mm hanging + 900mm passage.
        # min_h_m/min_area_m2/max_aspect remain GUESS — not a distinct
        # Neufert category; sized like a small entrance lobby.
        min_w_m=1.5, min_h_m=1.8, min_area_m2=3.0, max_aspect=3.0,
        requires_exterior_wall=False,
        # The mudroom IS the garage->house buffer: _slice_entry places it
        # toward the garage so the sequence is Foyer -> Mudroom -> Garage.
        # It therefore takes circulation access directly; the garage hangs
        # off it (see Garage), not the reverse.
        requires_circulation_access=True,
        allowed_ensuite_parents=(),
    ),
    "Garage": RoomStandard(
        # Single-bay minimums; program.example.json's own min_w/min_h_m
        # (5.4x5.4) already cover a double bay on top of this floor.
        # AXIS-BOUND, not rotation-invariant: the garage is pinned to the
        # street (north) edge and the car drives in along the depth (y) axis,
        # so min_h_m=5.0 is the driving length and min_w_m=3.0 the bay width;
        # a rotated 5.0-wide x 3.0-deep garage is too shallow to park in.
        min_w_m=3.0, min_h_m=5.0, min_area_m2=15.0, max_aspect=2.2,
        requires_exterior_wall=True,
        # Reached through the mudroom/foyer, not directly off circulation.
        requires_circulation_access=False,
        allowed_ensuite_parents=("Mudroom", "Foyer"),
    ),
    "Corridor": RoomStandard(
        # Confirmed, not a guess: below 1200mm two people cannot pass;
        # 1200mm is also the wheelchair door-opening minimum (Neufert).
        min_w_m=1.2,
        # GUESS: a corridor's length is layout-driven, not a fixed Neufert
        # minimum; min_h_m/min_area_m2 just mirror the width floor.
        min_h_m=1.2, min_area_m2=1.44,
        max_aspect=8.0,  # a spine is meant to be long and thin
        requires_exterior_wall=False, requires_circulation_access=False,
    ),
}

# Slicer fallback names that share a band with a table entry. "Bedroom" and
# "Children Bedroom" are what _slice_children emits when it CANNOT split the
# zone into two beds + bathroom; they are the same room type as "Bedroom 2" and
# take the same envelope.
_BAND_ALIAS = {"Bedroom": "Bedroom 2", "Children Bedroom": "Bedroom 2"}

# Fold the architect's ceilings into ROOMS in ONE place rather than hand-copying
# a number into 18 entries, so the table above stays the single source and the
# two cannot drift.
ROOMS = {
    name: (
        replace(spec, max_area_m2=ARCHITECT_AREA_BANDS[_BAND_ALIAS.get(name, name)][1])
        if _BAND_ALIAS.get(name, name) in ARCHITECT_AREA_BANDS
        else spec
    )
    for name, spec in ROOMS.items()
}


def architect_band(room: str) -> tuple[float, float] | None:
    """The architect's (min, max) area band for a room name, or None if he gave
    no figure for it. The MIN here is his, deliberately separate from
    RoomStandard.min_area_m2 (the Neufert/SNiP norm floor): the two answer
    different questions and a room must clear BOTH."""
    key = _BAND_ALIAS.get(room, room)
    return ARCHITECT_AREA_BANDS.get(key)


def area_floor(room: str) -> float:
    """The binding minimum area for a room: the higher of the norm floor and the
    architect's minimum. This is the number the slicer must actually hit."""
    spec = ROOMS.get(room)
    norm = spec.min_area_m2 if spec is not None else 0.0
    band = architect_band(room)
    return max(norm, band[0]) if band is not None else norm


def area_ceiling(room: str) -> float:
    spec = ROOMS.get(room)
    return spec.max_area_m2 if spec is not None else float("inf")


def area_ideal(room: str) -> float:
    """The architect's third value (Ruling 1): the area surplus is distributed
    TOWARD and stops at. Derived by IDEAL_BAND_FRACTION from his own band and
    his own tier -- see that constant for the rule and for what is his versus
    ours.

    Guaranteed to lie inside [area_floor, area_ceiling]. That guarantee is the
    whole reason this is safe to put in the objective where Phase 1's adherence
    term was not: a target no room can reach is a penalty no solution can avoid,
    which is a constant with a weight on it rather than a preference. Here the
    ideal is a point ON the room's own legal interval by construction, so a room
    can always sit exactly on it.

    A room with no ceiling (no architect band) has no meaningful band to take a
    fraction of, so its ideal is its floor -- the tier-3 rule, which is also the
    conservative answer for a room type nobody has ruled on."""
    floor = area_floor(room)
    ceiling = area_ceiling(room)
    if not (ceiling < float("inf")) or ceiling <= floor:
        return floor
    frac = IDEAL_BAND_FRACTION.get(priority_tier(room), 0.0)
    # Quantised to 0.25 m2, the area of one 0.5 x 0.5 m grid cell: an ideal off
    # the grid is unreachable by construction and would leave every room
    # carrying a permanent sub-cell shortfall. Then CLAMPED back into the band,
    # because rounding a floor that is itself off-quantum (Bathroom 4.08, Guest
    # WC 1.56) can round DOWN through it -- which would put the ideal outside
    # the room's own legal interval and break this function's one guarantee.
    q = round((floor + frac * (ceiling - floor)) * 4) / 4
    return max(floor, min(ceiling, q))


# ---------------------------------------------------------------------------
# Zone envelopes: the minimum solver-zone rectangle that slices into legal
# rooms. The solver constrains ZONES; slicer.py cuts each composite ZONE into
# ROOMS. Nothing between them checked that the cut children meet their minima,
# so composite zones were emitting sub-Neufert slivers.
#
# These are now COMPUTED, not hand-calibrated: slicer.compute_zone_minima runs
# the REAL cutters over candidate (w, h) on the grid and returns the smallest
# envelope whose slice is fully standards-legal. So the envelope re-derives
# automatically when GRID_M or a cut rule changes, instead of a constant table
# drifting silently out of sync with the slicer.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ZoneMinima:
    min_w_m: float
    min_h_m: float
    min_area_m2: float
    max_aspect: float


def zone_minima(zone_id: str) -> ZoneMinima | None:
    """Minimum solver-zone envelope (w, h, area, aspect) for a solver zone id,
    computed from the actual slicer. Unknown ids (e.g. the inert "circulation")
    return None, leaving the solver on its declared Space minima and default
    aspect. Imported lazily because slicer.py imports this module."""
    from . import slicer

    return slicer.compute_zone_minima(zone_id)
