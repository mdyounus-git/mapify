"""The benchmark's sampling frame.

Eleven samples chosen by convenience told us very little: the calibration
table had cells with n=1, and tourist photography over-represents landmarks,
which are the easy case. This is a deliberate frame instead.

Each site carries a scene tag so results can be reported by *kind* as well as
by place:

    landmark   a recognisable structure — the easy case, and over-represented
               in any photo corpus, so it is capped
    street     ordinary street level, which is what the tool is actually for
    rural      countryside, villages, agricultural land
    coastal    shoreline, harbours, beaches
    interior   inside a building, where there is often nothing to go on
    transport  stations, airports, roads — usually signage-rich

Africa and South America are deliberately over-sampled relative to their share
of photo corpora, because that is exactly where GeoCLIP is known to fail and
where the original eleven-sample set was thinnest. A benchmark that mirrors the
bias of the training data would hide the weakness rather than measure it.

Coordinates are approximate centres to search around, not ground truth. The
ground truth for each image is whatever coordinate Wikimedia Commons records
for that specific photograph.
"""

from __future__ import annotations

# (slug, latitude, longitude, region, scene, label)
SITES: list[tuple[str, float, float, str, str, str]] = [
    # ── Europe ────────────────────────────────────────────────────────────
    ("paris-eiffel",     48.8584,   2.2945, "Europe", "landmark",  "Eiffel Tower, France"),
    ("paris-belleville", 48.8721,   2.3844, "Europe", "street",    "Belleville, Paris"),
    ("london-south",     51.4744,  -0.0905, "Europe", "street",    "Peckham, London"),
    ("london-bridge",    51.5055,  -0.0754, "Europe", "landmark",  "Tower Bridge, London"),
    ("berlin-kreuzberg", 52.4988,  13.4184, "Europe", "street",    "Kreuzberg, Berlin"),
    ("munich",           48.1374,  11.5755, "Europe", "street",    "Marienplatz, Munich"),
    ("amsterdam",        52.3676,   4.9041, "Europe", "street",    "Amsterdam centre"),
    ("rotterdam",        51.9225,   4.4792, "Europe", "street",    "Rotterdam"),
    ("madrid",           40.4168,  -3.7038, "Europe", "street",    "Madrid centre"),
    ("seville",          37.3886,  -5.9823, "Europe", "street",    "Seville"),
    ("lisbon",           38.7223,  -9.1393, "Europe", "street",    "Lisbon"),
    ("rome",             41.8902,  12.4922, "Europe", "landmark",  "Colosseum, Rome"),
    ("naples",           40.8518,  14.2681, "Europe", "street",    "Naples"),
    ("venice",           45.4341,  12.3388, "Europe", "street",    "Venice"),
    ("athens",           37.9715,  23.7257, "Europe", "landmark",  "Acropolis, Athens"),
    ("thessaloniki",     40.6401,  22.9444, "Europe", "street",    "Thessaloniki"),
    ("warsaw",           52.2297,  21.0122, "Europe", "street",    "Warsaw"),
    ("krakow",           50.0614,  19.9366, "Europe", "street",    "Krakow"),
    ("prague",           50.0875,  14.4213, "Europe", "street",    "Prague"),
    ("budapest",         47.4979,  19.0402, "Europe", "street",    "Budapest"),
    ("bucharest",        44.4268,  26.1025, "Europe", "street",    "Bucharest"),
    ("sofia",            42.6977,  23.3219, "Europe", "street",    "Sofia"),
    ("belgrade",         44.8125,  20.4612, "Europe", "street",    "Belgrade"),
    ("zagreb",           45.8150,  15.9819, "Europe", "street",    "Zagreb"),
    ("vienna",           48.2082,  16.3738, "Europe", "street",    "Vienna"),
    ("zurich",           47.3769,   8.5417, "Europe", "street",    "Zurich"),
    ("stockholm",        59.3293,  18.0686, "Europe", "street",    "Stockholm"),
    ("oslo",             59.9139,  10.7522, "Europe", "street",    "Oslo"),
    ("copenhagen",       55.6761,  12.5683, "Europe", "street",    "Copenhagen"),
    ("helsinki",         60.1699,  24.9384, "Europe", "street",    "Helsinki"),
    ("reykjavik",        64.1466, -21.9426, "Europe", "street",    "Reykjavik"),
    ("dublin",           53.3498,  -6.2603, "Europe", "street",    "Dublin"),
    ("edinburgh",        55.9533,  -3.1883, "Europe", "street",    "Edinburgh"),
    ("porto",            41.1579,  -8.6291, "Europe", "coastal",   "Porto"),
    ("norway-fjord",     62.1049,   7.2069, "Europe", "rural",     "Norwegian fjord"),
    ("tuscany",          43.4637,  11.3785, "Europe", "rural",     "Tuscany"),
    ("swiss-alps",       46.0207,   7.7491, "Europe", "rural",     "Zermatt, Switzerland"),
    ("moscow",           55.7525,  37.6231, "Europe", "landmark",  "Red Square, Moscow"),
    ("st-petersburg",    59.9398,  30.3146, "Europe", "street",    "St Petersburg"),
    ("kyiv",             50.4501,  30.5234, "Europe", "street",    "Kyiv"),

    # ── Asia ──────────────────────────────────────────────────────────────
    ("tokyo-shibuya",    35.6595, 139.7005, "Asia", "street",    "Shibuya, Tokyo"),
    ("tokyo-asakusa",    35.7148, 139.7967, "Asia", "landmark",  "Sensoji, Tokyo"),
    ("osaka",            34.6687, 135.5013, "Asia", "street",    "Dotonbori, Osaka"),
    ("kyoto",            35.0116, 135.7681, "Asia", "street",    "Kyoto"),
    ("seoul",            37.5665, 126.9780, "Asia", "street",    "Seoul"),
    ("busan",            35.1796, 129.0756, "Asia", "coastal",   "Busan"),
    ("beijing",          39.9163, 116.3972, "Asia", "landmark",  "Forbidden City, Beijing"),
    ("shanghai",         31.2304, 121.4737, "Asia", "street",    "Shanghai"),
    ("hong-kong",        22.2793, 114.1628, "Asia", "street",    "Hong Kong"),
    ("taipei",           25.0330, 121.5654, "Asia", "street",    "Taipei"),
    ("bangkok",          13.7563, 100.5018, "Asia", "street",    "Bangkok"),
    ("chiang-mai",       18.7883,  98.9853, "Asia", "street",    "Chiang Mai"),
    ("hanoi",            21.0285, 105.8542, "Asia", "street",    "Hanoi"),
    ("ho-chi-minh",      10.7769, 106.7009, "Asia", "street",    "Ho Chi Minh City"),
    ("phnom-penh",       11.5564, 104.9282, "Asia", "street",    "Phnom Penh"),
    ("singapore",         1.2834, 103.8607, "Asia", "street",    "Singapore"),
    ("kuala-lumpur",      3.1478, 101.6953, "Asia", "street",    "Kuala Lumpur"),
    ("jakarta",          -6.1754, 106.8272, "Asia", "street",    "Jakarta"),
    ("bali",             -8.5069, 115.2625, "Asia", "rural",     "Ubud, Bali"),
    ("manila",           14.5995, 120.9842, "Asia", "street",    "Manila"),
    ("delhi",            28.6139,  77.2090, "Asia", "street",    "Delhi"),
    ("bengaluru",        12.9794,  77.5912, "Asia", "street",    "Bengaluru"),
    ("mumbai",           18.9220,  72.8347, "Asia", "street",    "Mumbai"),
    ("kolkata",          22.5726,  88.3639, "Asia", "street",    "Kolkata"),
    ("chennai",          13.0827,  80.2707, "Asia", "street",    "Chennai"),
    ("jaipur",           26.9124,  75.7873, "Asia", "street",    "Jaipur"),
    ("kathmandu",        27.7172,  85.3240, "Asia", "street",    "Kathmandu"),
    ("dhaka",            23.8103,  90.4125, "Asia", "street",    "Dhaka"),
    ("colombo",           6.9271,  79.8612, "Asia", "street",    "Colombo"),
    ("karachi",          24.8607,  67.0011, "Asia", "street",    "Karachi"),
    ("lahore",           31.5204,  74.3587, "Asia", "street",    "Lahore"),
    ("tehran",           35.6892,  51.3890, "Asia", "street",    "Tehran"),
    ("istanbul",         41.0082,  28.9784, "Asia", "street",    "Istanbul"),
    ("dubai",            25.2048,  55.2708, "Asia", "street",    "Dubai"),
    ("doha",             25.2854,  51.5310, "Asia", "street",    "Doha"),
    ("jerusalem",        31.7683,  35.2137, "Asia", "street",    "Jerusalem"),
    ("amman",            31.9539,  35.9106, "Asia", "street",    "Amman"),
    ("tbilisi",          41.7151,  44.8271, "Asia", "street",    "Tbilisi"),
    ("yerevan",          40.1792,  44.4991, "Asia", "street",    "Yerevan"),
    ("almaty",           43.2220,  76.8512, "Asia", "street",    "Almaty"),
    ("tashkent",         41.2995,  69.2401, "Asia", "street",    "Tashkent"),
    ("ulaanbaatar",      47.8864, 106.9057, "Asia", "street",    "Ulaanbaatar"),
    ("himalaya",         27.9881,  86.9250, "Asia", "rural",     "Everest region, Nepal"),

    # ── Africa (over-sampled deliberately) ────────────────────────────────
    ("cairo-giza",       29.9792,  31.1342, "Africa", "landmark", "Giza, Egypt"),
    ("cairo-city",       30.0444,  31.2357, "Africa", "street",   "Cairo centre"),
    ("alexandria",       31.2001,  29.9187, "Africa", "coastal",  "Alexandria"),
    ("marrakesh",        31.6295,  -7.9811, "Africa", "street",   "Marrakesh"),
    ("casablanca",       33.5731,  -7.5898, "Africa", "street",   "Casablanca"),
    ("fes",              34.0181,  -5.0078, "Africa", "street",   "Fes"),
    ("tunis",            36.8065,  10.1815, "Africa", "street",   "Tunis"),
    ("algiers",          36.7538,   3.0588, "Africa", "street",   "Algiers"),
    ("lagos",             6.5244,   3.3792, "Africa", "street",   "Lagos"),
    ("abuja",             9.0765,   7.3986, "Africa", "street",   "Abuja"),
    ("accra",             5.6037,  -0.1870, "Africa", "street",   "Accra"),
    ("dakar",            14.7167, -17.4677, "Africa", "street",   "Dakar"),
    ("abidjan",           5.3600,  -4.0083, "Africa", "street",   "Abidjan"),
    ("bamako",           12.6392,  -8.0029, "Africa", "street",   "Bamako"),
    ("nairobi",          -1.2921,  36.8219, "Africa", "street",   "Nairobi"),
    ("mombasa",          -4.0435,  39.6682, "Africa", "coastal",  "Mombasa"),
    ("kampala",           0.3476,  32.5825, "Africa", "street",   "Kampala"),
    ("kigali",           -1.9441,  30.0619, "Africa", "street",   "Kigali"),
    ("dar-es-salaam",    -6.7924,  39.2083, "Africa", "street",   "Dar es Salaam"),
    ("addis-ababa",       9.0320,  38.7469, "Africa", "street",   "Addis Ababa"),
    ("khartoum",         15.5007,  32.5599, "Africa", "street",   "Khartoum"),
    ("johannesburg",    -26.2041,  28.0473, "Africa", "street",   "Johannesburg"),
    ("cape-town",       -33.9249,  18.4241, "Africa", "coastal",  "Cape Town"),
    ("durban",          -29.8587,  31.0218, "Africa", "coastal",  "Durban"),
    ("windhoek",        -22.5609,  17.0658, "Africa", "street",   "Windhoek"),
    ("gaborone",        -24.6282,  25.9231, "Africa", "street",   "Gaborone"),
    ("harare",          -17.8252,  31.0335, "Africa", "street",   "Harare"),
    ("lusaka",          -15.3875,  28.3228, "Africa", "street",   "Lusaka"),
    ("maputo",          -25.9692,  32.5732, "Africa", "street",   "Maputo"),
    ("antananarivo",    -18.8792,  47.5079, "Africa", "street",   "Antananarivo"),
    ("serengeti",        -2.3333,  34.8333, "Africa", "rural",    "Serengeti"),
    ("sahara-morocco",   31.1000,  -4.0000, "Africa", "rural",    "Sahara, Morocco"),

    # ── South America (over-sampled deliberately) ─────────────────────────
    ("rio-corcovado",   -22.9519, -43.2105, "South America", "landmark", "Corcovado, Rio"),
    ("rio-copacabana",  -22.9711, -43.1822, "South America", "coastal",  "Copacabana, Rio"),
    ("sao-paulo",       -23.5505, -46.6333, "South America", "street",   "Sao Paulo"),
    ("salvador",        -12.9777, -38.5016, "South America", "street",   "Salvador, Brazil"),
    ("brasilia",        -15.7942, -47.8822, "South America", "street",   "Brasilia"),
    ("buenos-aires",    -34.6037, -58.3816, "South America", "street",   "Buenos Aires"),
    ("cordoba-ar",      -31.4201, -64.1888, "South America", "street",   "Cordoba, Argentina"),
    ("montevideo",      -34.9011, -56.1645, "South America", "street",   "Montevideo"),
    ("santiago",        -33.4489, -70.6693, "South America", "street",   "Santiago"),
    ("valparaiso",      -33.0472, -71.6127, "South America", "coastal",  "Valparaiso"),
    ("lima",            -12.0464, -77.0428, "South America", "street",   "Lima"),
    ("cusco",           -13.5319, -71.9675, "South America", "street",   "Cusco"),
    ("la-paz",          -16.4897, -68.1193, "South America", "street",   "La Paz"),
    ("quito",            -0.1807, -78.4678, "South America", "street",   "Quito"),
    ("bogota",            4.7110, -74.0721, "South America", "street",   "Bogota"),
    ("medellin",          6.2442, -75.5812, "South America", "street",   "Medellin"),
    ("cartagena",        10.3910, -75.4794, "South America", "coastal",  "Cartagena"),
    ("caracas",          10.4806, -66.9036, "South America", "street",   "Caracas"),
    ("asuncion",        -25.2637, -57.5759, "South America", "street",   "Asuncion"),
    ("patagonia",       -50.9423, -73.4068, "South America", "rural",    "Torres del Paine"),
    ("amazon",           -3.1190, -60.0217, "South America", "rural",    "Manaus, Amazon"),

    # ── North America ─────────────────────────────────────────────────────
    ("nyc-times-sq",     40.7580, -73.9855, "North America", "street",   "Times Square, NY"),
    ("nyc-brooklyn",     40.6782, -73.9442, "North America", "street",   "Brooklyn, NY"),
    ("chicago",          41.8827, -87.6233, "North America", "street",   "Chicago"),
    ("san-francisco",    37.8199, -122.4783, "North America", "landmark", "Golden Gate"),
    ("los-angeles",      34.0522, -118.2437, "North America", "street",   "Los Angeles"),
    ("seattle",          47.6062, -122.3321, "North America", "street",   "Seattle"),
    ("new-orleans",      29.9584, -90.0644, "North America", "street",   "New Orleans"),
    ("miami",            25.7907, -80.1300, "North America", "coastal",  "Miami Beach"),
    ("denver",           39.7392, -104.9903, "North America", "street",   "Denver"),
    ("toronto",          43.6426, -79.3871, "North America", "street",   "Toronto"),
    ("montreal",         45.5017, -73.5673, "North America", "street",   "Montreal"),
    ("vancouver",        49.2827, -123.1207, "North America", "street",   "Vancouver"),
    ("mexico-city",      19.4326, -99.1332, "North America", "street",   "Mexico City"),
    ("oaxaca",           17.0732, -96.7266, "North America", "street",   "Oaxaca"),
    ("havana",           23.1136, -82.3666, "North America", "street",   "Havana"),
    ("panama-city",       8.9824, -79.5199, "North America", "street",   "Panama City"),
    ("guatemala",        14.6349, -90.5069, "North America", "street",   "Guatemala City"),
    ("grand-canyon",     36.0544, -112.1401, "North America", "rural",   "Grand Canyon"),
    ("alaska",           61.2181, -149.9003, "North America", "rural",   "Anchorage, Alaska"),

    # ── Oceania ───────────────────────────────────────────────────────────
    ("sydney-opera",    -33.8568, 151.2153, "Oceania", "landmark", "Sydney Opera House"),
    ("sydney-street",   -33.8830, 151.2050, "Oceania", "street",   "Surry Hills, Sydney"),
    ("melbourne",       -37.8136, 144.9631, "Oceania", "street",   "Melbourne"),
    ("brisbane",        -27.4698, 153.0251, "Oceania", "street",   "Brisbane"),
    ("perth",           -31.9523, 115.8613, "Oceania", "street",   "Perth"),
    ("auckland",        -36.8485, 174.7633, "Oceania", "street",   "Auckland"),
    ("wellington",      -41.2866, 174.7756, "Oceania", "street",   "Wellington"),
    ("queenstown",      -45.0312, 168.6626, "Oceania", "rural",    "Queenstown, NZ"),
    ("outback",         -25.3444, 131.0369, "Oceania", "rural",    "Uluru, Australia"),
    ("suva",            -18.1416, 178.4419, "Oceania", "street",   "Suva, Fiji"),
]


# One country, densely: the owner works from India, and the world frame has
# a single Indian photograph (Mumbai), which cannot show whether India is
# placed in India. Every state's kind of place -- metros and towns, the
# coasts, the plains, the hills and the north-east -- with the scene mix of
# the world frame. Regions here are the parts of India.
INDIA_SITES = [
    ("in-delhi-cp",          28.6315, 77.2167, "North",      "street",    "Connaught Place, Delhi"),
    ("in-delhi-chandni",     28.6506, 77.2303, "North",      "street",    "Chandni Chowk, Delhi"),
    ("in-delhi-station",     28.6430, 77.2194, "North",      "transport", "New Delhi station"),
    ("in-jaipur",            26.9239, 75.8267, "North",      "street",    "Jaipur"),
    ("in-udaipur",           24.5764, 73.6835, "North",      "coastal",   "Udaipur lakes"),
    ("in-lucknow",           26.8500, 80.9462, "North",      "street",    "Hazratganj, Lucknow"),
    ("in-varanasi",          25.3109, 83.0107, "North",      "coastal",   "Varanasi ghats"),
    ("in-amritsar",          31.6200, 74.8765, "North",      "street",    "Amritsar"),
    ("in-chandigarh",        30.7398, 76.7827, "North",      "street",    "Chandigarh"),
    ("in-shimla",            31.1048, 77.1734, "North",      "street",    "Mall Road, Shimla"),
    ("in-rishikesh",         30.0869, 78.2676, "North",      "street",    "Rishikesh"),
    ("in-srinagar",          34.0837, 74.7973, "North",      "coastal",   "Dal Lake, Srinagar"),
    ("in-leh",               34.1642, 77.5848, "North",      "rural",     "Leh"),
    ("in-mumbai-dadar",      19.0178, 72.8478, "West",       "street",    "Dadar, Mumbai"),
    ("in-mumbai-cst",        18.9398, 72.8355, "West",       "transport", "CSMT, Mumbai"),
    ("in-pune",              18.5236, 73.8411, "West",       "street",    "Pune"),
    ("in-ahmedabad",         23.0258, 72.5873, "West",       "street",    "Ahmedabad"),
    ("in-surat",             21.1702, 72.8311, "West",       "street",    "Surat"),
    ("in-goa",               15.4989, 73.8278, "West",       "coastal",   "Panaji, Goa"),
    ("in-nagpur",            21.1458, 79.0882, "Central",    "street",    "Nagpur"),
    ("in-bhopal",            23.2599, 77.4126, "Central",    "street",    "Bhopal"),
    ("in-indore",            22.7196, 75.8577, "Central",    "street",    "Indore"),
    ("in-bengaluru-mg",      12.9756, 77.6066, "South",      "street",    "MG Road, Bengaluru"),
    ("in-bengaluru-majestic", 12.9767, 77.5713, "South",     "transport", "Majestic, Bengaluru"),
    ("in-chennai-tnagar",    13.0418, 80.2341, "South",      "street",    "T. Nagar, Chennai"),
    ("in-chennai-marina",    13.0500, 80.2824, "South",      "coastal",   "Marina, Chennai"),
    ("in-hyderabad-old",     17.3616, 78.4747, "South",      "street",    "Old City, Hyderabad"),
    ("in-hyderabad-hitec",   17.4435, 78.3772, "South",      "street",    "HITEC City, Hyderabad"),
    ("in-kochi",              9.9658, 76.2421, "South",      "coastal",   "Fort Kochi"),
    ("in-trivandrum",         8.4875, 76.9525, "South",      "street",    "Thiruvananthapuram"),
    ("in-alappuzha",          9.4981, 76.3388, "South",      "rural",     "Alappuzha backwaters"),
    ("in-munnar",            10.0889, 77.0595, "South",      "rural",     "Munnar"),
    ("in-madurai",            9.9195, 78.1193, "South",      "street",    "Madurai"),
    ("in-coimbatore",        11.0168, 76.9558, "South",      "street",    "Coimbatore"),
    ("in-mysuru",            12.3051, 76.6551, "South",      "street",    "Mysuru"),
    ("in-mangaluru",         12.8698, 74.8430, "South",      "street",    "Mangaluru"),
    ("in-hampi",             15.3350, 76.4600, "South",      "rural",     "Hampi"),
    ("in-vizag",             17.7128, 83.3200, "South",      "coastal",   "Visakhapatnam"),
    ("in-vijayawada",        16.5062, 80.6480, "South",      "street",    "Vijayawada"),
    ("in-kolkata",           22.5646, 88.3510, "East",       "street",    "Esplanade, Kolkata"),
    ("in-kolkata-howrah",    22.5839, 88.3426, "East",       "transport", "Howrah"),
    ("in-patna",             25.6093, 85.1376, "East",       "street",    "Patna"),
    ("in-bhubaneswar",       20.2961, 85.8245, "East",       "street",    "Bhubaneswar"),
    ("in-puri",              19.7983, 85.8249, "East",       "coastal",   "Puri"),
    ("in-guwahati",          26.1806, 91.7539, "North-east", "street",    "Guwahati"),
    ("in-shillong",          25.5788, 91.8933, "North-east", "street",    "Shillong"),
    ("in-gangtok",           27.3314, 88.6138, "North-east", "street",    "Gangtok"),
    ("in-imphal",            24.8170, 93.9368, "North-east", "street",    "Imphal"),
]

FRAMES = {"world": SITES, "india": INDIA_SITES}


def by_region() -> dict[str, int]:
    out: dict[str, int] = {}
    for _, _, _, region, _, _ in SITES:
        out[region] = out.get(region, 0) + 1
    return out


def by_scene() -> dict[str, int]:
    out: dict[str, int] = {}
    for _, _, _, _, scene, _ in SITES:
        out[scene] = out.get(scene, 0) + 1
    return out


if __name__ == "__main__":
    print(f"{len(SITES)} sites")
    print("\nby region:")
    for k, v in sorted(by_region().items(), key=lambda kv: -kv[1]):
        print(f"  {k:<16} {v:3d}")
    print("\nby scene:")
    for k, v in sorted(by_scene().items(), key=lambda kv: -kv[1]):
        print(f"  {k:<16} {v:3d}")
