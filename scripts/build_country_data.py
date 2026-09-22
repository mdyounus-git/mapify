#!/usr/bin/env python3
"""Generate ``mapify/data/country_attributes.json``.

The country list, ISO codes, centroids and UN subregions come from the Natural
Earth admin-0 layer (public domain). Onto that we join four hand-maintained
tables of plain geographic/legal facts:

  * which side of the road traffic drives on;
  * which writing systems appear on public signage;
  * the main languages on public signage;
  * the visually distinctive class of vehicle registration plate.

These are facts, not anyone's creative expression, and are compiled here from
general public knowledge. Sources used as cross-checks are listed in
``mapify/data/sources.md``. Corrections are welcome -- edit the tables below
and re-run this script.

    python scripts/build_country_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NE_SHP = ROOT / "data" / "naturalearth" / "ne_10m_admin_0_countries"
OUT = ROOT / "mapify" / "data" / "country_attributes.json"

# --------------------------------------------------------------------------
# 1. Left-hand traffic. Everything not listed here drives on the right.
# --------------------------------------------------------------------------
LEFT_HAND_TRAFFIC = {
    # Europe
    "GB", "IE", "MT", "CY", "IM", "JE", "GG",
    # South and East Asia
    "IN", "PK", "BD", "LK", "NP", "BT", "MV", "JP", "ID", "TH", "MY", "SG",
    "BN", "TL", "HK", "MO",
    # Oceania
    "AU", "NZ", "PG", "FJ", "WS", "TO", "SB", "NR", "TV", "KI", "CK", "NU",
    "NF", "TK", "PN",
    # Africa
    "ZA", "NA", "BW", "ZW", "ZM", "MW", "MZ", "TZ", "KE", "UG", "LS", "SZ",
    "MU", "SC",
    # Americas and the Caribbean
    "GY", "SR", "JM", "TT", "BB", "BS", "AG", "DM", "GD", "KN", "LC", "VC",
    "BM", "KY", "VG", "VI", "AI", "MS", "TC", "FK", "SH", "DO_NOT_USE",
}
LEFT_HAND_TRAFFIC.discard("DO_NOT_USE")

# --------------------------------------------------------------------------
# 2. Writing systems on public signage, most prominent first (ISO 15924).
#    Countries absent from this table are assumed to sign in Latin only.
# --------------------------------------------------------------------------
SIGNAGE_SCRIPTS = {
    "RU": ["Cyrl", "Latn"], "BY": ["Cyrl", "Latn"], "UA": ["Cyrl", "Latn"],
    "BG": ["Cyrl", "Latn"], "RS": ["Cyrl", "Latn"], "MK": ["Cyrl", "Latn"],
    "ME": ["Latn", "Cyrl"], "BA": ["Latn", "Cyrl"], "KZ": ["Cyrl", "Latn"],
    "KG": ["Cyrl", "Latn"], "TJ": ["Cyrl", "Latn"], "MN": ["Cyrl", "Latn"],
    "UZ": ["Latn", "Cyrl"], "TM": ["Latn", "Cyrl"],
    "GR": ["Grek", "Latn"], "CY": ["Grek", "Latn"],
    "IL": ["Hebr", "Arab", "Latn"],
    "SA": ["Arab", "Latn"], "AE": ["Arab", "Latn"], "QA": ["Arab", "Latn"],
    "BH": ["Arab", "Latn"], "KW": ["Arab", "Latn"], "OM": ["Arab", "Latn"],
    "YE": ["Arab"], "JO": ["Arab", "Latn"], "SY": ["Arab", "Latn"],
    "IQ": ["Arab", "Latn"], "LB": ["Arab", "Latn"], "PS": ["Arab", "Latn"],
    "EG": ["Arab", "Latn"], "LY": ["Arab", "Latn"], "SD": ["Arab", "Latn"],
    "TN": ["Arab", "Latn"], "DZ": ["Arab", "Latn", "Tfng"],
    "MA": ["Arab", "Tfng", "Latn"], "EH": ["Arab", "Latn"],
    "MR": ["Arab", "Latn"], "IR": ["Arab", "Latn"], "AF": ["Arab", "Latn"],
    "PK": ["Arab", "Latn"],
    "IN": ["Deva", "Latn", "Beng", "Taml", "Telu", "Knda", "Mlym", "Gujr",
           "Guru", "Orya"],
    "NP": ["Deva", "Latn"], "BD": ["Beng", "Latn"],
    "LK": ["Sinh", "Taml", "Latn"], "BT": ["Tibt", "Latn"],
    "MV": ["Thaa", "Latn"],
    "TH": ["Thai", "Latn"], "LA": ["Laoo", "Latn"], "KH": ["Khmr", "Latn"],
    "MM": ["Mymr", "Latn"],
    "CN": ["Hani", "Latn"], "TW": ["Hani", "Latn"], "HK": ["Hani", "Latn"],
    "MO": ["Hani", "Latn"], "SG": ["Latn", "Hani", "Taml"],
    "JP": ["Jpan", "Hani", "Latn"], "KR": ["Hang", "Latn"], "KP": ["Hang"],
    "GE": ["Geor", "Latn"], "AM": ["Armn", "Latn"], "AZ": ["Latn"],
    "ET": ["Ethi", "Latn"], "ER": ["Ethi", "Arab", "Latn"],
    "CA": ["Latn", "Cans"],
}

# --------------------------------------------------------------------------
# 3. Main languages on public signage (ISO 639-1 where one exists).
# --------------------------------------------------------------------------
SIGNAGE_LANGS = {
    "AT": ["de"], "DE": ["de"], "CH": ["de", "fr", "it"], "LI": ["de"],
    "FR": ["fr"], "BE": ["nl", "fr", "de"], "LU": ["fr", "de", "lb"],
    "MC": ["fr"], "IT": ["it"], "SM": ["it"], "VA": ["it"],
    "ES": ["es", "ca", "gl", "eu"], "PT": ["pt"], "AD": ["ca"],
    "NL": ["nl"], "GB": ["en"], "IE": ["en", "ga"], "MT": ["mt", "en"],
    "DK": ["da"], "SE": ["sv"], "NO": ["no"], "FI": ["fi", "sv"],
    "IS": ["is"], "EE": ["et"], "LV": ["lv"], "LT": ["lt"],
    "PL": ["pl"], "CZ": ["cs"], "SK": ["sk"], "HU": ["hu"], "SI": ["sl"],
    "HR": ["hr"], "BA": ["bs", "hr", "sr"], "RS": ["sr"], "ME": ["sr"],
    "MK": ["mk"], "AL": ["sq"], "XK": ["sq", "sr"], "BG": ["bg"],
    "RO": ["ro"], "MD": ["ro", "ru"], "GR": ["el"], "CY": ["el", "tr", "en"],
    "TR": ["tr"], "RU": ["ru"], "BY": ["be", "ru"], "UA": ["uk", "ru"],
    "KZ": ["kk", "ru"], "KG": ["ky", "ru"], "TJ": ["tg", "ru"],
    "UZ": ["uz", "ru"], "TM": ["tk", "ru"], "GE": ["ka", "en"],
    "AM": ["hy", "en"], "AZ": ["az", "en"], "MN": ["mn"],
    "US": ["en", "es"], "CA": ["en", "fr"], "MX": ["es"],
    "BR": ["pt"], "AR": ["es"], "CL": ["es"], "CO": ["es"], "PE": ["es"],
    "VE": ["es"], "EC": ["es"], "BO": ["es"], "PY": ["es"], "UY": ["es"],
    "GY": ["en"], "SR": ["nl"], "GF": ["fr"],
    "CN": ["zh"], "TW": ["zh"], "HK": ["zh", "en"], "MO": ["zh", "pt"],
    "JP": ["ja"], "KR": ["ko"], "KP": ["ko"],
    "IN": ["hi", "en", "ta", "te", "kn", "ml", "bn", "mr", "gu", "pa"],
    "PK": ["ur", "en"], "BD": ["bn", "en"], "NP": ["ne", "en"],
    "LK": ["si", "ta", "en"], "MV": ["dv", "en"], "BT": ["dz", "en"],
    "TH": ["th", "en"], "VN": ["vi"], "LA": ["lo"], "KH": ["km"],
    "MM": ["my", "en"], "MY": ["ms", "en"], "SG": ["en", "zh", "ms", "ta"],
    "ID": ["id"], "PH": ["en", "tl"], "BN": ["ms", "en"], "TL": ["pt", "id"],
    "AU": ["en"], "NZ": ["en", "mi"], "PG": ["en"], "FJ": ["en"],
    "SA": ["ar", "en"], "AE": ["ar", "en"], "QA": ["ar", "en"],
    "KW": ["ar", "en"], "BH": ["ar", "en"], "OM": ["ar", "en"],
    "YE": ["ar"], "JO": ["ar", "en"], "SY": ["ar"], "IQ": ["ar", "ku"],
    "LB": ["ar", "fr", "en"], "IL": ["he", "ar", "en"], "PS": ["ar", "en"],
    "IR": ["fa", "en"], "AF": ["fa", "ps"],
    "EG": ["ar", "en"], "LY": ["ar"], "TN": ["ar", "fr"], "DZ": ["ar", "fr"],
    "MA": ["ar", "fr"], "SD": ["ar", "en"], "MR": ["ar", "fr"],
    "ZA": ["en", "af"], "NA": ["en", "af"], "BW": ["en"], "ZW": ["en"],
    "ZM": ["en"], "MW": ["en"], "MZ": ["pt"], "AO": ["pt"], "CV": ["pt"],
    "TZ": ["sw", "en"], "KE": ["sw", "en"], "UG": ["en", "sw"],
    "RW": ["rw", "en", "fr"], "BI": ["fr", "rn"], "ET": ["am", "en"],
    "ER": ["ti", "ar", "en"], "SO": ["so", "ar"], "DJ": ["fr", "ar"],
    "NG": ["en"], "GH": ["en"], "SN": ["fr"], "CI": ["fr"], "ML": ["fr"],
    "BF": ["fr"], "NE": ["fr"], "TD": ["fr", "ar"], "CM": ["fr", "en"],
    "CD": ["fr"], "CG": ["fr"], "GA": ["fr"], "MG": ["mg", "fr"],
    "MU": ["en", "fr"], "SC": ["en", "fr"],
}

# --------------------------------------------------------------------------
# 4. Vehicle registration plate classes, chosen so that they are actually
#    distinguishable in a photograph.
# --------------------------------------------------------------------------
PLATE_EU_BAND = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE", "NO", "IS", "RS", "MK", "AL", "ME", "BA", "UA", "MD",
    "TR", "GE", "AM", "AZ", "BY", "XK", "LI", "MC", "AD", "SM",
}
PLATE_CLASSES = {
    **{c: "eu_blue_band" for c in PLATE_EU_BAND},
    "GB": "uk_yellow_rear",
    "US": "north_america", "CA": "north_america", "MX": "north_america",
    "NL": "all_yellow", "LU": "all_yellow",
    "CN": "cn_blue",
    "RU": "ru_flag_band",
    "JP": "jp_compact",
    "SA": "arabic_plate", "AE": "arabic_plate", "QA": "arabic_plate",
    "KW": "arabic_plate", "BH": "arabic_plate", "OM": "arabic_plate",
    "EG": "arabic_plate", "JO": "arabic_plate", "IQ": "arabic_plate",
    "IR": "arabic_plate", "LY": "arabic_plate", "SY": "arabic_plate",
    "IN": "in_white_yellow", "BD": "in_white_yellow", "NP": "in_white_yellow",
    "LK": "in_white_yellow", "PK": "in_white_yellow",
    "AU": "oceania_state", "NZ": "oceania_state",
    "BR": "mercosur", "AR": "mercosur", "UY": "mercosur", "PY": "mercosur",
    "BO": "mercosur",
}

# Countries whose name should not be reported without the caveat that the
# Natural Earth default point of view is being used for boundaries.
DISPUTED_NOTE = {"XK", "PS", "EH", "TW", "SS", "CY"}


def main() -> int:
    try:
        import shapefile  # pyshp
    except ImportError:
        print("pyshp required: pip install pyshp", file=sys.stderr)
        return 2
    if not NE_SHP.with_suffix(".shp").exists():
        print(
            f"missing {NE_SHP}.shp -- run scripts/fetch_data.py first",
            file=sys.stderr,
        )
        return 2

    reader = shapefile.Reader(str(NE_SHP))
    fields = [f[0] for f in reader.fields[1:]]
    out: dict[str, dict[str, object]] = {}
    skipped: list[str] = []

    for sr in reader.iterShapeRecords():
        rec = dict(zip(fields, list(sr.record)))

        def pick(*keys: str) -> str | None:
            for k in keys:
                v = rec.get(k)
                if isinstance(v, str):
                    v = v.strip()
                    if v and v not in {"-99", "-1"}:
                        return v
            return None

        iso2 = pick("ISO_A2_EH", "ISO_A2", "WB_A2")
        name = pick("NAME_EN", "NAME", "ADMIN") or "?"
        if not iso2 or len(iso2) != 2:
            skipped.append(name)
            continue
        iso2 = iso2.upper()

        try:
            lon = float(rec.get("LABEL_X"))
            lat = float(rec.get("LABEL_Y"))
        except (TypeError, ValueError):
            bbox = sr.shape.bbox
            lon = (bbox[0] + bbox[2]) / 2.0
            lat = (bbox[1] + bbox[3]) / 2.0

        entry = {
            "name": name,
            "name_long": pick("NAME_LONG", "FORMAL_EN"),
            "iso_a3": pick("ISO_A3_EH", "ISO_A3", "ADM0_A3"),
            "continent": pick("CONTINENT"),
            "subregion": pick("SUBREGION"),
            "region_un": pick("REGION_UN"),
            "label_lat": round(lat, 4),
            "label_lon": round(lon, 4),
            "drive_side": "left" if iso2 in LEFT_HAND_TRAFFIC else "right",
            "scripts": SIGNAGE_SCRIPTS.get(iso2, ["Latn"]),
            "languages": SIGNAGE_LANGS.get(iso2, []),
            "plate_class": PLATE_CLASSES.get(iso2, "plain_white"),
            "disputed_boundary_note": iso2 in DISPUTED_NOTE,
        }
        # Keep the larger polygon when Natural Earth splits a code across rows.
        prev = out.get(iso2)
        if prev is None:
            out[iso2] = entry
    reader.close()

    payload = {
        "_meta": {
            "generated_by": "scripts/build_country_data.py",
            "country_list_source": (
                "Natural Earth 10m admin-0 countries (public domain); "
                "boundaries follow the Natural Earth default point of view"
            ),
            "attribute_source": (
                "Hand-maintained tables of public geographic/legal facts. "
                "See mapify/data/sources.md"
            ),
            "count": len(out),
        },
        "countries": dict(sorted(out.items())),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")

    left = sum(1 for v in out.values() if v["drive_side"] == "left")
    nonlatin = sum(1 for v in out.values() if v["scripts"] != ["Latn"])
    print(f"wrote {OUT} ({OUT.stat().st_size:,} B)")
    print(f"  {len(out)} countries, {left} drive on the left, "
          f"{nonlatin} sign in a non-Latin script")
    if skipped:
        print(f"  {len(skipped)} rows had no usable ISO-3166 alpha-2 code "
              f"(e.g. {', '.join(skipped[:4])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
