"""The zero-shot prompt bank.

Each group is a set of mutually exclusive descriptions scored with a softmax
against the image, so the result is "which of these, and how sure" rather than
an absolute score. Groups exist for the clue types a human analyst would use:
traffic side, signage script, architecture, road markings, utility poles,
vegetation, terrain, plate style, vehicle mix, and coarse world region.

Each option can declare how it constrains geography:

    countries=      an explicit ISO-3166 alpha-2 set
    drive_side=     "left" / "right"       -> countries that drive that way
    script=         ISO 15924 code         -> countries signing in it
    plate_class=    plate class key        -> countries using it
    continents=     Natural Earth CONTINENT values
    subregions=     Natural Earth SUBREGION values
    lat_band=       (min_abs_lat, max_abs_lat) consistency window

Options with none of these are descriptive only: they appear in the report as
observed detail but move no candidate. That distinction matters -- an analyst
should be able to see which observations actually did work.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Option:
    key: str
    label: str
    prompts: tuple[str, ...]
    countries: frozenset[str] = frozenset()
    drive_side: str | None = None
    script: str | None = None
    plate_class: str | None = None
    continents: frozenset[str] = frozenset()
    subregions: frozenset[str] = frozenset()
    lat_band: tuple[float, float] | None = None

    @property
    def constrains(self) -> bool:
        return bool(
            self.countries or self.drive_side or self.script
            or self.plate_class or self.continents or self.subregions
            or self.lat_band
        )


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    # Which fusion weight this group draws on.
    weight_key: str
    options: tuple[Option, ...]
    # Below this softmax probability the reading is treated as "no call".
    min_confidence: float = 0.34
    # A group whose top two options are close is not evidence of anything.
    min_margin: float = 0.08
    description: str = ""


def _o(key: str, label: str, *prompts: str, **kw) -> Option:
    return Option(key=key, label=label, prompts=tuple(prompts), **kw)


# --------------------------------------------------------------------------
# Traffic side. The single most reliable coarse discriminator in street
# imagery, and it cleanly halves the world.
# --------------------------------------------------------------------------
TRAFFIC = Group(
    key="traffic_side",
    title="Traffic direction",
    weight_key="w_driving_side",
    min_confidence=0.55,
    min_margin=0.14,
    description="Which side of the road the visible traffic keeps to.",
    options=(
        _o("left", "Left-hand traffic",
           "a street photo in a country where cars drive on the left side of the road",
           "traffic keeping to the left-hand side of the road",
           "a car with the steering wheel on the right-hand side",
           drive_side="left"),
        _o("right", "Right-hand traffic",
           "a street photo in a country where cars drive on the right side of the road",
           "traffic keeping to the right-hand side of the road",
           "a car with the steering wheel on the left-hand side",
           drive_side="right"),
        _o("indeterminate", "No usable traffic cue",
           "a photograph with no road and no vehicles visible",
           "a photo where the direction of traffic cannot be determined",
           "an indoor photograph"),
    ),
)

# --------------------------------------------------------------------------
# Signage script. CLIP is surprisingly good at naming writing systems even
# when it cannot read them, which covers signage OCR could not resolve.
# --------------------------------------------------------------------------
SCRIPT = Group(
    key="signage_script",
    title="Signage script",
    weight_key="w_script",
    min_confidence=0.30,
    min_margin=0.06,
    description="The writing system on visible signs, shopfronts or markings.",
    options=(
        _o("Latn", "Latin script",
           "a street sign written in the Latin alphabet",
           "shop signs using the Latin alphabet", script="Latn"),
        _o("Cyrl", "Cyrillic script",
           "a street sign written in the Cyrillic alphabet",
           "Russian language shop signs in Cyrillic letters", script="Cyrl"),
        _o("Arab", "Arabic script",
           "a street sign written in Arabic script",
           "shop signs in Arabic calligraphy", script="Arab"),
        _o("Hani", "Chinese characters",
           "a street sign written in Chinese characters",
           "shop signs covered in Chinese hanzi characters", script="Hani"),
        _o("Jpan", "Japanese script",
           "a street sign written in Japanese with kanji and kana",
           "Japanese shop signs with hiragana and katakana", script="Jpan"),
        _o("Hang", "Korean hangul",
           "a street sign written in Korean hangul",
           "Korean shop signs in hangul", script="Hang"),
        _o("Deva", "Devanagari script",
           "a street sign written in Devanagari script",
           "Hindi signage in Devanagari letters", script="Deva"),
        _o("Thai", "Thai script",
           "a street sign written in Thai script",
           "Thai shop signs with curly Thai letters", script="Thai"),
        _o("Grek", "Greek script",
           "a street sign written in the Greek alphabet",
           "Greek shop signs", script="Grek"),
        _o("Hebr", "Hebrew script",
           "a street sign written in Hebrew letters",
           "Hebrew shop signs", script="Hebr"),
        _o("Taml", "Tamil script",
           "a street sign written in Tamil script", script="Taml"),
        _o("Beng", "Bengali script",
           "a street sign written in Bengali script", script="Beng"),
        _o("Ethi", "Ethiopic script",
           "a street sign written in Ethiopic Amharic script", script="Ethi"),
        _o("Geor", "Georgian script",
           "a street sign written in the Georgian alphabet", script="Geor"),
        _o("Armn", "Armenian script",
           "a street sign written in the Armenian alphabet", script="Armn"),
        _o("Mymr", "Myanmar script",
           "a street sign written in Burmese Myanmar script", script="Mymr"),
        _o("Khmr", "Khmer script",
           "a street sign written in Khmer script", script="Khmr"),
        _o("none", "No legible text",
           "a photograph containing no readable text or signage",
           "a natural landscape with no signs"),
    ),
)

# --------------------------------------------------------------------------
# Coarse world region. Broad, but it reliably knocks out whole continents.
# --------------------------------------------------------------------------
REGION = Group(
    key="world_region",
    title="World region",
    weight_key="w_region_hint",
    min_confidence=0.22,
    min_margin=0.05,
    description="The broad part of the world the scene resembles.",
    options=(
        _o("w_europe", "Western Europe",
           "a photo taken in western Europe",
           "a street in France, Germany, the Netherlands or Belgium",
           subregions=frozenset({"Western Europe", "Northern Europe",
                                 "Southern Europe"})),
        _o("e_europe", "Eastern Europe / Central Asia",
           "a photo taken in eastern Europe or the former Soviet Union",
           "a street in Russia, Ukraine, Poland or Kazakhstan",
           subregions=frozenset({"Eastern Europe", "Central Asia"})),
        _o("n_america", "North America",
           "a photo taken in the United States or Canada",
           "a North American suburban street with wide roads",
           subregions=frozenset({"Northern America"})),
        _o("latam", "Latin America",
           "a photo taken in Latin America",
           "a street in Mexico, Brazil, Colombia or Argentina",
           subregions=frozenset({"South America", "Central America",
                                 "Caribbean"})),
        _o("mena", "Middle East / North Africa",
           "a photo taken in the Middle East or North Africa",
           "a street in Egypt, Saudi Arabia, Morocco or the United Arab Emirates",
           subregions=frozenset({"Western Asia", "Northern Africa"})),
        _o("ssa", "Sub-Saharan Africa",
           "a photo taken in sub-Saharan Africa",
           "a street in Nigeria, Kenya, Ghana or South Africa",
           subregions=frozenset({"Eastern Africa", "Western Africa",
                                 "Middle Africa", "Southern Africa"})),
        _o("s_asia", "South Asia",
           "a photo taken in South Asia",
           "a street in India, Pakistan, Bangladesh or Sri Lanka",
           subregions=frozenset({"Southern Asia"})),
        _o("se_asia", "Southeast Asia",
           "a photo taken in Southeast Asia",
           "a street in Thailand, Vietnam, Indonesia or the Philippines",
           subregions=frozenset({"South-Eastern Asia"})),
        _o("e_asia", "East Asia",
           "a photo taken in East Asia",
           "a street in China, Japan, South Korea or Taiwan",
           subregions=frozenset({"Eastern Asia"})),
        _o("oceania", "Oceania",
           "a photo taken in Australia, New Zealand or the Pacific islands",
           subregions=frozenset({"Australia and New Zealand", "Melanesia",
                                 "Polynesia", "Micronesia"})),
    ),
)

# --------------------------------------------------------------------------
# Environment. These constrain latitude rather than country.
# --------------------------------------------------------------------------
BIOME = Group(
    key="biome",
    title="Vegetation / biome",
    weight_key="w_climate",
    min_confidence=0.26,
    description="The dominant vegetation and climate signature.",
    options=(
        _o("tropical", "Tropical / equatorial",
           "a tropical scene with palm trees and dense green vegetation",
           "a humid equatorial rainforest landscape",
           lat_band=(0.0, 28.0)),
        _o("desert", "Arid desert",
           "an arid desert landscape with sand and bare rock",
           "a dry desert scene with almost no vegetation",
           lat_band=(12.0, 40.0)),
        _o("savanna", "Savanna / dry grassland",
           "a savanna landscape with dry grass and scattered acacia trees",
           lat_band=(0.0, 30.0)),
        _o("mediterranean", "Mediterranean scrub",
           "a Mediterranean landscape with olive trees, cypress and dry scrub",
           lat_band=(28.0, 45.0)),
        _o("temperate", "Temperate deciduous",
           "a temperate landscape with broadleaf deciduous trees and green grass",
           lat_band=(30.0, 58.0)),
        _o("boreal", "Boreal conifer",
           "a boreal forest of spruce and pine trees",
           "a cold northern conifer forest landscape",
           lat_band=(45.0, 70.0)),
        _o("tundra", "Tundra / polar",
           "a treeless arctic tundra landscape",
           "a polar landscape of ice and snow",
           lat_band=(58.0, 90.0)),
        _o("alpine", "Alpine / high mountain",
           "a high alpine mountain landscape above the tree line"),
        _o("urban", "Dense urban, little vegetation",
           "a dense city scene with buildings and almost no vegetation"),
    ),
)

SNOW = Group(
    key="snow_cover",
    title="Snow cover",
    weight_key="w_climate",
    min_confidence=0.62,
    min_margin=0.2,
    description="Lying snow is incompatible with the deep tropics.",
    options=(
        _o("snow", "Snow on the ground",
           "a scene with deep snow covering the ground and rooftops",
           "a snowy winter street",
           lat_band=(23.0, 90.0)),
        _o("none", "No snow",
           "a scene with no snow anywhere",
           "a green or dry landscape with no snow"),
    ),
)

# --------------------------------------------------------------------------
# Built environment.
# --------------------------------------------------------------------------
ARCHITECTURE = Group(
    key="architecture",
    title="Architecture",
    weight_key="w_region_hint",
    min_confidence=0.22,
    description="Building style, materials and roof form.",
    options=(
        _o("nw_european", "Northwest European",
           "a street of brick terraced houses with steep tiled roofs in "
           "northern Europe",
           subregions=frozenset({"Western Europe", "Northern Europe"})),
        _o("mediterranean", "Mediterranean",
           "whitewashed or stucco buildings with terracotta tiled roofs and "
           "shuttered windows",
           subregions=frozenset({"Southern Europe", "Northern Africa"})),
        _o("soviet_bloc", "Post-Soviet panel blocks",
           "grey prefabricated concrete panel apartment blocks of the Soviet era",
           subregions=frozenset({"Eastern Europe", "Central Asia"})),
        _o("n_american", "North American",
           "American suburban houses with vinyl siding, attached garages and "
           "a wide asphalt road",
           subregions=frozenset({"Northern America"})),
        _o("latam", "Latin American",
           "low concrete buildings with flat roofs, rebar sticking up and "
           "painted walls, in Latin America",
           subregions=frozenset({"South America", "Central America"})),
        _o("s_asian", "South Asian",
           "densely packed concrete buildings with exposed rebar, water tanks "
           "on the roof and hanging cables, in India",
           subregions=frozenset({"Southern Asia"})),
        _o("e_asian", "East Asian",
           "tall narrow apartment towers and tiled low-rise buildings in "
           "East Asia",
           subregions=frozenset({"Eastern Asia"})),
        _o("se_asian", "Southeast Asian shophouse",
           "narrow multi-storey shophouses with shuttered fronts in "
           "Southeast Asia",
           subregions=frozenset({"South-Eastern Asia"})),
        _o("gulf", "Gulf / Middle Eastern",
           "sand-coloured concrete buildings with flat roofs and air "
           "conditioning units in the Arabian Gulf",
           subregions=frozenset({"Western Asia"})),
        _o("ssa", "Sub-Saharan African",
           "single-storey buildings with corrugated metal roofs in "
           "sub-Saharan Africa",
           subregions=frozenset({"Eastern Africa", "Western Africa",
                                 "Middle Africa", "Southern Africa"})),
        _o("none", "No diagnostic architecture",
           "a natural landscape with no buildings"),
    ),
)

ROAD = Group(
    key="road_markings",
    title="Road surface and markings",
    weight_key="w_region_hint",
    min_confidence=0.30,
    description="Carriageway surface and line colour.",
    options=(
        _o("white_lines", "White lane markings",
           "an asphalt road with white painted lane markings"),
        _o("yellow_centre", "Yellow centre line",
           "a road with a yellow painted centre line dividing the traffic",
           countries=frozenset({"US", "CA", "MX", "JP", "KR", "PH", "CL"})),
        _o("unpaved", "Unpaved",
           "an unpaved dirt or gravel road with no markings"),
        _o("cobbled", "Cobbled / setts",
           "a street paved with cobblestones or stone setts"),
        _o("none", "No road visible",
           "a photograph with no road surface visible"),
    ),
)

UTILITY = Group(
    key="utility",
    title="Utility infrastructure",
    weight_key="w_region_hint",
    min_confidence=0.32,
    description="Poles, wires and street furniture.",
    options=(
        _o("wooden_poles", "Wooden poles, overhead wires",
           "wooden utility poles carrying overhead electrical wires"),
        _o("concrete_poles", "Concrete poles",
           "grey concrete utility poles carrying power lines"),
        _o("dense_cabling", "Dense tangled cabling",
           "a dense tangle of overhead electrical and telecom cables between poles"),
        _o("buried", "No overhead wires",
           "a street with no overhead wires, all utilities buried"),
        _o("pylons", "Steel lattice pylons",
           "tall steel lattice electricity transmission pylons"),
    ),
)

PLATE = Group(
    key="plate_style",
    title="Vehicle registration plates",
    weight_key="w_plate",
    min_confidence=0.34,
    min_margin=0.10,
    description="Plate shape and colour, where a plate is legible.",
    options=(
        _o("eu_blue_band", "EU-style blue band",
           "a long white European vehicle number plate with a blue band on "
           "the left edge",
           plate_class="eu_blue_band"),
        _o("north_america", "North American",
           "a short wide North American licence plate with a state or "
           "province name",
           plate_class="north_america"),
        _o("uk_yellow_rear", "White front, yellow rear",
           "a British vehicle with a white front number plate and a yellow "
           "rear number plate",
           plate_class="uk_yellow_rear"),
        _o("cn_blue", "Blue plate, Chinese characters",
           "a blue Chinese vehicle licence plate with white characters",
           plate_class="cn_blue"),
        _o("in_white_yellow", "White private / yellow commercial",
           "an Indian vehicle number plate, white for private or yellow for "
           "commercial vehicles",
           plate_class="in_white_yellow"),
        _o("arabic_plate", "Arabic-numeral plate",
           "a vehicle number plate showing Arabic script and Arabic numerals",
           plate_class="arabic_plate"),
        _o("none", "No legible plate",
           "a photograph with no vehicle number plate visible"),
    ),
)

VEHICLES = Group(
    key="vehicles",
    title="Vehicle mix",
    weight_key="w_region_hint",
    min_confidence=0.30,
    description="What is actually on the road.",
    options=(
        _o("kei", "Japanese kei cars",
           "small boxy Japanese kei cars on a narrow street",
           countries=frozenset({"JP"})),
        _o("pickups", "Pickup trucks and large SUVs",
           "large American pickup trucks and SUVs in a car park",
           subregions=frozenset({"Northern America"})),
        _o("euro_hatch", "Small European hatchbacks",
           "small European hatchback cars parked along a street",
           subregions=frozenset({"Western Europe", "Southern Europe",
                                 "Northern Europe", "Eastern Europe"})),
        _o("autorickshaw", "Auto-rickshaws",
           "yellow and green three-wheeled auto rickshaws in traffic",
           countries=frozenset({"IN", "BD", "PK", "LK", "NP"})),
        _o("motorbikes", "Dense motorbike traffic",
           "a street crowded with scooters and small motorbikes",
           subregions=frozenset({"South-Eastern Asia", "Southern Asia"})),
        _o("tuktuk", "Tuk-tuks",
           "colourful tuk-tuks on a Southeast Asian street",
           countries=frozenset({"TH", "KH", "LA", "LK"})),
        _o("none", "No diagnostic vehicles",
           "a photograph with no vehicles visible"),
    ),
)

TERRAIN = Group(
    key="terrain",
    title="Terrain",
    weight_key="w_climate",
    min_confidence=0.28,
    description="Landform behind and around the subject.",
    options=(
        _o("flat", "Flat plain", "a completely flat landscape with a low horizon"),
        _o("hills", "Rolling hills", "a landscape of rolling green hills"),
        _o("mountains", "High mountains",
           "a landscape dominated by tall jagged mountains"),
        _o("coast", "Coastal", "a coastal scene with the sea and a shoreline"),
        _o("valley", "River valley", "a river valley between steep slopes"),
        _o("urban_flat", "Urban, terrain hidden",
           "a dense urban scene where the terrain is not visible"),
    ),
)

SETTING = Group(
    key="setting",
    title="Settlement type",
    weight_key="w_region_hint",
    min_confidence=0.30,
    description="How built up the scene is.",
    options=(
        _o("cbd", "High-rise city centre",
           "a dense city centre of glass and steel high-rise towers"),
        _o("oldtown", "Low-rise historic core",
           "a narrow street in a historic low-rise old town"),
        _o("suburb", "Suburban", "a quiet residential suburban street"),
        _o("village", "Rural village", "a small rural village with few buildings"),
        _o("industrial", "Industrial", "an industrial area with warehouses"),
        _o("wilderness", "Wilderness", "a remote natural landscape with no buildings"),
        _o("interior", "Indoors", "an indoor photograph taken inside a building"),
    ),
)

# --------------------------------------------------------------------------
# What is deliberately not in this file
# --------------------------------------------------------------------------
#
# **Faces.** Nothing here asks CLIP anything about the people in a
# photograph beyond what they are wearing for the weather, and even that
# was tried and removed (see below). Guessing where someone is from by
# looking at them is unreliable, and it is the same inference that powers
# the worst applications of this class of tool. This project will not do
# it, and the absence is a decision rather than an oversight.
#
# **More groups.** Four were written and measured on the 163-sample
# benchmark in Phase 22: road-sign convention, roof form, street furniture
# and dress. Every one of them was inert or harmful.
#
#     group             median km    better    worse
#     baseline              341.8         -        -
#     sign convention       341.8         0        0     never fired at all
#     roof form             368.0         0        1
#     street furniture      368.0         0        1
#     dress                 341.8         1        1
#     all four              385.1         1        3
#
# The reason is worth keeping, because it applies to the next four
# somebody thinks of. These groups are not independent evidence: they are
# more opinions from the same image encoder about the same embedding, and
# the fusion combines them in log space as though they were independent.
# Stacking correlated opinions does not sharpen the estimate, it amplifies
# whatever that one encoder already got wrong. They also almost never
# cleared their confidence thresholds -- 159 of 163 samples were untouched
# by all four together.
#
# The way to a better estimate is evidence from somewhere *else*: text
# actually read off the image (Phase 21), or a second locator trained
# differently (Phase 16). Not a thirteenth prompt group.

ALL_GROUPS: tuple[Group, ...] = (
    TRAFFIC, SCRIPT, REGION, ARCHITECTURE, BIOME, SNOW, ROAD, UTILITY,
    PLATE, VEHICLES, TERRAIN, SETTING,
)

GROUPS_BY_KEY = {g.key: g for g in ALL_GROUPS}


def all_prompts() -> list[str]:
    """Every prompt string, in a stable order, for batch text encoding."""
    out: list[str] = []
    for group in ALL_GROUPS:
        for option in group.options:
            out.extend(option.prompts)
    return out


def prompt_index() -> dict[tuple[str, str], tuple[int, int]]:
    """(group key, option key) -> half-open slice into :func:`all_prompts`."""
    index: dict[tuple[str, str], tuple[int, int]] = {}
    cursor = 0
    for group in ALL_GROUPS:
        for option in group.options:
            n = len(option.prompts)
            index[(group.key, option.key)] = (cursor, cursor + n)
            cursor += n
    return index


# Country-level prompts for the CLIP gazetteer locator. Two phrasings per
# country: one street-level, one general, averaged for robustness.
COUNTRY_PROMPT_TEMPLATES = (
    "a street-level photograph taken in {name}",
    "a photo taken in {name}",
)
