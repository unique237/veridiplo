"""Sample diplomas for the Verify page (synthetic specimens; the API loads the fixture by `diploma_case_id`).

Hard-coded fallback used when the API has no `GET /cases/samples` endpoint. All names are fictional (SPECIMEN data).
"""
from __future__ import annotations

from typing import Any

from common import api

SAMPLES: list[dict[str, Any]] = [
    {
        "diploma_case_id": "GEN-001",
        "label": "genuine",
        "fields": {
            "holder_name": "DAIROU Anne-Marie",
            "birth_date": "1998-07-23",
            "birth_place": "Garoua",
            "institution_id": "UFN",
            "programme_id": "LINFO",
            "session_year": 2024,
            "average": 13.45,
            "mention": "Assez Bien",
            "serial": "UFN/2024/LINFO/00036",
            "signatory_name": "Joséphine ABENA MANGA",
            "issue_date": "2024-09-30"
        }
    },
    {
        "diploma_case_id": "GEN-002",
        "label": "genuine",
        "fields": {
            "holder_name": "Honorine TSANGA MVONDO",
            "birth_date": "1999-05-11",
            "birth_place": "Ambam",
            "institution_id": "UFB",
            "programme_id": "LGEST",
            "session_year": 2022,
            "average": 13.3,
            "mention": "Assez Bien",
            "serial": "UFB.2022.000154",
            "signatory_name": "Samuel Aimé TCHINDA KAMGA",
            "issue_date": "2022-10-24"
        }
    },
    {
        "diploma_case_id": "TMEN-001",
        "label": "tampered_mention",
        "fields": {
            "holder_name": "BELINGA Bertrand",
            "birth_date": "1999-05-25",
            "birth_place": "Obala",
            "institution_id": "UFN",
            "programme_id": "LDROIT",
            "session_year": 2024,
            "average": 14.27,
            "mention": "Bien",
            "serial": "UFN/2024/LDROIT/00153",
            "signatory_name": "Joséphine ABENA MANGA",
            "issue_date": "2024-12-25"
        }
    },
    {
        "diploma_case_id": "TAVG-001",
        "label": "tampered_average",
        "fields": {
            "holder_name": "Ghislain NGAMENI",
            "birth_date": "1999-02-01",
            "birth_place": "Douala",
            "institution_id": "UFN",
            "programme_id": "LDROIT",
            "session_year": 2021,
            "average": 10.52,
            "mention": "Passable",
            "serial": "UFN/2021/LDROIT/00095",
            "signatory_name": "Alphonse MVONDO BELINGA",
            "issue_date": "2021-08-27"
        }
    },
    {
        "diploma_case_id": "TNAM-001",
        "label": "tampered_name",
        "fields": {
            "holder_name": "HAMAN Marie",
            "birth_date": "1994-04-01",
            "birth_place": "Garoua",
            "institution_id": "UFN",
            "programme_id": "LINFO",
            "session_year": 2017,
            "average": 13.84,
            "mention": "Assez Bien",
            "serial": "UFN/2017/LINFO/00752",
            "signatory_name": "Alphonse MVONDO BELINGA",
            "issue_date": "2017-09-11"
        }
    },
    {
        "diploma_case_id": "WSES-001",
        "label": "wrong_session_date",
        "fields": {
            "holder_name": "ABENA Ernest Aimé",
            "birth_date": "1993-01-27",
            "birth_place": "Mbalmayo",
            "institution_id": "UFB",
            "programme_id": "LCOMPT",
            "session_year": 2018,
            "average": 12.6,
            "mention": "Assez Bien",
            "serial": "UFB.2018.000711",
            "signatory_name": "Samuel Aimé TCHINDA KAMGA",
            "issue_date": "2018-11-05"
        }
    },
    {
        "diploma_case_id": "NACC-001",
        "label": "not_accredited",
        "fields": {
            "holder_name": "Solange Godlove NGU",
            "birth_date": "1996-01-04",
            "birth_place": "Bali",
            "institution_id": "UFN",
            "programme_id": "LINFO",
            "session_year": 2019,
            "average": 11.86,
            "mention": "Passable",
            "serial": "UFN/2019/LINFO/00097",
            "signatory_name": "Alphonse MVONDO BELINGA",
            "issue_date": "2019-12-06"
        }
    },
    {
        "diploma_case_id": "SOOO-001",
        "label": "signatory_out_of_office",
        "fields": {
            "holder_name": "FOKOU Pierre-Marie",
            "birth_date": "1991-05-09",
            "birth_place": "Dschang",
            "institution_id": "UFN",
            "programme_id": "MINFO",
            "session_year": 2017,
            "average": 12.33,
            "mention": "Assez Bien",
            "serial": "UFN/2017/MINFO/00068",
            "signatory_name": "Charlotte NGO BATJOM",
            "issue_date": "2018-01-15"
        }
    },
    {
        "diploma_case_id": "DUPS-001",
        "label": "duplicate_serial",
        "fields": {
            "holder_name": "DJOUMESSI Éric-Joël",
            "birth_date": "2001-05-02",
            "birth_place": "Dschang",
            "institution_id": "UFB",
            "programme_id": "LLOG",
            "session_year": 2023,
            "average": 12.96,
            "mention": "Assez Bien",
            "serial": "UFB.2023.000155",
            "signatory_name": "Samuel Aimé TCHINDA KAMGA",
            "issue_date": "2023-12-04"
        }
    },
    {
        "diploma_case_id": "MISS-001",
        "label": "missing_archive",
        "fields": {
            "holder_name": "BELINGA Laure Inès",
            "birth_date": "1977-11-27",
            "birth_place": "Mbalmayo",
            "institution_id": "UFN",
            "programme_id": "LDROIT",
            "session_year": 1998,
            "average": 11.08,
            "mention": "Passable",
            "serial": "UFN/1998/LDROIT/00005",
            "signatory_name": "Hyacinthe Roger MBOLO ESSIMI",
            "issue_date": "1999-01-25"
        }
    },
    {
        "diploma_case_id": "OCRH-001",
        "label": "ocr_hard_name",
        "fields": {
            "holder_name": "Jean-Remi ETOUND-MBALL OWONO",
            "birth_date": "2001-01-05",
            "birth_place": "Maroua",
            "institution_id": "UFN",
            "programme_id": "LDROIT",
            "session_year": 2022,
            "average": 11.05,
            "mention": "Passable",
            "serial": "UFN/2022/LDROIT/00081",
            "signatory_name": "Joséphine ABENA MANGA",
            "issue_date": "2022-11-30"
        }
    },
    {
        "diploma_case_id": "REVN-001",
        "label": "reversed_name_order",
        "fields": {
            "holder_name": "Hugues Nicaise MVONDO",
            "birth_date": "2001-12-04",
            "birth_place": "Sangmélima",
            "institution_id": "UFB",
            "programme_id": "LGEST",
            "session_year": 2024,
            "average": 16.27,
            "mention": "Très Bien",
            "serial": "UFB.2024.000040",
            "signatory_name": "Prisca ELLA NKODO",
            "issue_date": "2025-01-20"
        }
    }
]


def load_samples() -> list[dict[str, Any]]:
    """Samples from GET /cases/samples when available (list of dicts with case_id/diploma_case_id), else SAMPLES."""
    r = api("GET", "/cases/samples", timeout=4)
    data = r.data.get("samples") if r.ok and isinstance(r.data, dict) else r.data if r.ok else None
    if not isinstance(data, list) or not data:
        return SAMPLES
    out = []
    for d in data:
        if not isinstance(d, dict):
            continue
        cid = d.get("diploma_case_id") or d.get("case_id") or d.get("id")
        if not cid:
            continue
        fields = d.get("fields") if isinstance(d.get("fields"), dict) else {
            k: v for k, v in d.items() if k in ("holder_name", "birth_date", "birth_place", "institution_id", "programme_id",
                                               "session_year", "average", "mention", "serial", "signatory_name", "issue_date")}
        out.append({"diploma_case_id": cid, "label": d.get("label", ""), "fields": fields})
    return out or SAMPLES
