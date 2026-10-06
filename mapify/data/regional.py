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

def _code(c: str) -> str:
    """An ISO 4217 code, counted only beside a number: "TRY 20", "20 SAR".

    As bare words several are English -- "TRY OUR NEW MENU", "PHP" on a
    programming poster, "Hong Kong SAR" -- and would name a country from
    a slogan.
    """
    return rf"\b{c}\s?\d|\d\s?{c}\b"


# Currency signs and codes -> countries; "narrow" marks the ones worth
# acting on. Codes count only beside a number (see _code).
CURRENCIES: list[tuple[str, list[str], bool]] = [
    (r"₹|" + _code("INR"), ["IN"], True),
    # "Rs" is the rupee of India, Pakistan, Sri Lanka, Nepal, Mauritius
    # and the Seychelles alike.
    (r"\bRs\.?\s?\d", ["IN", "PK", "LK", "NP", "MU", "SC"], False),
    (r"৳|\bTk\.?\s?\d|" + _code("BDT"), ["BD"], True),
    (_code("PKR"), ["PK"], True), (r"රු|" + _code("LKR"), ["LK"], True),
    (_code("NPR"), ["NP"], True),
    (r"€|" + _code("EUR"), ["DE", "FR", "IT", "ES", "NL", "BE", "AT", "PT", "IE",
                            "FI", "GR", "SK", "SI", "LT", "LV", "EE", "HR", "LU",
                            "MT", "CY"], False),
    (r"£|" + _code("GBP"), ["GB", "GI", "FK"], True),
    # The yen sign is written for the Chinese yuan too.
    (r"¥", ["JP", "CN"], True), (r"円|" + _code("JPY"), ["JP"], True),
    (r"元", ["CN", "TW"], True), (_code("CNY") + "|" + _code("RMB"), ["CN"], True),
    (r"₩|원|" + _code("KRW"), ["KR"], True), (r"₱|" + _code("PHP"), ["PH"], True),
    (r"฿|บาท|" + _code("THB"), ["TH"], True), (r"₫|" + _code("VND"), ["VN"], True),
    (r"\bRM\s?\d|" + _code("MYR"), ["MY"], True),
    (r"\bRp\.?\s?\d|" + _code("IDR"), ["ID"], True),
    (r"(?<![A-Za-z])R\$|" + _code("BRL"), ["BR"], True),
    (r"₺|" + _code("TRY") + "|" + _code("TL"), ["TR"], True),
    (r"₽|руб|" + _code("RUB"), ["RU"], True), (r"₴|грн|" + _code("UAH"), ["UA"], True),
    (r"zł|" + _code("PLN"), ["PL"], True), (r"Kč|" + _code("CZK"), ["CZ"], True),
    (r"\bFr\.\s?\d|" + _code("CHF"), ["CH", "LI"], True),
    (r"د\.إ|" + _code("AED"), ["AE"], True), (r"ر\.س|" + _code("SAR"), ["SA"], True),
    (r"\bKSh\s?\d|" + _code("KES"), ["KE"], True), (r"₦|" + _code("NGN"), ["NG"], True),
    (r"₵|" + _code("GHS"), ["GH"], True),
    (_code("ZAR") + r"|\bR\s?\d+[.,]\d{2}\b", ["ZA"], False),
    (r"E£|ج\.م|" + _code("EGP"), ["EG"], True),
    (r"₪|" + _code("ILS") + "|" + _code("NIS"), ["IL"], True),
    # A letter before the sign makes it another dollar: "US$" is not "S$".
    (_code("MXN"), ["MX"], True), (r"(?<![A-Za-z])A\$|" + _code("AUD"), ["AU"], True),
    (r"(?<![A-Za-z])C\$|" + _code("CAD"), ["CA"], True),
    (r"\bNZ\$|" + _code("NZD"), ["NZ"], True),
    (r"(?<![A-Za-z])S\$|" + _code("SGD"), ["SG"], True),
    (r"\bHK\$|" + _code("HKD"), ["HK"], True),
    # Dollar prices are also how much of the world quotes tourists.
    (r"\bUS\$|" + _code("USD"), ["US"], False),
    (r"\$\s?\d", ["US", "CA", "AU", "NZ", "SG", "HK", "MX", "AR", "CL", "CO"], False),
    (r"\bkr\.?\s?\d|\d\s?kr\b|" + _code("SEK") + "|" + _code("NOK") + "|"
     + _code("DKK"), ["SE", "NO", "DK", "IS"], False),
]

# Countries that post road speeds and distances in miles.
MPH_COUNTRIES = frozenset({"US", "GB", "LR", "MM", "PR", "GU", "VI", "AS", "MP",
                           "BS", "BZ", "AG", "DM", "GD", "KN", "LC", "VC", "WS",
                           "KY", "VG", "FK", "TC", "AI", "MS", "IM", "JE", "GG"})

# Postcode shapes distinctive enough to name a country. Five-digit and
# four-digit codes are left out: too many countries use them.
POSTCODES: list[tuple[str, str, re.Pattern[str]]] = [
    # India's six-digit PIN only where it is labelled or ends an address
    # line ("Bengaluru - 560001"): a bare six-digit number is also a
    # postcode in China, Russia, Singapore, Romania and Vietnam, or a price.
    ("IN", "PIN code", re.compile(
        r"(?i)(?:\bpin(?:\s?code)?\s?[:.\-]?\s?|[a-z]{3,}\s?[-–]\s?)[1-8]\d{2}\s?\d{3}(?!\d)")),
    ("GB", "UK postcode", re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s\d[A-Z]{2}\b")),
    ("CA", "Canadian postal code", re.compile(r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b")),
    # Not "1030 AM", "1500 KM", "2019 BC": units and eras that look alike.
    ("NL", "Dutch postcode", re.compile(
        r"\b[1-9]\d{3}\s?(?!AM|PM|KM|CM|MM|KG|ML|AD|BC|HP|CC|MB|GB|KB|TB|HZ|FT|IN|RS|SA|SD|SS)"
        r"[A-Z]{2}\b")),
    # The postal mark, or it is a phone number ("555-1234").
    ("JP", "Japanese postal code", re.compile(r"〒\s?\d{3}-\d{4}")),
    ("BR", "Brazilian CEP", re.compile(r"(?i)\bCEP\s?:?\s?\d{5}-?\d{3}\b")),
    # Followed by the town, as written: "00-950 Warszawa", "1000-001 Lisboa".
    ("PL", "Polish postcode", re.compile(r"\b\d{2}-\d{3}\s+[A-ZĆŁŚŻŹ][a-ząćęłńóśźż]{2,}")),
    ("PT", "Portuguese postcode", re.compile(r"\b\d{4}-\d{3}\s+[A-Z][a-zà-ú]{2,}")),
    # An Eircode's last four always hold a letter.
    ("IE", "Eircode", re.compile(
        r"\b[AC-FHKNPRTV-Y]\d{2}\s?(?=[\dAC-FHKNPRTV-Y]*[AC-FHKNPRTV-Y])"
        r"[AC-FHKNPRTV-Y\d]{4}\b")),
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
