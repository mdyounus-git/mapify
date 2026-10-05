"""Regional facts that text in a photograph can be checked against.

Every table here is public, factual and hand-maintained: ITU calling
codes, ISO country-code top-level domains, currency signs, speed units,
postcode shapes, Indian registration state codes, and which countries a
handful of telecom, retail and banking brands operate in. None of it is
copied from a dataset.

Two cautions apply everywhere, and the techniques that use these tables
repeat them in their findings:

  * **Coverage is partial.** A brand or format missing from a table
    proves nothing; only a match is evidence.
  * **Many signals are shared.** "$" is dozens of countries, a five-digit
    number is a US ZIP code or a French one or neither. Each entry is
    marked with how much it narrows, and weak matches are reported as
    weak.
"""

from __future__ import annotations

import re

__all__ = ["CALLING_CODES", "INDIA_STD", "GENERIC_TLDS", "CURRENCIES",
           "MPH_COUNTRIES", "POSTCODES", "INDIA_PLATE_STATES", "BRANDS",
           "SIGN_CONVENTION"]

# ITU-T E.164 country calling codes (the distinctive ones; "1" is NANP).
CALLING_CODES: dict[str, list[str]] = {
    "1": ["US", "CA", "PR", "JM", "TT", "BS", "BB", "DO"],
    "7": ["RU", "KZ"], "20": ["EG"], "27": ["ZA"], "30": ["GR"], "31": ["NL"],
    "32": ["BE"], "33": ["FR"], "34": ["ES"], "36": ["HU"], "39": ["IT"],
    "40": ["RO"], "41": ["CH"], "43": ["AT"], "44": ["GB"], "45": ["DK"],
    "46": ["SE"], "47": ["NO"], "48": ["PL"], "49": ["DE"], "51": ["PE"],
    "52": ["MX"], "53": ["CU"], "54": ["AR"], "55": ["BR"], "56": ["CL"],
    "57": ["CO"], "58": ["VE"], "60": ["MY"], "61": ["AU"], "62": ["ID"],
    "63": ["PH"], "64": ["NZ"], "65": ["SG"], "66": ["TH"], "81": ["JP"],
    "82": ["KR"], "84": ["VN"], "86": ["CN"], "90": ["TR"], "91": ["IN"],
    "92": ["PK"], "93": ["AF"], "94": ["LK"], "95": ["MM"], "98": ["IR"],
    "212": ["MA"], "213": ["DZ"], "216": ["TN"], "218": ["LY"], "220": ["GM"],
    "221": ["SN"], "225": ["CI"], "233": ["GH"], "234": ["NG"], "237": ["CM"],
    "243": ["CD"], "249": ["SD"], "250": ["RW"], "251": ["ET"], "254": ["KE"],
    "255": ["TZ"], "256": ["UG"], "260": ["ZM"], "263": ["ZW"], "351": ["PT"],
    "353": ["IE"], "354": ["IS"], "358": ["FI"], "370": ["LT"], "371": ["LV"],
    "372": ["EE"], "380": ["UA"], "381": ["RS"], "385": ["HR"], "386": ["SI"],
    "420": ["CZ"], "421": ["SK"], "852": ["HK"], "853": ["MO"], "855": ["KH"],
    "856": ["LA"], "880": ["BD"], "886": ["TW"], "960": ["MV"], "961": ["LB"],
    "962": ["JO"], "963": ["SY"], "964": ["IQ"], "965": ["KW"], "966": ["SA"],
    "967": ["YE"], "968": ["OM"], "970": ["PS"], "971": ["AE"], "972": ["IL"],
    "973": ["BH"], "974": ["QA"], "975": ["BT"], "976": ["MN"], "977": ["NP"],
    "992": ["TJ"], "994": ["AZ"], "995": ["GE"], "998": ["UZ"],
}

# Indian trunk (STD) codes of large cities, as written after the leading 0.
INDIA_STD: dict[str, str] = {
    "11": "Delhi", "22": "Mumbai", "33": "Kolkata", "44": "Chennai",
    "80": "Bengaluru", "40": "Hyderabad", "20": "Pune", "79": "Ahmedabad",
    "141": "Jaipur", "522": "Lucknow", "484": "Kochi", "471": "Thiruvananthapuram",
    "422": "Coimbatore", "452": "Madurai", "431": "Tiruchirappalli",
    "413": "Puducherry", "824": "Mangaluru", "821": "Mysuru", "891": "Visakhapatnam",
    "866": "Vijayawada", "674": "Bhubaneswar", "612": "Patna", "172": "Chandigarh",
    "712": "Nagpur", "755": "Bhopal", "731": "Indore", "361": "Guwahati",
}

# Top-level domains that are used worldwide and name no country, or are
# country codes sold generically. A match on these is not geography.
GENERIC_TLDS = frozenset({
    "com", "org", "net", "edu", "gov", "info", "biz", "io", "ai", "co", "tv",
    "me", "ly", "app", "dev", "xyz", "online", "site", "store", "shop", "tech",
})

# Currency signs and codes -> countries; "narrow" marks the ones worth
# acting on.
CURRENCIES: list[tuple[str, list[str], bool]] = [
    (r"₹|\bINR\b|\bRs\.?\s?\d", ["IN"], True),
    (r"৳|\bBDT\b|\bTk\.?\s?\d", ["BD"], True),
    (r"\bPKR\b", ["PK"], True), (r"\bLKR\b|රු", ["LK"], True),
    (r"\bNPR\b", ["NP"], True),
    (r"€|\bEUR\b", ["DE", "FR", "IT", "ES", "NL", "BE", "AT", "PT", "IE", "FI",
                   "GR", "SK", "SI", "LT", "LV", "EE", "HR", "LU", "MT", "CY"], False),
    (r"£|\bGBP\b", ["GB", "GI", "FK"], True),
    (r"¥|\bJPY\b|円", ["JP"], True), (r"\bCNY\b|\bRMB\b|元", ["CN"], True),
    (r"₩|\bKRW\b|원", ["KR"], True), (r"₱|\bPHP\b", ["PH"], True),
    (r"฿|\bTHB\b|บาท", ["TH"], True), (r"₫|\bVND\b", ["VN"], True),
    (r"\bRM\s?\d|\bMYR\b", ["MY"], True), (r"\bRp\.?\s?\d|\bIDR\b", ["ID"], True),
    (r"R\$|\bBRL\b", ["BR"], True), (r"₺|\bTRY\b|\bTL\b", ["TR"], True),
    (r"₽|\bRUB\b|руб", ["RU"], True), (r"₴|\bUAH\b|грн", ["UA"], True),
    (r"zł|\bPLN\b", ["PL"], True), (r"Kč|\bCZK\b", ["CZ"], True),
    (r"\bCHF\b|\bFr\.\s?\d", ["CH", "LI"], True),
    (r"\bAED\b|د\.إ", ["AE"], True), (r"\bSAR\b|ر\.س", ["SA"], True),
    (r"\bKSh\b|\bKES\b", ["KE"], True), (r"₦|\bNGN\b", ["NG"], True),
    (r"\bGH₵|₵|\bGHS\b", ["GH"], True), (r"\bZAR\b|\bR\s?\d+[.,]\d{2}\b", ["ZA"], False),
    (r"E£|\bEGP\b|ج\.م", ["EG"], True), (r"₪|\bILS\b|\bNIS\b", ["IL"], True),
    (r"\bMXN\b", ["MX"], True), (r"\bAUD\b|A\$", ["AU"], True),
    (r"\bCAD\b|C\$", ["CA"], True), (r"\bNZD\b|NZ\$", ["NZ"], True),
    (r"\bSGD\b|S\$", ["SG"], True), (r"\bHKD\b|HK\$", ["HK"], True),
    (r"\bUSD\b|US\$", ["US"], False),
    (r"\$\s?\d", ["US", "CA", "AU", "NZ", "SG", "HK", "MX", "AR", "CL", "CO"], False),
    (r"\bkr\.?\s?\d|\bSEK\b|\bNOK\b|\bDKK\b", ["SE", "NO", "DK", "IS"], False),
]

# Countries that post road speeds and distances in miles.
MPH_COUNTRIES = frozenset({"US", "GB", "LR", "MM", "PR", "GU", "VI", "AS", "MP",
                           "BS", "BZ", "AG", "DM", "GD", "KN", "LC", "VC", "WS",
                           "KY", "VG", "FK", "TC", "AI", "MS", "IM", "JE", "GG"})

# Postcode shapes distinctive enough to name a country. Five-digit and
# four-digit codes are left out: too many countries use them.
POSTCODES: list[tuple[str, str, re.Pattern[str]]] = [
    ("IN", "PIN (6 digits)", re.compile(r"(?<!\d)[1-8]\d{2}\s?\d{3}(?!\d)")),
    ("GB", "UK postcode", re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b")),
    ("CA", "Canadian postal code", re.compile(r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b")),
    ("NL", "Dutch postcode", re.compile(r"\b[1-9]\d{3}\s?[A-Z]{2}\b")),
    ("JP", "Japanese postal code", re.compile(r"〒\s?\d{3}-\d{4}|\b\d{3}-\d{4}\b")),
    ("BR", "Brazilian CEP", re.compile(r"\b\d{5}-\d{3}\b")),
    ("PL", "Polish postcode", re.compile(r"\b\d{2}-\d{3}\b")),
    ("PT", "Portuguese postcode", re.compile(r"\b\d{4}-\d{3}\b")),
    ("IE", "Eircode", re.compile(r"\b[AC-FHKNPRTV-Y]\d{2}\s?[AC-FHKNPRTV-Y\d]{4}\b")),
]

# Indian registration-plate state and territory codes.
INDIA_PLATE_STATES: dict[str, str] = {
    "AN": "Andaman and Nicobar", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh",
    "AS": "Assam", "BR": "Bihar", "CG": "Chhattisgarh", "CH": "Chandigarh",
    "DD": "Dadra, Nagar Haveli, Daman and Diu", "DL": "Delhi", "GA": "Goa",
    "GJ": "Gujarat", "HP": "Himachal Pradesh", "HR": "Haryana", "JH": "Jharkhand",
    "JK": "Jammu and Kashmir", "KA": "Karnataka", "KL": "Kerala", "LA": "Ladakh",
    "LD": "Lakshadweep", "MH": "Maharashtra", "ML": "Meghalaya", "MN": "Manipur",
    "MP": "Madhya Pradesh", "MZ": "Mizoram", "NL": "Nagaland", "OD": "Odisha",
    "OR": "Odisha", "PB": "Punjab", "PY": "Puducherry", "RJ": "Rajasthan",
    "SK": "Sikkim", "TN": "Tamil Nadu", "TR": "Tripura", "TS": "Telangana",
    "TG": "Telangana", "UK": "Uttarakhand", "UP": "Uttar Pradesh",
    "WB": "West Bengal", "BH": "Bharat series (any state)",
}

# Brand -> (kind, countries). Operating countries as publicly listed;
# "global" brands are omitted because they narrow nothing.
BRANDS: dict[str, tuple[str, list[str]]] = {
    # telecom
    "airtel": ("telecom", ["IN", "BD", "LK", "KE", "NG", "UG", "TZ", "ZM", "MW",
                           "RW", "CD", "NE", "TD", "GA", "CG", "MG", "SC"]),
    "jio": ("telecom", ["IN"]), "bsnl": ("telecom", ["IN"]),
    "vodafone idea": ("telecom", ["IN"]), "safaricom": ("telecom", ["KE"]),
    "mtn": ("telecom", ["NG", "GH", "ZA", "UG", "CM", "CI", "RW", "BJ", "ZM", "SS"]),
    "telcel": ("telecom", ["MX"]), "globe telecom": ("telecom", ["PH"]),
    "grameenphone": ("telecom", ["BD"]), "robi": ("telecom", ["BD"]),
    "jazz": ("telecom", ["PK"]), "zong": ("telecom", ["PK"]), "ufone": ("telecom", ["PK"]),
    "dialog": ("telecom", ["LK"]), "ncell": ("telecom", ["NP"]),
    "telstra": ("telecom", ["AU"]), "optus": ("telecom", ["AU"]),
    "turkcell": ("telecom", ["TR"]), "telkomsel": ("telecom", ["ID"]),
    "maxis": ("telecom", ["MY"]), "singtel": ("telecom", ["SG"]),
    "docomo": ("telecom", ["JP"]), "softbank": ("telecom", ["JP"]),
    "verizon": ("telecom", ["US"]), "safaricom m-pesa": ("telecom", ["KE"]),
    "m-pesa": ("telecom", ["KE", "TZ", "MZ", "CD", "GH", "EG", "LS"]),
    "etisalat": ("telecom", ["AE", "EG", "AF"]), "viettel": ("telecom", ["VN", "KH", "LA"]),
    # retail
    "reliance fresh": ("retail", ["IN"]), "reliance digital": ("retail", ["IN"]),
    "reliance trends": ("retail", ["IN"]), "dmart": ("retail", ["IN"]),
    "big bazaar": ("retail", ["IN"]), "spencer's": ("retail", ["IN"]),
    "more supermarket": ("retail", ["IN"]), "tanishq": ("retail", ["IN"]),
    "saravana stores": ("retail", ["IN"]), "pothys": ("retail", ["IN"]),
    "walmart": ("retail", ["US", "CA", "MX", "CL", "CR", "GT", "HN", "NI", "SV"]),
    "target": ("retail", ["US"]), "tesco": ("retail", ["GB", "IE", "CZ", "SK", "HU"]),
    "sainsbury's": ("retail", ["GB"]), "asda": ("retail", ["GB"]),
    "woolworths": ("retail", ["AU", "NZ", "ZA"]), "coles": ("retail", ["AU"]),
    "oxxo": ("retail", ["MX", "CL", "CO", "PE", "BR"]),
    "familymart": ("retail", ["JP", "TW", "TH", "ID", "MY", "PH", "VN"]),
    "lawson": ("retail", ["JP", "ID", "TH", "PH"]),
    "7-eleven": ("retail", ["US", "JP", "TH", "TW", "KR", "MY", "PH", "SG", "MX", "CA"]),
    "indomaret": ("retail", ["ID"]), "alfamart": ("retail", ["ID", "PH"]),
    "biedronka": ("retail", ["PL"]), "mercadona": ("retail", ["ES", "PT"]),
    "rewe": ("retail", ["DE", "AT"]), "edeka": ("retail", ["DE"]),
    "shoprite": ("retail", ["ZA", "NG", "ZM", "GH", "NA", "BW", "MZ"]),
    "pick n pay": ("retail", ["ZA"]), "naivas": ("retail", ["KE"]),
    "big c": ("retail", ["TH", "VN"]), "lotus's": ("retail", ["TH", "MY"]),
    # fuel
    "indian oil": ("fuel", ["IN"]), "bharat petroleum": ("fuel", ["IN"]),
    "hindustan petroleum": ("fuel", ["IN"]), "pemex": ("fuel", ["MX"]),
    "petronas": ("fuel", ["MY"]), "pertamina": ("fuel", ["ID"]),
    "petrobras": ("fuel", ["BR"]), "ypf": ("fuel", ["AR"]), "copec": ("fuel", ["CL"]),
    "petron": ("fuel", ["PH", "MY"]), "ptt": ("fuel", ["TH"]),
    # banks
    "state bank of india": ("bank", ["IN"]), "sbi": ("bank", ["IN"]),
    "hdfc bank": ("bank", ["IN"]), "icici bank": ("bank", ["IN"]),
    "axis bank": ("bank", ["IN"]), "canara bank": ("bank", ["IN"]),
    "indian bank": ("bank", ["IN"]), "bank of baroda": ("bank", ["IN", "UG", "KE"]),
    "kotak": ("bank", ["IN"]), "union bank of india": ("bank", ["IN"]),
    "chase": ("bank", ["US"]), "wells fargo": ("bank", ["US"]),
    "bank of america": ("bank", ["US"]), "barclays": ("bank", ["GB"]),
    "itaú": ("bank", ["BR", "CL", "PY", "UY"]), "bradesco": ("bank", ["BR"]),
    "citibanamex": ("bank", ["MX"]), "equity bank": ("bank", ["KE", "UG", "TZ", "RW"]),
    "maybank": ("bank", ["MY", "SG", "ID"]), "bdo": ("bank", ["PH"]),
    "kasikorn": ("bank", ["TH"]), "vietcombank": ("bank", ["VN"]),
    "bank mandiri": ("bank", ["ID"]), "commonwealth bank": ("bank", ["AU"]),
    "capitec": ("bank", ["ZA"]), "sberbank": ("bank", ["RU"]),
    "ziraat": ("bank", ["TR"]), "al rajhi": ("bank", ["SA"]),
    "emirates nbd": ("bank", ["AE"]),
}

# Road-sign conventions distinctive enough to name countries. Everything
# else broadly follows the Vienna Convention.
SIGN_CONVENTION: dict[str, set[str]] = {
    "mutcd": {"US", "CA", "MX", "PR", "PH", "LR"},
    "australasian": {"AU", "NZ", "PG"},
    "japanese": {"JP"},
    "sadc": {"ZA", "BW", "NA", "LS", "SZ", "ZW", "ZM", "MW", "MZ"},
}
