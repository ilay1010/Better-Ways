"""Cached transit dataset for Israel (Hebrew labels).

Coordinates, stop names and line numbers approximate the real network
(Egged / Afikim / Dan / Metropoline intercity lines, light rail, Israel Railways).
Travel times are typical off-peak in-vehicle minutes between consecutive stops.
Walking transfers are not listed here: routing.py derives them from distance.
"""

MAJOR_HUB = "מרכז תחבורה ראשי"
MINOR_STOP = "תחנה משנית"

# id: (name, lat, lon, hub_type, safety 0-1 — shelter, lighting, staffed, alt. lines)
STOPS = {
    # Jerusalem
    "JLM_CBS":      ("ירושלים - התחנה המרכזית",         31.7890, 35.2030, MAJOR_HUB, 0.95),
    "JLM_YITZHAK":  ("ירושלים - יצחק נבון",             31.7884, 35.2025, MAJOR_HUB, 0.95),
    "JLM_MALHA":    ("קניון מלחה / אצטדיון טדי",        31.7510, 35.1880, MAJOR_HUB, 0.85),
    "JLM_HAR_HOTZ": ("הר חוצבים",                       31.7990, 35.2030, MINOR_STOP, 0.55),
    "JLM_RAMOT":    ("צומת רמות",                       31.8130, 35.2010, MINOR_STOP, 0.50),
    "JLM_MEVASERET":("צומת מבשרת ציון",                 31.8020, 35.1500, MINOR_STOP, 0.45),
    "JLM_SHAAR":    ("מחלף שער הגיא",                   31.8170, 35.0230, MINOR_STOP, 0.25),
    "JLM_CITY_CTR": ("מרכז העיר - יפו (רכבת קלה)",      31.7820, 35.2190, MAJOR_HUB, 0.85),
    "BEIT_SHEMESH": ("תחנת רכבת בית שמש",               31.7580, 34.9890, MINOR_STOP, 0.55),
    # Center
    "MODIIN_CTR":   ("תחנת רכבת מודיעין מרכז",          31.9000, 35.0070, MAJOR_HUB, 0.80),
    "LATRUN":       ("צומת לטרון",                      31.8380, 34.9800, MINOR_STOP, 0.30),
    "BEN_GURION":   ("נמל התעופה בן גוריון",            32.0000, 34.8700, MAJOR_HUB, 0.90),
    "SHAPIRIM":     ("מחלף שפירים",                     32.0000, 34.8350, MINOR_STOP, 0.40),
    "BNEI_BRAK":    ("בני ברק - ז'בוטינסקי",            32.0860, 34.8330, MINOR_STOP, 0.60),
    "PETAH_TIKVA":  ("פתח תקווה - התחנה המרכזית",       32.0890, 34.8860, MAJOR_HUB, 0.80),
    "RAMLA":        ("צומת רמלה",                       31.9280, 34.8700, MINOR_STOP, 0.40),
    "LOD":          ("תחנת רכבת לוד",                   31.9460, 34.8760, MAJOR_HUB, 0.75),
    "RISHON_MD":    ("ראשון לציון משה דיין",            31.9880, 34.7710, MINOR_STOP, 0.65),
    "HERZLIYA":     ("תחנת רכבת הרצליה",                32.1640, 34.8180, MINOR_STOP, 0.70),
    "NETANYA":      ("תחנת רכבת נתניה",                 32.3200, 34.8690, MAJOR_HUB, 0.75),
    "NETANYA_CBS":  ("נתניה - התחנה המרכזית",           32.3290, 34.8580, MINOR_STOP, 0.60),
    "HADERA":       ("חדרה מערב",                       32.4380, 34.8990, MINOR_STOP, 0.55),
    # Tel Aviv
    "TLV_SAVIDOR":  ("תל אביב סבידור מרכז / ארלוזורוב", 32.0840, 34.7980, MAJOR_HUB, 0.95),
    "TLV_CBS":      ("תל אביב - התחנה המרכזית החדשה",   32.0560, 34.7790, MAJOR_HUB, 0.75),
    "TLV_HASHALOM": ("תל אביב השלום / עזריאלי",         32.0730, 34.7930, MAJOR_HUB, 0.90),
    "TLV_HAHAGANA": ("תל אביב ההגנה",                   32.0540, 34.7860, MAJOR_HUB, 0.80),
    "TLV_CARLEBACH":("קרליבך (הקו האדום)",              32.0680, 34.7820, MINOR_STOP, 0.65),
    "TLV_UNIV":     ("תל אביב אוניברסיטה",              32.1030, 34.8040, MINOR_STOP, 0.60),
    "TLV_DIZENGOFF":("דיזנגוף סנטר",                    32.0750, 34.7750, MINOR_STOP, 0.60),
    # North
    "HAIFA_HOF":    ("חיפה חוף הכרמל",                  32.7936, 34.9575, MAJOR_HUB, 0.90),
    "HAIFA_CTR":    ("חיפה מרכז השמונה",                32.8222, 34.9970, MINOR_STOP, 0.65),
    "HAIFA_MIFRATZ":("חיפה מרכזית המפרץ",               32.7930, 35.0360, MAJOR_HUB, 0.85),
    "AKKO":         ("תחנת רכבת עכו",                   32.9285, 35.0830, MINOR_STOP, 0.60),
    "NAHARIYA":     ("תחנת רכבת נהריה",                 33.0050, 35.0985, MAJOR_HUB, 0.70),
    "KARMIEL":      ("תחנת רכבת כרמיאל",                32.9145, 35.2860, MINOR_STOP, 0.60),
    "AFULA":        ("עפולה - התחנה המרכזית",           32.6080, 35.2890, MINOR_STOP, 0.55),
    "NAZARETH":     ("נצרת - אזור התעשייה",             32.7000, 35.3030, MINOR_STOP, 0.45),
    "TIBERIAS":     ("טבריה - התחנה המרכזית",           32.7930, 35.5330, MAJOR_HUB, 0.65),
    "SAFED":        ("צפת - התחנה המרכזית",             32.9650, 35.4960, MINOR_STOP, 0.50),
    "KIRYAT_SHMONA":("קריית שמונה - התחנה המרכזית",     33.2080, 35.5700, MINOR_STOP, 0.50),
    # South
    "ASHDOD":       ("אשדוד עד הלום",                   31.7740, 34.6650, MINOR_STOP, 0.60),
    "ASHKELON":     ("תחנת רכבת אשקלון",                31.6770, 34.6050, MINOR_STOP, 0.55),
    "KIRYAT_GAT":   ("תחנת רכבת קריית גת",              31.6030, 34.7790, MINOR_STOP, 0.45),
    "BS_NORTH":     ("באר שבע צפון / אוניברסיטה",       31.2620, 34.8090, MAJOR_HUB, 0.80),
    "BS_CTR":       ("באר שבע מרכז",                    31.2430, 34.7980, MAJOR_HUB, 0.80),
    "BS_CBS":       ("באר שבע - התחנה המרכזית",         31.2440, 34.7960, MAJOR_HUB, 0.75),
    "EILAT":        ("אילת - התחנה המרכזית",            29.5560, 34.9510, MAJOR_HUB, 0.70),
}

# City of each stop, for "[name] city" autocomplete labels
CITY = {
    "JLM_CBS": "ירושלים", "JLM_YITZHAK": "ירושלים", "JLM_MALHA": "ירושלים", "JLM_HAR_HOTZ": "ירושלים",
    "JLM_RAMOT": "ירושלים", "JLM_MEVASERET": "מבשרת ציון", "JLM_SHAAR": "שער הגיא", "JLM_CITY_CTR": "ירושלים",
    "BEIT_SHEMESH": "בית שמש", "MODIIN_CTR": "מודיעין", "LATRUN": "לטרון", "BEN_GURION": "נתב״ג",
    "SHAPIRIM": "אור יהודה", "BNEI_BRAK": "בני ברק", "PETAH_TIKVA": "פתח תקווה", "RAMLA": "רמלה",
    "LOD": "לוד", "RISHON_MD": "ראשון לציון", "HERZLIYA": "הרצליה", "NETANYA": "נתניה",
    "NETANYA_CBS": "נתניה", "HADERA": "חדרה", "TLV_SAVIDOR": "תל אביב", "TLV_CBS": "תל אביב",
    "TLV_HASHALOM": "תל אביב", "TLV_HAHAGANA": "תל אביב", "TLV_CARLEBACH": "תל אביב", "TLV_UNIV": "תל אביב",
    "TLV_DIZENGOFF": "תל אביב", "HAIFA_HOF": "חיפה", "HAIFA_CTR": "חיפה", "HAIFA_MIFRATZ": "חיפה",
    "AKKO": "עכו", "NAHARIYA": "נהריה", "KARMIEL": "כרמיאל", "AFULA": "עפולה", "NAZARETH": "נצרת",
    "TIBERIAS": "טבריה", "SAFED": "צפת", "KIRYAT_SHMONA": "קריית שמונה", "ASHDOD": "אשדוד",
    "ASHKELON": "אשקלון", "KIRYAT_GAT": "קריית גת", "BS_NORTH": "באר שבע", "BS_CTR": "באר שבע",
    "BS_CBS": "באר שבע", "EILAT": "אילת",
}

# line: (mode, headway_minutes, [stop ids in order], [minutes between consecutive stops])
LINES = {
    # Jerusalem - Tel Aviv corridor
    "480":  ("Bus",   10, ["JLM_CBS", "JLM_MEVASERET", "JLM_SHAAR", "SHAPIRIM", "TLV_SAVIDOR"], [8, 15, 22, 12]),
    "405":  ("Bus",   15, ["JLM_CBS", "JLM_MEVASERET", "JLM_SHAAR", "LATRUN", "RAMLA", "TLV_CBS"], [8, 15, 10, 20, 25]),
    "947":  ("Bus",   30, ["JLM_CBS", "JLM_SHAAR", "BEN_GURION", "PETAH_TIKVA"], [22, 25, 20]),
    "400":  ("Bus",   20, ["JLM_CBS", "JLM_SHAAR", "BNEI_BRAK"], [22, 35]),
    "423":  ("Bus",   45, ["JLM_RAMOT", "JLM_MEVASERET", "JLM_SHAAR", "LATRUN"], [12, 14, 10]),
    "438":  ("Bus",   40, ["MODIIN_CTR", "LATRUN", "JLM_SHAAR", "JLM_MALHA"], [12, 10, 30]),
    "160":  ("Bus",   12, ["JLM_MALHA", "JLM_MEVASERET", "LATRUN", "RAMLA"], [12, 25, 18]),
    "471":  ("Bus",   40, ["JLM_MALHA", "JLM_MEVASERET", "JLM_SHAAR", "SHAPIRIM", "BNEI_BRAK", "TLV_UNIV"], [18, 15, 22, 18, 14]),
    "427":  ("Bus",   30, ["JLM_RAMOT", "JLM_MEVASERET", "JLM_SHAAR", "SHAPIRIM", "TLV_CBS"], [12, 14, 22, 20]),
    # Intercity buses
    "960":  ("Bus",   30, ["JLM_CBS", "JLM_SHAAR", "HAIFA_MIFRATZ"], [22, 100]),
    "910":  ("Bus",   20, ["TLV_SAVIDOR", "NETANYA_CBS", "HAIFA_MIFRATZ"], [30, 60]),
    "370":  ("Bus",   15, ["TLV_CBS", "BS_CBS"], [90]),
    "470":  ("Bus",   30, ["JLM_CBS", "KIRYAT_GAT", "BS_CBS"], [65, 40]),
    "394":  ("Bus",   90, ["TLV_CBS", "BS_CBS", "EILAT"], [95, 190]),
    "444":  ("Bus",  120, ["JLM_CBS", "EILAT"], [270]),
    "962":  ("Bus",   60, ["JLM_CBS", "TIBERIAS"], [150]),
    "430":  ("Bus",   30, ["HAIFA_MIFRATZ", "NAZARETH", "TIBERIAS"], [40, 35]),
    "845":  ("Bus",   60, ["TLV_SAVIDOR", "KIRYAT_SHMONA"], [180]),
    "450":  ("Bus",   45, ["TIBERIAS", "SAFED", "KIRYAT_SHMONA"], [40, 40]),
    "361":  ("Bus",   30, ["HAIFA_MIFRATZ", "KARMIEL", "SAFED"], [40, 35]),
    "320":  ("Bus",   15, ["TLV_CBS", "ASHDOD"], [50]),
    "364":  ("Bus",   40, ["ASHKELON", "BS_CBS"], [70]),
    "417":  ("Bus",   15, ["BEIT_SHEMESH", "JLM_CBS"], [40]),
    "355":  ("Bus",   15, ["AFULA", "NAZARETH"], [20]),
    # Israel Railways
    "IR":   ("Train", 30, ["JLM_YITZHAK", "BEN_GURION", "TLV_SAVIDOR", "TLV_HASHALOM", "TLV_HAHAGANA"], [21, 11, 4, 3]),
    "MOD":  ("Train", 30, ["MODIIN_CTR", "BEN_GURION", "TLV_SAVIDOR", "TLV_HASHALOM"], [12, 11, 4]),
    "COAST":("Train", 30, ["NAHARIYA", "AKKO", "HAIFA_MIFRATZ", "HAIFA_CTR", "HAIFA_HOF", "HADERA", "NETANYA",
                           "HERZLIYA", "TLV_UNIV", "TLV_SAVIDOR", "TLV_HASHALOM", "TLV_HAHAGANA", "LOD",
                           "KIRYAT_GAT", "BS_NORTH", "BS_CTR"],
             [10, 12, 6, 7, 25, 13, 12, 6, 4, 3, 3, 14, 25, 22, 5]),
    "ASHK": ("Train", 30, ["TLV_SAVIDOR", "TLV_HASHALOM", "TLV_HAHAGANA", "RISHON_MD", "ASHDOD", "ASHKELON"], [3, 3, 10, 18, 12]),
    "KRM":  ("Train", 30, ["KARMIEL", "AKKO", "HAIFA_MIFRATZ", "HAIFA_CTR", "HAIFA_HOF"], [20, 12, 6, 7]),
    "JEZ":  ("Train", 60, ["HAIFA_HOF", "HAIFA_CTR", "HAIFA_MIFRATZ", "AFULA"], [7, 6, 25]),
    "BSH":  ("Train", 60, ["JLM_MALHA", "BEIT_SHEMESH", "LOD", "TLV_HAHAGANA", "TLV_HASHALOM", "TLV_SAVIDOR"], [35, 25, 14, 3, 3]),
    # Urban
    "L1":   ("Light Rail", 6, ["JLM_HAR_HOTZ", "JLM_YITZHAK", "JLM_CITY_CTR"], [7, 6]),
    "RED":  ("Light Rail", 5, ["PETAH_TIKVA", "BNEI_BRAK", "TLV_SAVIDOR", "TLV_CARLEBACH", "TLV_HAHAGANA"], [8, 10, 6, 5]),
    "5":    ("Bus",   8,  ["TLV_CBS", "TLV_HAHAGANA", "TLV_DIZENGOFF", "TLV_SAVIDOR"], [5, 10, 12]),
    "18":   ("Bus",   7,  ["TLV_CBS", "TLV_CARLEBACH", "TLV_DIZENGOFF"], [8, 7]),
    "25":   ("Bus",   12, ["TLV_HAHAGANA", "TLV_HASHALOM", "TLV_SAVIDOR", "TLV_UNIV"], [8, 5, 9]),
    "68":   ("Bus",   10, ["JLM_RAMOT", "JLM_CBS", "JLM_CITY_CTR"], [14, 8]),
    "18J":  ("Bus",   10, ["JLM_CBS", "JLM_MALHA"], [17]),
    "M1":   ("Bus",   8,  ["HAIFA_HOF", "HAIFA_CTR", "HAIFA_MIFRATZ"], [15, 15]),
    "3":    ("Bus",   10, ["BS_CBS", "BS_NORTH"], [10]),
}

# Hebrew display names; lines not listed display their number
LINE_LABEL = {
    "IR": "רכבת ירושלים–ת״א", "MOD": "רכבת מודיעין", "COAST": "רכבת החוף", "ASHK": "רכבת אשקלון",
    "KRM": "רכבת כרמיאל", "JEZ": "רכבת העמק", "BSH": "רכבת בית שמש",
    "L1": "רק״ל אדום (י-ם)", "RED": "רק״ל אדום (ת״א)", "18J": "18", "M1": "מטרונית 1",
}
MODE_LABEL = {"Bus": "אוטובוס", "Train": "רכבת", "Light Rail": "רכבת קלה", "Walk": "הליכה"}

# Peak hours slow buses (not rail) on congested highways
PEAK_WINDOWS = [(7, 9), (16, 19)]
PEAK_BUS_FACTOR = 1.35


def stop_name(stop_id):
    return STOPS[stop_id][0]


def line_label(line):
    return LINE_LABEL.get(line, line)


def lines_at(stop_id):
    return [ln for ln, (_, _, stops, _) in LINES.items() if stop_id in stops]
