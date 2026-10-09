"""Check the engine against the calibration personas.

    python -m app.calibrate              # personas with their own preset
    python -m app.calibrate --all        # every persona under every preset

Use this when tuning config/presets.yaml: a weight change is only an improvement if agreement
with counsellor expectations goes up.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from .catalog import default_catalog
from .config import default_config
from .engine import Recommendation, recommend
from .models import StudentProfile

PERSONAS_PATH = Path(__file__).resolve().parent.parent / "tests" / "personas.yaml"


@dataclass
class Check:
    persona: str
    description: str
    passed: bool


def load_personas(path: Path = PERSONAS_PATH):
    raw = yaml.safe_load(path.read_text())
    as_of = raw["as_of"] if isinstance(raw["as_of"], date) else date.fromisoformat(raw["as_of"])
    return as_of, raw["personas"]


def check(persona: dict, rec: Recommendation) -> list[Check]:
    name, exp = persona["name"], persona["expect"]
    ranked_ids = [r.course_id for r in rec.ranked]
    aside = {s.course_id: s.gate for s in rec.set_aside}
    out: list[Check] = []

    if "top_n_includes" in exp:
        n = exp["top_n_includes"]["n"]
        for cid in exp["top_n_includes"]["ids"]:
            out.append(Check(name, f"{cid} in top {n}", cid in ranked_ids[:n]))
    if "top_n_country_includes" in exp:
        n, country = exp["top_n_country_includes"]["n"], exp["top_n_country_includes"]["country"]
        out.append(Check(name, f"a {country} course in top {n}", any(r.country == country for r in rec.ranked[:n])))
    for cid, gate in (exp.get("set_aside") or {}).items():
        out.append(Check(name, f"{cid} set aside by {gate}", aside.get(cid) == gate))
    for cid in exp.get("not_ranked") or []:
        out.append(Check(name, f"{cid} not ranked", cid not in ranked_ids))
    if "max_confidence" in exp:
        out.append(Check(name, f"confidence <= {exp['max_confidence']}", rec.confidence <= exp["max_confidence"]))
    return out


def run(all_presets: bool = False) -> dict[str, list[Check]]:
    as_of, personas = load_personas()
    cat, cfg = default_catalog(), default_config()
    results: dict[str, list[Check]] = {}
    for preset_key in cfg.presets:
        checks: list[Check] = []
        for p in personas:
            if not all_presets and p["preset"] != preset_key:
                continue
            rec = recommend(cat, StudentProfile(**p["profile"]), preset_key, as_of, cfg)
            checks += check(p, rec)
        if checks:
            results[preset_key] = checks
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="run every persona under every preset")
    args = parser.parse_args()
    results = run(args.all)
    grand_pass = grand_total = 0
    for preset, checks in results.items():
        passed = sum(c.passed for c in checks)
        grand_pass, grand_total = grand_pass + passed, grand_total + len(checks)
        print(f"\n{preset}: {passed}/{len(checks)} expectations hold")
        for c in checks:
            if not c.passed:
                print(f"  FAIL  {c.persona}: {c.description}")
    print(f"\nOverall agreement: {grand_pass}/{grand_total} ({100 * grand_pass / max(grand_total, 1):.0f}%)")


if __name__ == "__main__":
    main()
