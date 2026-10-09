"""Load and validate the course catalogue.

Updating course data is a data change, not a code change: edit the YAML files in backend/data,
then run `python -m app.catalog` to validate before deploying.
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import yaml

from .models import Catalog, CountryProfile, Course, University

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class CatalogError(ValueError):
    pass


def load_catalog(data_dir: Path = DATA_DIR) -> Catalog:
    ref = yaml.safe_load((data_dir / "reference.yaml").read_text())
    raw_courses = yaml.safe_load((data_dir / "courses.yaml").read_text())["courses"]

    universities = {u["id"]: University(**u) for u in ref["universities"]}
    countries = {name: CountryProfile(country=name, **c) for name, c in ref["countries"].items()}
    courses = [Course(**c) for c in raw_courses]
    catalog = Catalog(universities=universities, courses=courses, countries=countries, fx_to_inr=ref["fx_to_inr"])
    _check_integrity(catalog)
    return catalog


def _check_integrity(catalog: Catalog) -> None:
    errors: list[str] = []
    seen: set[str] = set()
    for c in catalog.courses:
        if c.id in seen:
            errors.append(f"duplicate course id {c.id}")
        seen.add(c.id)
        uni = catalog.universities.get(c.university_id)
        if uni is None:
            errors.append(f"{c.id}: unknown university {c.university_id}")
            continue
        if uni.country not in catalog.countries:
            errors.append(f"{c.id}: no country profile for {uni.country}")
        if c.currency not in catalog.fx_to_inr:
            errors.append(f"{c.id}: no exchange rate for {c.currency}")
        if not c.intakes:
            errors.append(f"{c.id}: no intakes listed")
    for country in catalog.countries.values():
        if country.currency not in catalog.fx_to_inr:
            errors.append(f"country {country.country}: no exchange rate for {country.currency}")
    if errors:
        raise CatalogError("Catalog validation failed:\n  " + "\n  ".join(errors))


@lru_cache(maxsize=1)
def default_catalog() -> Catalog:
    return load_catalog()


if __name__ == "__main__":
    try:
        cat = load_catalog()
    except Exception as exc:  # noqa: BLE001 - CLI surface
        print(exc)
        sys.exit(1)
    print(f"OK: {len(cat.courses)} courses, {len(cat.universities)} universities, {len(cat.countries)} countries")
