"""Fictional Cameroonian-style name generator.

All names are random combinations of common name components; they do not
designate any real person.  Only characters available in Latin-1 / WinAnsi
are used so that reportlab's standard fonts can render them.
"""
from __future__ import annotations

import random
import re
import unicodedata

# --- family name components by (broad) origin group -------------------------------
FAMILY = {
    "centre_sud": [
        "NGONO", "ABENA", "ONANA", "MBALLA", "ATANGANA", "ESSOMBA", "ZOA", "OWONA", "MVONDO",
        "BELINGA", "ETOUNDI", "EBODE", "MANGA", "NKOA", "AMOUGOU", "FOUDA", "BIYONG", "EYENGA",
        "ELLA", "ENAMA", "ABOUNA", "MEKA", "NGUEMA", "OBAME", "ASSAM", "BEKOLO", "MINKO", "NLEND",
        "OLINGA", "TSANGA", "ZANGA", "BIDZANGA", "NNANGA", "EKANI", "MBIDA", "SOMO", "EDOA",
        "ETEKI", "ABESSOLO", "MENGUE", "NKODO", "OTTOU", "EVINA", "MEDJO", "NDZANA", "BILOA",
    ],
    "littoral": [
        "MBOCK", "HIAG", "MBOG", "BASSOM", "NYEMB", "LOBE", "EKOLLO", "DIBOUE", "EBELLE",
        "MOUKOKO", "NJOH", "MANDENGUE", "ELIMBI", "ETAME", "BELL", "DIKA", "EPEE", "KINGUE",
        "MUKETE", "SONE", "NDOUMBE", "BIKOI", "MATIP", "LIKENG", "PENDA", "SINGUE", "EBANGA",
    ],
    "ouest": [
        "TCHOUMI", "KAMGA", "FOTSO", "TAGNE", "NGUETSOP", "KENFACK", "DJOUMESSI", "TCHAKOUNTE",
        "NANA", "TEUKENG", "SIMO", "WAFO", "NJIKI", "KAMDEM", "TALLA", "DONGMO", "FEUDJIO",
        "TCHINDA", "NGAMENI", "DJEUMOU", "TSAFACK", "MELI", "YOUMBI", "KUATE", "ZEBAZE", "PENKA",
        "NOUMBISSIE", "TEFOUET", "FOKOU", "KEMAYOU", "TIOTSOP", "MEKUATE", "NJOYA", "MOUNCHILI",
        "NCHARE", "SANDJO", "TOUKAM", "DJAMEN", "KOUAM", "LEKEUFACK",
    ],
    "nord": [
        "ABBA", "HAMADOU", "ISSA", "MOHAMADOU", "OUMAROU", "ALIOUM", "SADOU", "BOUBA", "YOUSSOUFA",
        "ADAMOU", "DAIROU", "HAMAN", "BELLO", "MAHAMAT", "ABDOULAYE", "ALIM", "ZAKARI", "GAMBO",
        "HAMIDOU", "MAIGARI", "SALI", "TIDJANI", "YAYA", "DJIBRILLA",
    ],
    "anglo": [
        "FONYUY", "NGWA", "TABE", "ACHA", "MBAH", "NFOR", "FORCHA", "ASONG", "TANYI", "NJUME",
        "EKEMA", "AWAH", "MBUA", "ETA", "ENOW", "AYUK", "BESONG", "NKWETI", "TEBOH", "CHIA", "SHU",
        "FRU", "NGU", "TITA", "ANYE", "NDIFOR", "BIH", "KANG", "ATABONG", "EBOT", "TAMBE", "NDI",
        "MUMA", "NGEH", "WIRNKAR", "TANKO", "ATEM", "AMBE", "NJI", "EKOKA",
    ],
}

GIVEN_FR = [
    "Jean", "Marie", "Pierre", "Paul", "Joseph", "Emmanuel", "Christelle", "Brice", "Cédric",
    "Nadège", "Sandrine", "Larissa", "Patrick", "Estelle", "Arnaud", "Aurélie", "Hervé", "Ghislain",
    "Carine", "Flore", "Yvan", "Rodrigue", "Blaise", "Stéphane", "Mireille", "Prisca", "Désiré",
    "Éric", "Fabrice", "Gaëlle", "Harold", "Ingrid", "Jocelyne", "Léonie", "Magloire", "Nicaise",
    "Olivier", "Pascaline", "Roméo", "Thierry", "Vanessa", "Wilfried", "Yolande", "Zacharie",
    "Alain", "Bertrand", "Clarisse", "Delphine", "Ernest", "Félicité", "Gilbert", "Honorine",
    "Joël", "Kevin", "Laure", "Mélanie", "Noël", "Océane", "Prosper", "Rosine", "Sylvie", "Térence",
    "Ulrich", "Valérie", "Annie", "Béatrice", "Chantal", "Dieudonné", "Éphraïm", "Fidèle", "Guy",
    "Hélène", "Isidore", "Judith", "Landry", "Maurice", "Nathalie", "Armand", "Aimé", "Josiane",
    "Brigitte", "Cyrille", "Diane", "Évelyne", "Franck", "Gisèle", "Hugues", "Inès", "Julien",
]
GIVEN_FR_COMPOUND = [
    "Marie-Claire", "Marie-Thérèse", "Jean-Pierre", "Jean-Claude", "Paul-Armand", "Anne-Marie",
    "Rose-Aimée", "Jean-Baptiste", "Marie-Louise", "Marie-Josée", "Pierre-Marie", "Éric-Joël",
    "Jean Pierre", "Paul Armand", "Marie Laure", "Jean Marc", "Anne Flore", "Paul Emile",
    "Marie-Noëlle", "Jean-Paul", "Rose-Marie", "Louis-Marie",
]
GIVEN_EN = [
    "Brendaline", "Neba", "Divine", "Precious", "Godlove", "Collins", "Blessing", "Gladys",
    "Randolph", "Peter", "Mary", "Agnes", "Ernest", "Lilian", "Nelson", "Terence", "Veronica",
    "Walter", "Bernard", "Cynthia", "Desmond", "Faith", "Gabriel", "Hilary", "Irene", "Julius",
    "Kelvin", "Linda", "Mercy", "Nixon", "Pamela", "Roland", "Stella", "Tobias", "Ursula", "Victor",
    "Ngum", "Tamfu", "Atem", "Emmanuel", "Cecilia", "Prudence", "Clarisse", "Eric", "Derick",
    "Wilfred", "Rosemary", "Solange", "Lesly", "Fidelis", "Beatrice", "Joseph",
]

# --- names built to be hard for OCR (diacritics, apostrophes, confusable glyphs) -----------
HARD_FAMILY_FR = [
    "N'DJOCK MBOUOMBOUO", "NJIMOUMBOUO TCHAMBA", "MEWOULOU ÉKOUMBOUMBA", "NGOUNOU-MEUKAM",
    "TCHOUMBOUÉ NGUIMFACK", "N'GUEMNJEU MOUMOUNI", "ÉTOUNDI-MBALLA OWONO", "NDJIMBIE-WOUMBOU",
]
HARD_GIVEN_FR = ["Éphraïm-Désiré", "Aïcha-Noëlle", "Hyacinthe Ghislain", "Jean-Rémi Éloi", "Zénabou Anaïs"]
HARD_FAMILY_EN = [
    "NJIMOUMBOUO TCHOUMBOU", "NGWA-NJUMBUM FONCHAM", "MBOUOMBOUO NKWEMBI", "TCHUMBAWOU ENOW-MBONGE",
]
HARD_GIVEN_EN = ["Ephraim Desiree", "Brendaline Njumbum", "Clementine Wirngo", "Fonyuy Nkemnji"]

BIRTH_PLACES = [
    "Yaoundé", "Douala", "Bafoussam", "Bamenda", "Garoua", "Ebolowa", "Bertoua", "Maroua",
    "Ngaoundéré", "Limbé", "Buea", "Kribi", "Édéa", "Dschang", "Nkongsamba", "Sangmélima",
    "Mbalmayo", "Bafia", "Foumban", "Kumba", "Bali", "Mbouda", "Mbengwi", "Kumbo", "Wum",
    "Obala", "Mora", "Guider", "Batouri", "Abong-Mbang", "Ambam", "Loum", "Tiko", "Bangangté",
    "Yagoua", "Meiganga", "Eseka", "Mamfe", "Nkambé", "Akonolinga",
]
BIRTH_PLACE_GROUP = {
    "centre_sud": ["Yaoundé", "Ebolowa", "Sangmélima", "Mbalmayo", "Bafia", "Obala", "Ambam", "Eseka",
                   "Akonolinga", "Kribi", "Bertoua", "Abong-Mbang"],
    "littoral": ["Douala", "Édéa", "Nkongsamba", "Loum", "Kribi", "Limbé", "Yaoundé"],
    "ouest": ["Bafoussam", "Dschang", "Foumban", "Mbouda", "Bangangté", "Douala", "Yaoundé"],
    "nord": ["Garoua", "Maroua", "Ngaoundéré", "Yagoua", "Guider", "Mora", "Meiganga"],
    "anglo": ["Bamenda", "Buea", "Limbé", "Kumba", "Bali", "Mbengwi", "Kumbo", "Wum", "Mamfe",
              "Nkambé", "Tiko"],
}

GROUP_WEIGHTS = {
    "fr": [("centre_sud", 30), ("littoral", 14), ("ouest", 30), ("nord", 12), ("anglo", 14)],
    "en": [("anglo", 62), ("ouest", 14), ("centre_sud", 10), ("littoral", 10), ("nord", 4)],
}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_name(s: str) -> str:
    """Normalise like the verification pipeline: unidecode, uppercase, alnum tokens, sorted."""
    s = strip_accents(s).upper().replace("'", "")
    toks = re.sub(r"[^A-Z0-9]+", " ", s).split()
    return " ".join(sorted(toks))


def lcs_len(a: str, b: str) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def token_sort_ratio(a: str, b: str) -> float:
    """Equivalent to rapidfuzz.fuzz.token_sort_ratio on normalised strings (Indel similarity)."""
    na, nb = norm_name(a), norm_name(b)
    if not na and not nb:
        return 100.0
    return 100.0 * 2 * lcs_len(na, nb) / (len(na) + len(nb))


def _pick_group(rng: random.Random, lang: str) -> str:
    groups, weights = zip(*GROUP_WEIGHTS[lang])
    return rng.choices(groups, weights)[0]


def make_person_name(rng: random.Random, lang: str) -> dict:
    """Return {family_name, given_names, group}; family UPPERCASE, given Title-case."""
    group = _pick_group(rng, lang)
    pool = FAMILY[group]
    fam = rng.choice(pool)
    if rng.random() < 0.30:  # compound family name (two tokens, or hyphenated for north/anglo)
        other = rng.choice(pool)
        if other != fam:
            fam = f"{fam}-{other}" if (group in ("anglo", "nord") and rng.random() < 0.4) else f"{fam} {other}"
    gpool = GIVEN_EN if (group == "anglo" or (lang == "en" and rng.random() < 0.7)) else GIVEN_FR
    r = rng.random()
    if gpool is GIVEN_FR and r < 0.22:
        given = rng.choice(GIVEN_FR_COMPOUND)
    else:
        given = rng.choice(gpool)
        if r > 0.55:
            second = rng.choice(gpool)
            if second != given:
                given = f"{given} {second}"
    if rng.random() < 0.06:  # a third name
        extra = rng.choice(gpool)
        if extra not in given:
            given = f"{given} {extra}"
    return {"family_name": fam, "given_names": given, "group": group}


def birth_place_for(rng: random.Random, group: str) -> str:
    return rng.choice(BIRTH_PLACE_GROUP.get(group, BIRTH_PLACES))


def make_hard_name(rng: random.Random, lang: str) -> dict:
    if lang == "en":
        return {"family_name": rng.choice(HARD_FAMILY_EN), "given_names": rng.choice(HARD_GIVEN_EN), "group": "anglo"}
    return {"family_name": rng.choice(HARD_FAMILY_FR), "given_names": rng.choice(HARD_GIVEN_FR), "group": "ouest"}


def render_name(family: str, given: str, order: str) -> str:
    """order: 'FG' -> 'FAMILY Given', 'GF' -> 'Given FAMILY'."""
    return f"{family} {given}" if order == "FG" else f"{given} {family}"


def ocr_variant(family: str, given: str) -> tuple[str, str]:
    """Plausible orthographic variant of a name (as found on a diploma vs a PV).

    Drops the last letter of each family token and the last given name, and removes
    diacritics.  The caller verifies the resulting similarity falls in the ambiguous band.
    """
    ftoks = re.split(r"([ -])", family)
    new_f = "".join(t[:-1] if (i % 2 == 0 and len(t) > 5) else t for i, t in enumerate(ftoks))
    gtoks = re.split(r"[ ]", given)
    new_g = " ".join(gtoks[:-1]) if len(gtoks) > 1 else given[:-1] if len(given) > 6 else given
    return strip_accents(new_f), strip_accents(new_g)
