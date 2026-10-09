"""Model assisted extraction. The provider is replaced with a fake, so these tests never call a
network API; they check that the deterministic checks around the model hold."""
import json

import pytest
from fastapi.testclient import TestClient

from app import llm_extract
from app.api import _vocabulary, app
from app.llm_extract import LLMConfig, validate

# Real Meet captions from a test call: misheard words and stray full stops.
TRANSCRIPT = (
    "scenario. So, any country where the? Global conflict and the global scenario is not great. "
    "I would not want to. Go there for my parents sake. And my GPA is. 8, 8.0, and. Um, I don't "
    "have any backlogs. My eyelid score is a 99. Yeah. So, I have completed my btec. In mechanical "
    "engineering. And I want to pursue."
)
VOCAB = _vocabulary()


def model_output(*items):
    return json.dumps({"items": [dict(zip(("field", "value", "quote"), i)) for i in items]})


def test_reads_noisy_captions_and_keeps_grounded_values():
    raw = model_output(
        ("gpa_percent", "8.0/10", "my GPA is. 8, 8.0"),
        ("backlogs", "0", "I don't have any backlogs"),
        ("degree_years", "4", "completed my btec"),
        ("background_tags", "mechanical, engineering", "my btec. In mechanical engineering"),
    )
    accepted, rejected = validate(raw, TRANSCRIPT, VOCAB)
    got = {a["field"]: a["value"] for a in accepted}
    assert got == {"gpa_percent": 76.0, "backlogs": 0, "degree_years": 4, "background_tags": ["engineering", "mechanical"]}
    assert rejected == []
    gpa = next(a for a in accepted if a["field"] == "gpa_percent")
    assert "9.5" in gpa["note"] and gpa["evidence"] == "my GPA is. 8, 8.0"


def test_rejects_values_whose_quote_is_not_in_the_transcript():
    raw = model_output(("ielts_overall", "7", "my IELTS score is 7"))
    accepted, rejected = validate(raw, TRANSCRIPT, VOCAB)
    assert accepted == [] and rejected[0]["reason"] == "quote not found in transcript"


def test_rejects_implausible_values_even_with_a_real_quote():
    raw = model_output(("ielts_overall", "99", "My eyelid score is a 99"))
    accepted, rejected = validate(raw, TRANSCRIPT, VOCAB)
    assert accepted == [] and "IELTS" in rejected[0]["reason"]


def test_value_parsing():
    parse = lambda f, v: llm_extract.parse_value(f, v, VOCAB)[0]  # noqa: E731
    assert parse("gpa_percent", "78%") == 78
    assert parse("gpa_percent", "3.6/4") == 90.0
    assert parse("budget_inr", "4000000") == 4_000_000
    assert parse("budget_inr", "40 lakhs") == 4_000_000
    assert parse("target_intake", "sep 2027") == {"term": "sep", "year": 2027}
    assert parse("target_intake", "fall") == {"term": "sep", "year": None}
    assert parse("target_countries", "Canada, United Kingdom, Mars") == ["canada", "uk"]
    with pytest.raises(ValueError):
        parse("degree_years", "5")
    with pytest.raises(ValueError):
        parse("interest_tags", "astrology")


def test_garbage_output_is_an_error_not_a_crash():
    with pytest.raises(llm_extract.ExtractionError):
        validate("not json", TRANSCRIPT, VOCAB)


def test_prompt_lists_catalogue_tags_and_noise_examples():
    prompt = llm_extract.build_system_prompt(VOCAB)
    assert "data_science" in prompt and "eyelid" in prompt and "btec" in prompt


def test_config_picks_groq_first_and_disables_without_keys(monkeypatch):
    for k in ("LLM_PROVIDER", "LLM_MODEL", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert not llm_extract.config_from_env().enabled
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert llm_extract.config_from_env() == LLMConfig("anthropic", "claude-opus-5-5")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    assert llm_extract.config_from_env() == LLMConfig("groq", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_PROVIDER", "none")
    assert not llm_extract.config_from_env().enabled


def test_extract_endpoint(monkeypatch):
    client = TestClient(app)
    monkeypatch.setattr(llm_extract, "config_from_env", lambda: LLMConfig("groq", "fake-model"))
    monkeypatch.setitem(llm_extract.PROVIDERS, "groq", lambda model, system, user: model_output(("backlogs", "0", "don't have any backlogs")))
    r = client.post("/extract", json={"transcript": TRANSCRIPT})
    assert r.status_code == 200
    assert r.json()["items"] == [{"field": "backlogs", "value": 0, "evidence": "don't have any backlogs"}]
    assert client.get("/extract/status").json() == {"enabled": True, "provider": "groq", "model": "fake-model"}

    monkeypatch.setattr(llm_extract, "config_from_env", lambda: LLMConfig(None, None))
    assert client.post("/extract", json={"transcript": "hi"}).status_code == 503


SECOND_CALL = (
    "But I do not want to be pursuing any. Course in the UK or the US because of the global "
    "conflicts. The most important priority actually. Budget. I was thinking around maybe one to "
    "two years unders to CR, but preferably under 1.5 CRS. Um, I have no backlogs. My cgpa is eight and."
)


def test_ruled_out_countries_priority_and_crore():
    raw = model_output(
        ("excluded_countries", "uk, usa", "Course in the UK or the US"),
        ("priority_preset", "budget", "The most important priority actually. Budget"),
        ("budget_inr", "1.5 crs", "preferably under 1.5 CRS"),
        ("gpa_percent", "8/10", "My cgpa is eight"),
    )
    accepted, rejected = validate(raw, SECOND_CALL, VOCAB)
    got = {a["field"]: a["value"] for a in accepted}
    assert got == {"excluded_countries": ["uk", "usa"], "priority_preset": "budget_first", "budget_inr": 15_000_000, "gpa_percent": 76.0}
    assert rejected == []
