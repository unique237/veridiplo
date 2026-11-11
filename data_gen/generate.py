"""Synthetic data generator for VeriDiplo.

    python -m data_gen.generate --out data/synthetic

Everything is fictional and seeded (SEED env, default 42).  See data_gen/README.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from . import names as N
from . import registry as REG
from .noise import degrade_to_png, scan_pdf
from .render import render_diploma, render_list, render_pv

SEED_DEFAULT = 42
YEARS = list(range(2015, 2026))
MISSING_YEAR = 1998
MENTIONS = {"fr": ["Passable", "Assez Bien", "Bien", "Très Bien"], "en": ["Pass", "Fairly Good", "Good", "Very Good"]}
BAND_LOW = [10.0, 12.0, 14.0, 16.0]  # lower bound of each mention band (upper bound exclusive, last = 20)
DEC_ADMIT = {"fr": "Admis", "en": "Admitted"}
DEC_FAIL = {"fr": "Ajourné", "en": "Failed"}
SIZE_SCALE = 0.795  # tunes the total number of graduates to ~600
XLSX_REGISTRIES = {("UFN", 2020), ("FUM", 2022), ("IPFM", 2024)}
SEMICOLON_REGISTRY_INSTS = {"ISFG"}


# ======================================================================================
# 1. Static fictional world: institutions, programmes, accreditations, signatories
# ======================================================================================
def _acc(frm, to, ref):
    return {"from": frm, "to": to, "decree_ref": ref}


def build_institutions() -> list[dict]:
    """Return the fictional institutions with programmes and signatories (internal superset)."""
    I = []
    I.append(dict(
        id="UFN", initials="UFN", name_fr="Université Fictive de Nkolbisson", name_en="Fictive University of Nkolbisson",
        kind="public", language="fr", city="Yaoundé", delib=(7, 5, 20),
        serial_format=r"^UFN/\d{4}/[A-Z]{3,7}/\d{5}$", serial_pattern="UFN/{YYYY}/{PROG}/{NNNNN}", serial_example="UFN/2019/LINFO/00123",
        programmes=[
            dict(id="LDROIT", name_fr="Licence en Droit Privé", name_en="Bachelor in Private Law", level="Licence", language="fr",
                 size=15, first=2015, dur=3, letter="A", accr=[_acc("1992-10-01", None, "ARR-FICTIF-0114/1992")]),
            dict(id="LINFO", name_fr="Licence en Informatique", name_en="Bachelor in Computer Science", level="Licence", language="fr",
                 size=5, first=2015, dur=3, letter="B",
                 accr=[_acc("2004-09-01", "2018-08-31", "ARR-FICTIF-0388/2004"), _acc("2019-09-01", None, "ARR-FICTIF-1021/2019")]),
            dict(id="MINFO", name_fr="Master en Informatique", name_en="Master in Computer Science", level="Master", language="fr",
                 size=2.5, first=2015, dur=2, letter="C", accr=[_acc("2012-09-01", None, "ARR-FICTIF-0507/2012")]),
            dict(id="MDATA", name_fr="Master en Science des Données", name_en="Master in Data Science", level="Master", language="fr",
                 size=3, first=2020, dur=2, letter="D", accr=[_acc("2021-09-01", None, "ARR-FICTIF-1377/2021")]),
        ],
        signatories=[("Hyacinthe Roger MBOLO ESSIMI", "Recteur", "1994-01-01", "2009-12-31"),
                     ("Charlotte NGO BATJOM", "Rectrice", "2010-01-01", "2016-12-31"),
                     ("Alphonse MVONDO BELINGA", "Recteur", "2017-01-01", "2022-09-30"),
                     ("Joséphine ABENA MANGA", "Rectrice", "2022-10-01", None)],
    ))
    I.append(dict(
        id="UFB", initials="UFB", name_fr="Université Fictive de Bonabéri", name_en="Fictive University of Bonaberi",
        kind="public", language="fr", city="Douala", delib=(7, 8, 25),
        serial_format=r"^UFB\.\d{4}\.\d{6}$", serial_pattern="UFB.{YYYY}.{NNNNNN}", serial_example="UFB.2019.004512",
        programmes=[
            dict(id="LGEST", name_fr="Licence en Gestion", name_en="Bachelor in Management", level="Licence", language="fr",
                 size=6, first=2015, dur=3, letter="A", accr=[_acc("2000-09-01", None, "ARR-FICTIF-0201/2000")]),
            dict(id="LCOMPT", name_fr="Licence en Comptabilité et Finance", name_en="Bachelor in Accounting and Finance", level="Licence", language="fr",
                 size=4, first=2015, dur=3, letter="B", accr=[_acc("2008-09-01", None, "ARR-FICTIF-0455/2008")]),
            dict(id="MMARK", name_fr="Master en Marketing Digital", name_en="Master in Digital Marketing", level="Master", language="fr",
                 size=2, first=2017, dur=2, letter="C", accr=[_acc("2016-09-01", None, "ARR-FICTIF-0902/2016")]),
            dict(id="LLOG", name_fr="Licence en Logistique et Transport", name_en="Bachelor in Logistics and Transport", level="Licence", language="fr",
                 size=3, first=2017, dur=3, letter="D", accr=[_acc("2019-09-01", None, "ARR-FICTIF-1033/2019")]),
        ],
        signatories=[("Étienne Blaise MOUKOKO EPEE", "Recteur", "2000-03-01", "2011-02-28"),
                     ("Marguerite DIBOUE LOBE", "Rectrice", "2011-03-01", "2018-06-30"),
                     ("Samuel Aimé TCHINDA KAMGA", "Recteur", "2018-07-01", "2023-12-31"),
                     ("Prisca ELLA NKODO", "Rectrice", "2024-01-01", None)],
    ))
    I.append(dict(
        id="FUM", initials="FUM", name_fr="Université Fictive de Mbengwi", name_en="Fictional University of Mbengwi",
        kind="public", language="en", city="Bamenda", delib=(8, 4, 22),
        serial_format=r"^FUM-\d{4}-[A-Z]{3,7}-\d{4}$", serial_pattern="FUM-{YYYY}-{PROG}-{NNNN}", serial_example="FUM-2019-BSCOMP-0042",
        programmes=[
            dict(id="BAENG", name_fr="Licence en Études Anglaises", name_en="Bachelor of Arts in English Studies", level="Bachelor", language="en",
                 size=5, first=2015, dur=3, letter="A", accr=[_acc("2003-09-01", None, "ARR-FICTIF-0277/2003")]),
            dict(id="BSCOMP", name_fr="Licence en Informatique", name_en="Bachelor of Science in Computer Science", level="Bachelor", language="en",
                 size=3.5, first=2015, dur=3, letter="B",
                 accr=[_acc("2006-09-01", "2018-08-31", "ARR-FICTIF-0391/2006"), _acc("2019-09-01", None, "ARR-FICTIF-1040/2019")]),
            dict(id="MSEDU", name_fr="Master en Sciences de l'Éducation", name_en="Master of Science in Education", level="Master", language="en",
                 size=2, first=2015, dur=2, letter="C", accr=[_acc("2011-09-01", None, "ARR-FICTIF-0560/2011")]),
            dict(id="BSAGRB", name_fr="Licence en Agrobusiness", name_en="Bachelor of Science in Agribusiness", level="Bachelor", language="en",
                 size=2.5, first=2015, dur=3, letter="D", accr=[_acc("2013-09-01", None, "ARR-FICTIF-0689/2013")]),
        ],
        signatories=[("Nelson Fonyuy TABE ASONG", "Vice-Chancellor", "2003-09-01", "2012-08-31"),
                     ("Agnes NGWA MBAH", "Vice-Chancellor", "2012-09-01", "2019-08-31"),
                     ("Godlove ATEM TANYI", "Vice-Chancellor", "2019-09-01", "2024-05-31"),
                     ("Cynthia NFOR EKEMA", "Vice-Chancellor", "2024-06-01", None)],
    ))
    I.append(dict(
        id="ISFG", initials="ISFG", name_fr="Institut Supérieur Fictif de Garoua", name_en="Fictive Higher Institute of Garoua",
        kind="public", language="fr", city="Garoua", delib=(7, 1, 14),
        serial_format=r"^ISFG\d{4}[A-Z]{3,7}\d{4}$", serial_pattern="ISFG{YYYY}{PROG}{NNNN}", serial_example="ISFG2019BTSGC0017",
        programmes=[
            dict(id="BTSGC", name_fr="BTS Génie Civil", name_en="HND Civil Engineering", level="BTS", language="fr",
                 size=2.5, first=2015, dur=2, letter="A", accr=[_acc("2009-09-01", None, "ARR-FICTIF-0310/2009")]),
            dict(id="BTSIG", name_fr="BTS Informatique de Gestion", name_en="HND Business Computing", level="BTS", language="fr",
                 size=3, first=2015, dur=2, letter="B", accr=[_acc("2010-09-01", None, "ARR-FICTIF-0344/2010")]),
            dict(id="LPELEC", name_fr="Licence Professionnelle en Électrotechnique", name_en="Professional Bachelor in Electrical Engineering",
                 level="Licence", language="fr", size=2, first=2016, dur=3, letter="C", accr=[_acc("2014-09-01", None, "ARR-FICTIF-0793/2014")]),
            dict(id="BTSAGR", name_fr="BTS Production Animale", name_en="HND Animal Production", level="BTS", language="fr",
                 size=2, first=2015, dur=2, letter="D",
                 accr=[_acc("2012-09-01", "2020-08-31", "ARR-FICTIF-0612/2012"), _acc("2021-09-01", None, "ARR-FICTIF-1301/2021")]),
        ],
        signatories=[("Hamadou Alioum BELLO", "Directeur", "2009-01-01", "2015-06-30"),
                     ("Zénabou ISSA ADAMOU", "Directrice", "2015-07-01", "2020-12-31"),
                     ("Mahamat DAIROU YAYA", "Directeur", "2021-01-01", None)],
    ))
    I.append(dict(
        id="IPFM", initials="IPFM", name_fr="Institut Privé Fictif de Management de Bonanjo", name_en="Fictive Private Institute of Management of Bonanjo",
        kind="private", language="bilingual", city="Douala", delib=(6, 18, 30),
        serial_format=r"^IPFM/\d{4}/\d{5}$", serial_pattern="IPFM/{YYYY}/{NNNNN}", serial_example="IPFM/2019/00231",
        programmes=[
            dict(id="LMANAG", name_fr="Licence en Management", name_en="Bachelor in Management", level="Licence", language="fr",
                 size=3, first=2015, dur=3, letter="A", accr=[_acc("2011-09-01", None, "ARR-FICTIF-0577/2011")]),
            dict(id="BBANK", name_fr="Licence en Banque et Finance", name_en="Bachelor in Banking and Finance", level="Bachelor", language="en",
                 size=2.5, first=2015, dur=3, letter="B", accr=[_acc("2014-09-01", None, "ARR-FICTIF-0801/2014")]),
            dict(id="MMBA", name_fr="Master en Administration des Affaires", name_en="Master of Business Administration", level="Master", language="en",
                 size=2, first=2018, dur=2, letter="C", accr=[_acc("2016-09-01", None, "ARR-FICTIF-0944/2016")]),
            dict(id="MRH", name_fr="Master en Ressources Humaines", name_en="Master in Human Resources", level="Master", language="fr",
                 size=2, first=2019, dur=2, letter="D",
                 accr=[_acc("2018-09-01", "2023-08-31", "ARR-FICTIF-1004/2018"), _acc("2024-09-01", None, "ARR-FICTIF-1502/2024")]),
        ],
        signatories=[("Régine Estelle NJOH SONE", "Directrice Générale", "2011-01-01", "2017-12-31"),
                     ("Patrick OWONA ETEKI", "Directeur Général", "2018-01-01", "2022-03-31"),
                     ("Larissa KUATE FOKOU", "Directrice Générale", "2022-04-01", None)],
    ))
    for inst in I:
        for i, s in enumerate(inst["signatories"]):
            inst["signatories"][i] = dict(id=f"{inst['id']}-SG{i + 1}", name=s[0], title=s[1], in_office_from=s[2], in_office_to=s[3])
    return I


# ======================================================================================
# helpers
# ======================================================================================
def d2s(d: date) -> str:
    return d.isoformat()


def s2d(s: str) -> date:
    return date.fromisoformat(s)


def next_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def in_period(day: str, frm: str, to: str | None) -> bool:
    return frm <= day and (to is None or day <= to)


def accredited_at(prog: dict, day: str) -> bool:
    return any(in_period(day, a["from"], a["to"]) for a in prog["accr"])


def signatory_at(inst: dict, day: str) -> dict | None:
    for s in inst["signatories"]:
        if in_period(day, s["in_office_from"], s["in_office_to"]):
            return s
    return None


def mention_idx(avg: float) -> int:
    return max(i for i, lo in enumerate(BAND_LOW) if avg >= lo)


def fmt_avg(a: float) -> str:
    return f"{a:.2f}"


def make_serial(inst_id: str, prog_id: str, year: int, seq: int) -> str:
    if inst_id == "UFN":
        return f"UFN/{year}/{prog_id}/{seq:05d}"
    if inst_id == "UFB":
        return f"UFB.{year}.{seq:06d}"
    if inst_id == "FUM":
        return f"FUM-{year}-{prog_id}-{seq:04d}"
    if inst_id == "ISFG":
        return f"ISFG{year}{prog_id}{seq:04d}"
    if inst_id == "IPFM":
        return f"IPFM/{year}/{seq:05d}"
    raise ValueError(inst_id)


def serial_bucket(inst_id: str, prog_id: str, year: int) -> tuple:
    """Serial sequences are numbered per bucket (a bucket is what the serial format encodes)."""
    if inst_id in ("UFN", "FUM", "ISFG"):
        return (inst_id, prog_id, year)
    return (inst_id, year)


def make_matricule(inst_id: str, yy: int, letter: str, n: int) -> str:
    return {
        "UFN": f"{yy:02d}{letter}{n:04d}", "UFB": f"UB{yy:02d}{letter}{n:04d}", "FUM": f"FUM{yy:02d}{letter}{n:04d}",
        "ISFG": f"{yy:02d}IS{letter}{n:03d}", "IPFM": f"IP-{yy:02d}-{n:05d}",
    }[inst_id]


class IdPool:
    """Unique matricule allocator."""

    def __init__(self):
        self.used = set()

    def alloc(self, rng: random.Random, inst_id: str, yy: int, letter: str) -> str:
        for _ in range(10000):
            n = rng.randint(1, 4999 if inst_id != "ISFG" else 999)
            m = make_matricule(inst_id, yy, letter, n)
            if m not in self.used:
                self.used.add(m)
                return m
        raise RuntimeError("matricule space exhausted")


# ======================================================================================
# 2. Graduates
# ======================================================================================
def gen_graduates(insts: list[dict], seed: int) -> tuple[list[dict], dict]:
    """Return (graduates, pv_extra) where pv_extra[(inst, prog, year)] = failed candidates (PV only)."""
    rng = random.Random(f"{seed}:graduates")
    ids = IdPool()
    seq_state: dict[tuple, int] = {}
    seen_names: set[str] = set()
    seen_key2: set[str] = set()
    grads: list[dict] = []
    pv_extra: dict[tuple, list[dict]] = {}
    pcount = 0

    def unique_name(lang: str) -> dict:
        for _ in range(200):
            nm = N.make_person_name(rng, lang)
            full = N.norm_name(f"{nm['family_name']} {nm['given_names']}")
            key2 = N.norm_name(nm["family_name"]) + "|" + N.strip_accents(nm["given_names"].split()[0]).upper()
            if full not in seen_names and key2 not in seen_key2:
                seen_names.add(full)
                seen_key2.add(key2)
                return nm
        raise RuntimeError("no unique name")

    def next_serial(inst_id, prog_id, year):
        b = serial_bucket(inst_id, prog_id, year)
        if b not in seq_state:
            seq_state[b] = rng.randint(0, 150)
        seq_state[b] += 1
        return make_serial(inst_id, prog_id, year, seq_state[b])

    for year in YEARS + [MISSING_YEAR]:
        for inst in insts:
            for prog in inst["programmes"]:
                if year == MISSING_YEAR:
                    if not (inst["id"] == "UFN" and prog["id"] == "LDROIT"):
                        continue
                    n = 11
                else:
                    if year < prog["first"]:
                        continue
                    n = max(1, int(round(rng.gauss(prog["size"] * SIZE_SCALE, prog["size"] * SIZE_SCALE * 0.3))))
                m_, d_lo, d_hi = inst["delib"]
                delib = next_weekday(date(year, m_, rng.randint(d_lo, d_hi)))
                delib_s = d2s(delib)
                lang = prog["language"]
                acc_ok = accredited_at(prog, delib_s)
                level_ages = {"Licence": (21, 26), "Bachelor": (21, 26), "BTS": (20, 25), "Master": (23, 29)}[prog["level"]]
                cohort = []
                for _ in range(n):
                    nm = unique_name(lang)
                    age = rng.randint(*level_ages)
                    by = year - age
                    bd = date(by, rng.randint(1, 12), rng.randint(1, 28))
                    mu = 12.5 if prog["level"] == "Master" else 12.2
                    avg = round(min(18.9, max(10.0, rng.gauss(mu, 1.5))), 2)
                    mi = mention_idx(avg)
                    issue = next_weekday(delib + timedelta(days=rng.randint(40, 220)))
                    sg = signatory_at(inst, d2s(issue))
                    pcount += 1
                    g = {
                        "person_id": f"P{pcount:04d}",
                        "family_name": nm["family_name"], "given_names": nm["given_names"],
                        "birth_date": d2s(bd), "birth_place": N.birth_place_for(rng, nm["group"]),
                        "institution_id": inst["id"], "programme_id": prog["id"], "session_year": year,
                        "deliberation_date": delib_s, "average": avg, "mention": MENTIONS[lang][mi],
                        "decision": "admitted",
                        "matricule": ids.alloc(rng, inst["id"], (year - prog["dur"]) % 100, prog["letter"]),
                        "diploma_serial": None, "signatory_id": sg["id"], "issue_date": d2s(issue),
                        "language": lang, "accredited_at_session": acc_ok,
                        "archive_available": year != MISSING_YEAR,
                    }
                    cohort.append(g)
                rng.shuffle(cohort)
                for g in cohort:
                    g["diploma_serial"] = next_serial(inst["id"], prog["id"], year)
                grads.extend(cohort)
                # failed candidates appearing only on the PV
                if year != MISSING_YEAR:
                    ratio = 1.3 if prog["id"] == "LDROIT" else rng.uniform(0.3, 0.8)
                    extra = []
                    for _ in range(max(1, int(round(n * ratio)))):
                        nm = unique_name(lang)
                        extra.append({
                            "family_name": nm["family_name"], "given_names": nm["given_names"],
                            "matricule": ids.alloc(rng, inst["id"], (year - prog["dur"]) % 100, prog["letter"]),
                            "average": round(rng.uniform(5.0, 9.95), 2), "decision": DEC_FAIL[lang],
                        })
                    pv_extra[(inst["id"], prog["id"], year)] = extra
    return grads, pv_extra


# ======================================================================================
# 3. PV specs
# ======================================================================================
def pv_key(inst_id: str, prog_id: str, year: int) -> str:
    return f"{inst_id}_{prog_id}_{year}"


def build_pv_specs(insts, grads, pv_extra, seed, forced_orders: dict, hard_persons: set, special_pv_presets: dict):
    """Return list of PV specs (incl. row layout data) for sessions with an archive."""
    rng = random.Random(f"{seed}:pv")
    inst_by = {i["id"]: i for i in insts}
    by_pv: dict[tuple, list[dict]] = {}
    for g in grads:
        if g["archive_available"]:
            by_pv.setdefault((g["institution_id"], g["programme_id"], g["session_year"]), []).append(g)
    specs = []
    for (iid, pid, year), members in sorted(by_pv.items()):
        inst = inst_by[iid]
        prog = next(p for p in inst["programmes"] if p["id"] == pid)
        lang = prog["language"]
        delib = members[0]["deliberation_date"]
        sg = signatory_at(inst, delib)
        rows = []
        for g in members:
            order = forced_orders.get(g["person_id"]) or ("FG" if rng.random() < 0.88 else "GF")
            rows.append(dict(sort=N.norm_name(g["family_name"]), person_id=g["person_id"], matricule=g["matricule"],
                             name=N.render_name(g["family_name"], g["given_names"], order), order=order,
                             average=fmt_avg(g["average"]), mention=g["mention"], decision=DEC_ADMIT[lang]))
        for e in pv_extra.get((iid, pid, year), []):
            order = "FG" if rng.random() < 0.88 else "GF"
            rows.append(dict(sort=N.norm_name(e["family_name"]), person_id=None, matricule=e["matricule"],
                             name=N.render_name(e["family_name"], e["given_names"], order), order=order,
                             average=fmt_avg(e["average"]), mention="-", decision=e["decision"]))
        rows.sort(key=lambda r: r["sort"])
        for i, r in enumerate(rows, 1):
            r["n"] = i
        jp = N.make_person_name(random.Random(f"{seed}:jury:{iid}:{pid}:{year}"), "en" if lang == "en" else "fr")
        president = f"Pr {jp['given_names'].split()[0]} {jp['family_name']}"
        key = pv_key(iid, pid, year)
        preset = special_pv_presets.get(key) or ("poor" if rng.random() < 0.06 else "scan")
        if any(r["person_id"] in hard_persons for r in rows):
            preset = "poor"
        dpi = rng.choices([200, 250, 300], [0.7, 0.2, 0.1])[0]
        specs.append(dict(
            key=key, institution={k: inst[k] for k in ("id", "name_fr", "name_en", "city", "language", "initials")},
            programme={k: prog[k] for k in ("id", "name_fr", "name_en", "level")}, session_year=year,
            academic_year=f"{year - 1}/{year}", delib_date=delib, jury_president=president,
            signatory={"name": sg["name"], "title": sg["title"]}, lang=lang, rows=rows,
            preset=preset, dpi=dpi,
        ))
    return specs


def _pv_job(args):
    spec, pv_dir, seed = args
    clean = os.path.join(pv_dir, f"{spec['key']}.clean.pdf")
    final = os.path.join(pv_dir, f"{spec['key']}.pdf")
    layout = render_pv(spec, clean)
    params = scan_pdf(clean, final, seed, f"pv:{spec['key']}", dpi=spec["dpi"], preset=spec["preset"])
    return spec["key"], layout, params


def _diploma_job(args):
    spec, out_dir, tmp_dir, seed = args
    pdf = os.path.join(tmp_dir, f"{spec['case_id']}.pdf")
    render_diploma(spec, pdf)
    params = degrade_to_png(pdf, os.path.join(out_dir, f"{spec['case_id']}.png"), seed, f"dip:{spec['case_id']}")
    os.remove(pdf)
    return spec["case_id"], params


# ======================================================================================
# 4. Diploma cases (genuine + tampered)
# ======================================================================================
class CasePicker:
    def __init__(self, grads, insts, pv_extra, rng):
        self.grads, self.insts, self.rng = grads, insts, rng
        self.inst_by = {i["id"]: i for i in insts}
        self.prog_by = {p["id"]: p for i in insts for p in i["programmes"]}
        self.used: set[str] = set()   # person ids reserved
        self.pv_extra = pv_extra

    def pool(self, pred=None, accredited=True, archive=True):
        out = []
        for g in self.grads:
            if g["person_id"] in self.used:
                continue
            if archive and not g["archive_available"]:
                continue
            if accredited and not g["accredited_at_session"]:
                continue
            if not accredited and g["accredited_at_session"]:
                continue
            if pred and not pred(g):
                continue
            out.append(g)
        return out

    def take(self, pred=None, **kw):
        p = self.pool(pred, **kw)
        if not p:
            raise RuntimeError("no candidate for case")
        g = self.rng.choice(p)
        self.used.add(g["person_id"])
        return g


def build_cases(insts, grads, pv_extra, seed):
    """Select persons and build the typed fields of every diploma. Also mutates graduates for special cases."""
    rng = random.Random(f"{seed}:cases")
    P = CasePicker(grads, insts, pv_extra, rng)
    inst_by, prog_by = P.inst_by, P.prog_by
    cases: list[dict] = []
    forced_orders: dict[str, str] = {}
    hard_persons: set[str] = set()
    protected: set[str] = set()
    serial_override: dict[str, str] = {}
    counters: dict[str, int] = {}
    used_serials = {g["diploma_serial"] for g in grads}

    def cid(prefix):
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}-{counters[prefix]:03d}"

    def base_fields(g, case_id, label, tags, order=None):
        order = order or ("FG" if rng.random() < 0.65 else "GF")
        return dict(
            case_id=case_id, scenario_tags=tags, label=label,
            holder_name=N.render_name(g["family_name"], g["given_names"], order),
            birth_date=g["birth_date"], birth_place=g["birth_place"], institution_id=g["institution_id"],
            programme_id=g["programme_id"], session_year=g["session_year"], average=g["average"],
            mention=g["mention"], serial=g["diploma_serial"],
            signatory_name=next(s["name"] for s in inst_by[g["institution_id"]]["signatories"] if s["id"] == g["signatory_id"]),
            issue_date=g["issue_date"], language=g["language"],
        )

    def meta(case, g, verdict, failing, unknown=(), authentic=True, note="", extra=None):
        case["_meta"] = dict(person_id=g["person_id"], expected_verdict=verdict, expected_failing_checks=list(failing),
                             expected_unknown_checks=list(unknown), truth_authentic=authentic, note=note)
        if extra:
            case["_meta"].update(extra)
        protected.add(g["person_id"])
        cases.append(case)

    def forge_serial(inst_id, prog_id, year):
        b = serial_bucket(inst_id, prog_id, year)
        seq = rng.randint(700, 799) if inst_id != "FUM" and inst_id != "ISFG" else rng.randint(700, 799)
        for _ in range(500):
            s = make_serial(inst_id, prog_id, year, seq)
            if s not in used_serials:
                used_serials.add(s)
                return s
            seq += 1
        raise RuntimeError("serial")

    # ---- S6 first (rename persons before anything else uses them): OCR-hard names
    for k, (iid, lang) in enumerate([("UFN", "fr"), ("FUM", "en")], 1):
        g = P.take(lambda g, iid=iid: g["institution_id"] == iid and g["session_year"] >= 2018 and g["language"] == lang)
        hn = N.make_hard_name(rng, lang)
        g["family_name"], g["given_names"] = hn["family_name"], hn["given_names"]
        full = f"{g['family_name']} {g['given_names']}"
        # build a spelling variant whose similarity falls in the ambiguous band [80, 92)
        variants = []
        fam, giv = g["family_name"], g["given_names"]
        for drop_family in (1, 2, 3):
            for drop_given in (True, False):
                ftoks = re.split(r"([ \-'])", fam)
                nf = "".join(t[:-drop_family] if (i % 2 == 0 and len(t) > 5) else t for i, t in enumerate(ftoks))
                gt = giv.split(" ")
                ng = " ".join(gt[:-1]) if (drop_given and len(gt) > 1) else (giv[:-2] if len(giv) > 6 else giv)
                variants.append((N.strip_accents(nf), N.strip_accents(ng)))
        chosen = None
        for nf, ng in variants:
            sc = N.token_sort_ratio(full, f"{nf} {ng}")
            if 81 <= sc <= 91:
                chosen = (nf, ng, sc)
                break
        if chosen is None:  # fallback: closest to 86
            nf, ng = min(variants, key=lambda v: abs(N.token_sort_ratio(full, f"{v[0]} {v[1]}") - 86))
            chosen = (nf, ng, N.token_sort_ratio(full, f"{nf} {ng}"))
        forced_orders[g["person_id"]] = "FG"
        hard_persons.add(g["person_id"])
        f = base_fields(g, cid("OCRH"), "ocr_hard_name", ["S6"] if k == 1 else [])
        f["holder_name"] = f"{chosen[1]} {chosen[0]}"  # reversed order AND spelling variant
        meta(f, g, "INSUFFICIENT_EVIDENCE", [], unknown=["C1"], authentic=True,
             note="Genuine holder; diploma spelling/order differs from the PV (similarity in the 80-92 band): C1 must be 'unknown' -> human review.",
             extra=dict(name_similarity=round(chosen[2], 1), pv_name=full))

    # ---- genuine
    gen_specs = [
        dict(pred=lambda g: g["institution_id"] == "UFN" and g["programme_id"] == "LINFO" and g["session_year"] >= 2021 and g["mention"] in ("Assez Bien", "Bien"), tags=["S1"]),
        dict(pred=lambda g: g["institution_id"] == "UFB" and g["programme_id"] in ("LGEST", "LCOMPT") and g["session_year"] >= 2020, tags=["S4"]),
    ]
    for i in range(17):
        iid = [i_["id"] for i_ in insts][i % 5]
        gen_specs.append(dict(pred=(lambda g, iid=iid: g["institution_id"] == iid and g["programme_id"] not in
                                    {c["programme_id"] for c in cases if c["label"] == "genuine"}), tags=[]))
    for spec in gen_specs:
        try:
            g = P.take(spec["pred"])
        except RuntimeError:
            g = P.take(lambda g: True)
        f = base_fields(g, cid("GEN"), "genuine", spec["tags"])
        meta(f, g, "CONSISTENT", [], note="Genuine specimen.")

    # ---- reversed name order (genuine)
    for k in range(2):
        g = P.take(lambda g: g["institution_id"] in ("UFB", "ISFG", "FUM", "IPFM")[k::2] and g["session_year"] >= 2017)
        forced_orders[g["person_id"]] = "FG"
        f = base_fields(g, cid("REVN"), "reversed_name_order", [], order="GF")
        meta(f, g, "CONSISTENT", [], authentic=True, note="Genuine; name printed given-names-first on the diploma, surname-first on the PV (normalisation must reconcile).")

    # ---- tampered mention (S2, S7, + one EN)
    tm_specs = [
        dict(pred=lambda g: g["institution_id"] == "UFN" and g["mention"] == "Passable" and g["session_year"] >= 2019, raise_avg=True, tags=["S2"]),
        dict(pred=lambda g: g["institution_id"] == "UFB" and g["mention"] == "Assez Bien" and g["session_year"] >= 2018, raise_avg=False, tags=["S7"]),
        dict(pred=lambda g: g["institution_id"] == "FUM" and g["mention"] == "Pass", raise_avg=False, tags=[]),
    ]
    for spec in tm_specs:
        g = P.take(spec["pred"])
        f = base_fields(g, cid("TMEN"), "tampered_mention", spec["tags"])
        lang = g["language"]
        mi = MENTIONS[lang].index(g["mention"])
        new_mi = mi + (2 if (spec["raise_avg"]) else 1)
        f["mention"] = MENTIONS[lang][new_mi]
        if spec["raise_avg"]:
            lo = BAND_LOW[new_mi]
            f["average"] = round(rng.uniform(lo + 0.2, lo + 1.7), 2)
        meta(f, g, "INCONSISTENT", ["C4"], authentic=False,
             note=f"Mention {g['mention']} -> {f['mention']}" + (f", average {g['average']} -> {f['average']} (internally consistent, differs from PV)." if spec["raise_avg"] else " (average unchanged, so mention is also inconsistent with own average band)."),
             extra=dict(pv_mention=g["mention"], pv_average=g["average"]))

    # ---- tampered average (same band)
    for k in range(2):
        def room(g):
            mi = mention_idx(g["average"])
            lo, hi = BAND_LOW[mi], (BAND_LOW[mi + 1] - 0.01 if mi < 3 else 19.9)
            return mi < 3 and (hi - g["average"] >= 0.8 or g["average"] - lo >= 0.8)
        g = P.take(lambda g, k=k: room(g) and g["institution_id"] in (("UFN", "ISFG", "IPFM"), ("UFB", "FUM", "IPFM"))[k])
        mi = mention_idx(g["average"])
        lo, hi = BAND_LOW[mi], BAND_LOW[mi + 1] - 0.01
        f = base_fields(g, cid("TAVG"), "tampered_average", [])
        if hi - g["average"] >= 0.8:
            f["average"] = round(rng.uniform(g["average"] + 0.8, hi), 2)
        else:
            f["average"] = round(rng.uniform(lo, g["average"] - 0.8), 2)
        assert mention_idx(f["average"]) == mi
        meta(f, g, "INCONSISTENT", ["C4"], authentic=False,
             note=f"Average {g['average']} -> {f['average']} (mention band unchanged).", extra=dict(pv_average=g["average"]))

    # ---- tampered name
    for k in range(2):
        g = P.take(lambda g, k=k: g["institution_id"] in (("UFN", "UFB", "ISFG"), ("FUM", "IPFM", "UFN"))[k] and g["session_year"] >= 2016)
        lang = g["language"]
        peers = [f"{x['family_name']} {x['given_names']}" for x in grads if x["institution_id"] == g["institution_id"] and x["programme_id"] == g["programme_id"] and x["session_year"] == g["session_year"]]
        peers += [f"{e['family_name']} {e['given_names']}" for e in pv_extra.get((g["institution_id"], g["programme_id"], g["session_year"]), [])]
        allnames = {N.norm_name(f"{x['family_name']} {x['given_names']}") for x in grads}
        for _ in range(500):
            nm = N.make_person_name(rng, lang)
            cand = f"{nm['family_name']} {nm['given_names']}"
            if N.norm_name(cand) in allnames:
                continue
            if max(N.token_sort_ratio(cand, p) for p in peers) < 62:
                break
        f = base_fields(g, cid("TNAM"), "tampered_name", [])
        f["holder_name"] = N.render_name(nm["family_name"], nm["given_names"], "FG" if rng.random() < 0.6 else "GF")
        f["serial"] = forge_serial(g["institution_id"], g["programme_id"], g["session_year"])
        meta(f, g, "INCONSISTENT", ["C1"], authentic=False,
             note="Holder name not present in the PV / registry of the claimed session (all other fields copied from a real entry).",
             extra=dict(original_holder=f"{g['family_name']} {g['given_names']}"))

    # ---- wrong session date
    for k in range(2):
        def has_other(g):
            return any(h["institution_id"] == g["institution_id"] and h["programme_id"] == g["programme_id"] and h["session_year"] != g["session_year"] and abs(h["session_year"] - g["session_year"]) <= 3 and h["accredited_at_session"] and h["archive_available"] for h in grads)
        g = P.take(lambda g, k=k: has_other(g) and g["institution_id"] in (("UFB", "IPFM"), ("FUM", "ISFG"))[k] and 2016 <= g["session_year"] <= 2023)
        others = sorted({h["session_year"] for h in grads if h["institution_id"] == g["institution_id"] and h["programme_id"] == g["programme_id"] and h["session_year"] != g["session_year"] and abs(h["session_year"] - g["session_year"]) <= 3 and h["accredited_at_session"] and h["archive_available"]})
        claimed = rng.choice(others)
        inst = inst_by[g["institution_id"]]
        delib_claim = next(h["deliberation_date"] for h in grads if h["institution_id"] == g["institution_id"] and h["programme_id"] == g["programme_id"] and h["session_year"] == claimed)
        issue = next_weekday(s2d(delib_claim) + timedelta(days=rng.randint(40, 200)))
        sg = signatory_at(inst, d2s(issue))
        f = base_fields(g, cid("WSES"), "wrong_session_date", [])
        f.update(session_year=claimed, issue_date=d2s(issue), signatory_name=sg["name"],
                 serial=forge_serial(g["institution_id"], g["programme_id"], claimed))
        meta(f, g, "INCONSISTENT", ["C5"], authentic=False,
             note=f"Diploma claims session {claimed}; the holder graduated in session {g['session_year']} (PV of the claimed session does not list the holder: C1 also fails if the search is restricted to the claimed session).",
             extra=dict(true_session_year=g["session_year"], also_failing_if_session_scoped=["C1"]))

    # ---- not accredited
    for k in range(2):
        g = P.take(lambda g, k=k: g["programme_id"] in (("LLOG", "MDATA", "LINFO"), ("BSCOMP", "BTSAGR", "MRH"))[k], accredited=False)
        f = base_fields(g, cid("NACC"), "not_accredited", [])
        prog = prog_by[g["programme_id"]]
        meta(f, g, "INCONSISTENT", ["C3"], authentic=False,
             note=f"Programme {g['programme_id']} was not accredited on {g['deliberation_date']} (accreditation periods: {[(a['from'], a['to']) for a in prog['accr']]}). PV and registry entries exist.")

    # ---- signatory out of office
    for k in range(2):
        g = P.take(lambda g, k=k: g["institution_id"] in (("UFN", "FUM"), ("UFB", "ISFG"))[k] and g["session_year"] >= 2016)
        inst = inst_by[g["institution_id"]]
        others = [s for s in inst["signatories"] if s["id"] != g["signatory_id"] and not in_period(g["issue_date"], s["in_office_from"], s["in_office_to"])]
        sg = rng.choice(others)
        f = base_fields(g, cid("SOOO"), "signatory_out_of_office", [])
        f["signatory_name"] = sg["name"]
        meta(f, g, "INCONSISTENT", ["C6"], authentic=False,
             note=f"Signed by {sg['name']} (in office {sg['in_office_from']} - {sg['in_office_to']}) but issue date is {g['issue_date']}.")

    # ---- duplicate serial
    for k in range(2):
        for _ in range(100):
            cand = P.pool(lambda g, k=k: g["institution_id"] in (("UFN", "UFB"), ("FUM", "IPFM"))[k] and g["session_year"] >= 2017)
            gx = rng.choice(cand)
            mates = [h for h in grads if h["person_id"] != gx["person_id"] and h["person_id"] not in P.used and h["institution_id"] == gx["institution_id"]
                     and h["programme_id"] == gx["programme_id"] and h["session_year"] == gx["session_year"] and h["accredited_at_session"]]
            if mates:
                break
        gy = rng.choice(mates)
        P.used.update([gx["person_id"], gy["person_id"]])
        protected.add(gy["person_id"])
        f = base_fields(gx, cid("DUPS"), "duplicate_serial", [])
        f["serial"] = gy["diploma_serial"]
        serial_override[gx["person_id"]] = gy["diploma_serial"]
        meta(f, gx, "INCONSISTENT", ["C7"], authentic=False,
             note=f"Serial {gy['diploma_serial']} appears twice in the registry (holders {gx['person_id']} and {gy['person_id']}).",
             extra=dict(duplicate_with_person_id=gy["person_id"], true_serial=gx["diploma_serial"]))

    return cases, dict(forced_orders=forced_orders, hard_persons=hard_persons, protected=protected,
                       serial_override=serial_override, used_serials=used_serials, picker=P, rng=rng,
                       base_fields=base_fields, meta=meta, cid=cid)


def build_missing_archive_cases(grads, cases, ctx, insts):
    """1998 UFN LDROIT graduates (no PV, no registry). First one gets a look-alike in a later PV (S5)."""
    rng = ctx["rng"]
    inst = next(i for i in insts if i["id"] == "UFN")
    g98 = [g for g in grads if g["session_year"] == MISSING_YEAR]
    lookalike = rng.choice([g for g in grads if g["institution_id"] == "UFN" and 2016 <= g["session_year"] <= 2018 and g["accredited_at_session"]
                            and g["person_id"] not in ctx["picker"].used])
    ctx["picker"].used.add(lookalike["person_id"])
    first = g98[0]
    # shape the 1998 holder's name: same surname, same first given name, different extra given name
    g0 = lookalike["given_names"].split(" ")[0]
    for extra in ["Josiane", "Armand", "Aimé", "Cyrille", "Évelyne", "Hugues", "Inès", "Brigitte", "Landry", "Diane"]:
        cand = f"{lookalike['family_name']} {g0} {extra}"
        sc = N.token_sort_ratio(cand, f"{lookalike['family_name']} {lookalike['given_names']}")
        if 80 <= sc < 92 and extra not in lookalike["given_names"]:
            break
    first["family_name"], first["given_names"] = lookalike["family_name"], f"{g0} {extra}"
    first["birth_place"] = lookalike["birth_place"]
    sims = []
    for k, g in enumerate(g98[:2]):
        f = ctx["base_fields"](g, ctx["cid"]("MISS"), "missing_archive", ["S5"] if k == 0 else [], order="FG")
        extra_meta = dict(note="UFN LDROIT session 1998: neither the PV nor the registry exist in the archive (deliberately missing) -> INSUFFICIENT_EVIDENCE.")
        if k == 0:
            extra_meta.update(lookalike_person_id=lookalike["person_id"],
                              lookalike_name=f"{lookalike['family_name']} {lookalike['given_names']}",
                              lookalike_similarity=round(N.token_sort_ratio(f["holder_name"], f"{lookalike['family_name']} {lookalike['given_names']}"), 1),
                              lookalike_pv=pv_key("UFN", lookalike["programme_id"], lookalike["session_year"]))
        ctx["meta"](f, g, "INSUFFICIENT_EVIDENCE", [], unknown=["C1"], authentic=True, note=extra_meta.pop("note"), extra=extra_meta)
    return lookalike


# ======================================================================================
# main
# ======================================================================================
def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="VeriDiplo synthetic data generator")
    default_out = Path(os.environ.get("DATA_DIR", "data")) / "synthetic"
    ap.add_argument("--out", default=str(default_out))
    ap.add_argument("--seed", type=int, default=int(os.environ.get("SEED", SEED_DEFAULT)))
    ap.add_argument("--workers", type=int, default=int(os.environ.get("DATAGEN_WORKERS", min(4, os.cpu_count() or 1))))
    ap.add_argument("--no-render", action="store_true", help="skip PDF/PNG rendering (JSON/CSV only)")
    ap.add_argument("--no-sample", action="store_true")
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument("--no-scenarios", action="store_true")
    ap.add_argument("--scenarios-dir", default=None)
    ap.add_argument("--sample-dir", default=None)
    args = ap.parse_args(argv)
    t0 = time.time()
    seed = args.seed
    out = Path(args.out)
    for sub in ("pv", "registries", "diplomas", "lists"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    insts = build_institutions()
    grads, pv_extra = gen_graduates(insts, seed)
    cases, ctx = build_cases(insts, grads, pv_extra, seed)
    lookalike = build_missing_archive_cases(grads, cases, ctx, insts)
    prog_by = {p["id"]: p for i in insts for p in i["programmes"]}
    inst_by = {i["id"]: i for i in insts}

    # ---- PV specs (decides names rendered) ------------------------------------------------
    specs = build_pv_specs(insts, grads, pv_extra, seed, ctx["forced_orders"], ctx["hard_persons"], {})
    spec_by_key = {s["key"]: s for s in specs}
    for g in grads:  # trace each graduate to its PV row
        g["pv_file"] = None
        g["pv_row"] = None
        if g["archive_available"]:
            key = pv_key(g["institution_id"], g["programme_id"], g["session_year"])
            g["pv_file"] = f"pv/{key}.pdf"
            for r in spec_by_key[key]["rows"]:
                if r["person_id"] == g["person_id"]:
                    g["pv_row"] = r["n"]

    # ---- institutions.json ---------------------------------------------------------------
    inst_json = []
    for i in insts:
        inst_json.append(dict(
            id=i["id"], name_fr=i["name_fr"], name_en=i["name_en"], kind=i["kind"], language=i["language"], city=i["city"],
            programmes=[dict(id=p["id"], name_fr=p["name_fr"], name_en=p["name_en"], level=p["level"], language=p["language"],
                             accreditations=p["accr"]) for p in i["programmes"]],
            signatories=i["signatories"], serial_format=i["serial_format"], serial_pattern=i["serial_pattern"], serial_example=i["serial_example"]))
    write_json(out / "institutions.json", inst_json)

    # ---- diploma fields finalisation -------------------------------------------------------
    grad_by_id = {g["person_id"]: g for g in grads}
    diploma_specs = []
    for c in cases:
        g = grad_by_id[c["_meta"]["person_id"]]
        inst = inst_by[c["institution_id"]]
        prog = prog_by[c["programme_id"]]
        sg = next(s for s in inst["signatories"] if s["name"] == c["signatory_name"])
        delib = next(h["deliberation_date"] for h in grads if h["institution_id"] == c["institution_id"] and h["programme_id"] == c["programme_id"] and h["session_year"] == c["session_year"])
        spec = dict(c)
        spec.pop("_meta")
        spec.update(session_month=int(delib[5:7]),
                    institution={k: inst[k] for k in ("id", "name_fr", "name_en", "city", "initials")},
                    programme={k: prog[k] for k in ("id", "name_fr", "name_en", "level")},
                    signatory={"name": sg["name"], "title": sg["title"]})
        diploma_specs.append(spec)

    # ---- registries -----------------------------------------------------------------------
    rrng = random.Random(f"{seed}:registry")
    styles = {"UFN": "dmy_slash", "UFB": "iso", "FUM": "long_en", "ISFG": "dmy_dot", "IPFM": "dmy_dash"}
    registry_files = []
    n_reg_rows = 0
    for inst in insts:
        for year in YEARS:
            members = [g for g in grads if g["institution_id"] == inst["id"] and g["session_year"] == year]
            if not members:
                continue
            members = sorted(members, key=lambda g: g["matricule"])
            rows = REG.build_rows(members, prog_by, styles[inst["id"]], inst["id"] == "ISFG", rrng,
                                  ctx["protected"], ctx["serial_override"])
            n_reg_rows += len(rows)
            if (inst["id"], year) in XLSX_REGISTRIES:
                p = out / "registries" / f"{inst['id']}_{year}.xlsx"
                REG.write_xlsx(rows, str(p), f"Registre des diplômés - {inst['name_fr']} - session {year}")
                fmt = "xlsx"
            else:
                p = out / "registries" / f"{inst['id']}_{year}.csv"
                delim = ";" if inst["id"] in SEMICOLON_REGISTRY_INSTS else ","
                REG.write_csv(rows, str(p), delim)
                fmt = "csv"
            registry_files.append(dict(file=f"registries/{p.name}", institution_id=inst["id"], year=year, format=fmt, rows=len(rows),
                                       delimiter=(";" if (fmt == "csv" and inst["id"] in SEMICOLON_REGISTRY_INSTS) else ("," if fmt == "csv" else None)),
                                       header_row=4 if fmt == "xlsx" else 1))

    # ---- JSON ground truth ---------------------------------------------------------------
    write_json(out / "graduates.json", grads)
    write_json(out / "pv_index.json", {s["key"]: dict(
        institution_id=s["institution"]["id"], programme_id=s["programme"]["id"], session_year=s["session_year"],
        deliberation_date=s["delib_date"], lang=s["lang"], jury_president=s["jury_president"], signatory=s["signatory"],
        scan_preset=s["preset"], scan_dpi=s["dpi"],
        rows=[{k: r[k] for k in ("n", "person_id", "matricule", "name", "average", "mention", "decision")} for r in s["rows"]])
        for s in specs})

    # ---- rendering -------------------------------------------------------------------------
    pv_layouts, scan_params, dip_params = {}, {}, {}
    list_count = 0
    if not args.no_render:
        pv_dir = str(out / "pv")
        jobs = [(s, pv_dir, seed) for s in specs]
        if args.workers > 1:
            with ProcessPoolExecutor(max_workers=args.workers) as ex:
                results = list(ex.map(_pv_job, jobs, chunksize=4))
        else:
            results = [_pv_job(j) for j in jobs]
        for key, layout, params in results:
            pv_layouts[key] = layout
            scan_params[key] = params
        # layout rows into pv_index (bbox of each row in the clean PDF)
        idx = json.loads((out / "pv_index.json").read_text(encoding="utf-8"))
        for key, layout in pv_layouts.items():
            by_n = {r["n"]: r for r in layout["rows"]}
            idx[key]["pages"] = layout["pages"]
            idx[key]["clean_pdf"] = f"pv/{key}.clean.pdf"
            idx[key]["scan_params"] = {"dpi": scan_params[key]["dpi"], "pages": scan_params[key]["pages"],
                                       "first_page": scan_params[key]["pages_params"][0]}
            for r in idx[key]["rows"]:
                r["page"] = by_n[r["n"]]["page"]
                r["bbox_pt"] = by_n[r["n"]]["bbox_pt"]
        write_json(out / "pv_index.json", idx)
        # lists
        for inst in insts:
            for year in YEARS:
                members = [g for g in grads if g["institution_id"] == inst["id"] and g["session_year"] == year]
                if not members:
                    continue
                members.sort(key=lambda g: (g["programme_id"], N.norm_name(g["family_name"])))
                items = []
                for g in members:
                    p = prog_by[g["programme_id"]]
                    items.append(dict(name=N.render_name(g["family_name"], g["given_names"], "FG"),
                                      programme=p["name_en"] if g["language"] == "en" else p["name_fr"], mention=g["mention"]))
                render_list(dict(institution=inst, year=year, items=items), str(out / "lists" / f"{inst['id']}_{year}.pdf"))
                list_count += 1
        # diplomas
        tmp = out / "diplomas" / "_tmp"
        tmp.mkdir(exist_ok=True)
        djobs = [(s, str(out / "diplomas"), str(tmp), seed) for s in diploma_specs]
        if args.workers > 1:
            with ProcessPoolExecutor(max_workers=args.workers) as ex:
                dres = list(ex.map(_diploma_job, djobs))
        else:
            dres = [_diploma_job(j) for j in djobs]
        dip_params = dict(dres)
        shutil.rmtree(tmp, ignore_errors=True)
    for c in cases:
        fields = {k: v for k, v in c.items() if not k.startswith("_")}
        write_json(out / "diplomas" / f"{c['case_id']}.json", fields)

    # ---- manifest & ground truth -------------------------------------------------------------
    missing = [
        dict(institution_id="UFN", programme_id="LDROIT", year=MISSING_YEAR, kind="pv", file="pv/UFN_LDROIT_1998.pdf",
             reason="archive lost (deliberately missing) - scenario S5"),
        dict(institution_id="UFN", programme_id="LDROIT", year=MISSING_YEAR, kind="registry", file="registries/UFN_1998.csv",
             reason="archive lost (deliberately missing) - scenario S5"),
        dict(institution_id="UFN", programme_id="LDROIT", year=MISSING_YEAR, kind="list", file="lists/UFN_1998.pdf",
             reason="archive lost (deliberately missing) - scenario S5"),
    ]
    ground_truth = {}
    case_manifest = {}
    for c in cases:
        m = c["_meta"]
        g = grad_by_id[m["person_id"]]
        ev = None
        if g["pv_file"]:
            ev = dict(pv_file=g["pv_file"], row=g["pv_row"], registry_file=f"registries/{g['institution_id']}_{g['session_year']}.{'xlsx' if (g['institution_id'], g['session_year']) in XLSX_REGISTRIES else 'csv'}")
        ground_truth[c["case_id"]] = dict(
            label=c["label"], status=("genuine" if m["truth_authentic"] else "tampered"), truth_authentic=m["truth_authentic"],
            expected_verdict=m["expected_verdict"], expected_failing_checks=m["expected_failing_checks"],
            expected_unknown_checks=m["expected_unknown_checks"], person_id=m["person_id"],
            true_record=dict(family_name=g["family_name"], given_names=g["given_names"], birth_date=g["birth_date"],
                             institution_id=g["institution_id"], programme_id=g["programme_id"], session_year=g["session_year"],
                             average=g["average"], mention=g["mention"], serial=g["diploma_serial"], issue_date=g["issue_date"],
                             signatory_id=g["signatory_id"], accredited_at_session=g["accredited_at_session"]),
            evidence=ev, note=m["note"], details={k: v for k, v in m.items() if k not in ("person_id", "expected_verdict", "expected_failing_checks", "expected_unknown_checks", "truth_authentic", "note")})
        case_manifest[c["case_id"]] = dict(label=c["label"], scenario_tags=c["scenario_tags"], expected_verdict=m["expected_verdict"],
                                           expected_failing_checks=m["expected_failing_checks"], expected_unknown_checks=m["expected_unknown_checks"],
                                           truth_authentic=m["truth_authentic"])
    write_json(out / "ground_truth.json", ground_truth)
    sizes = {}
    for sub in ("pv", "registries", "diplomas", "lists"):
        files = [f for f in (out / sub).iterdir() if f.is_file()]
        sizes[sub] = dict(files=len(files), bytes=sum(f.stat().st_size for f in files))
    degraded = sorted(k for k, s in spec_by_key.items() if s["preset"] == "poor")
    manifest = dict(
        seed=seed, generator="data_gen 1.0", specimen_notice="SPECIMEN – SYNTHETIC DATA: all entities are fictional",
        counts=dict(institutions=len(insts), programmes=sum(len(i["programmes"]) for i in insts),
                    graduates=len(grads), graduates_2015_2025=sum(1 for g in grads if g["session_year"] >= 2015),
                    graduates_1998=sum(1 for g in grads if g["session_year"] == MISSING_YEAR),
                    non_accredited_graduates=sum(1 for g in grads if not g["accredited_at_session"]),
                    pv_pdfs=len(specs), pv_rows=sum(len(s["rows"]) for s in specs),
                    pv_non_admitted_rows=sum(1 for s in specs for r in s["rows"] if r["person_id"] is None),
                    registry_files=len(registry_files), registry_rows=n_reg_rows, lists=list_count, diplomas=len(cases)),
        file_sizes=sizes, missing_archives=missing,
        degraded_pvs=degraded, ocr_hard_pvs=sorted(pv_key(grad_by_id[c["_meta"]["person_id"]]["institution_id"], grad_by_id[c["_meta"]["person_id"]]["programme_id"], grad_by_id[c["_meta"]["person_id"]]["session_year"]) for c in cases if c["label"] == "ocr_hard_name"),
        mention_bands=dict(bands=[[10, 12], [12, 14], [14, 16], [16, 20]], fr=MENTIONS["fr"], en=MENTIONS["en"],
                           failed_row_mention="-"),
        pv_columns=["N°", "Matricule", "Nom et prénoms", "Moyenne", "Mention", "Décision"],
        pv_note="Rows list admitted candidates (Admis/Admitted) and a few non-admitted ones (Ajourné/Failed, mention '-') that are not in graduates.json. "
                "Name order in PV rows is 'FAMILY Given' (~88%) or 'Given FAMILY'. EN programmes add an English sub-label in the header cells.",
        registry_files=registry_files,
        accreditation_check_date="deliberation_date of the session",
        cases=case_manifest, lookalike_for_S5=dict(person_id=lookalike["person_id"], pv=pv_key("UFN", lookalike["programme_id"], lookalike["session_year"])),
        notes=["wrong_session_date: only C5 is strictly failing; C1 also fails if the PV search is scoped to the claimed session. Treat C5 as critical for this label.",
               "ocr_hard_name: expected INSUFFICIENT_EVIDENCE (C1 'unknown', similarity 80-92) although the holder is genuine."],
    )
    write_json(out / "manifest.json", manifest)

    # ---- scenarios & sample ----------------------------------------------------------------
    data_root = out.parent
    if not args.no_scenarios:
        from .scenarios import write_scenarios
        write_scenarios(Path(args.scenarios_dir) if args.scenarios_dir else data_root / "scenarios", grads, cases, diploma_specs,
                        ground_truth, seed, lookalike, insts)
    if not args.no_sample and not args.no_render:
        from .sample import write_sample
        write_sample(out, Path(args.sample_dir) if args.sample_dir else data_root / "sample", ground_truth, manifest, spec_by_key)

    rc = 0
    if not args.no_validate and not args.no_render:
        from .validate import run_checks
        errs = run_checks(out)
        for e in errs:
            print("[validate] FAIL", e)
        print(f"[validate] {'OK' if not errs else str(len(errs)) + ' problem(s)'}")
        rc = 1 if errs else 0
    dt = time.time() - t0
    total = sum(v["bytes"] for v in sizes.values())
    print(f"[data_gen] seed={seed} graduates={len(grads)} pvs={len(specs)} diplomas={len(cases)} "
          f"registries={len(registry_files)} lists={list_count} size={total / 1e6:.1f} MB time={dt:.1f}s -> {out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
