"""Offline checks for the token-optimized LLM paths.

No external API requests are made. Run from the project root:
    python scripts/smoke_test_token_optimization.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, UTC
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from app.models import Answer, Question, Response, User
from app.ai_growth import llm
from app.discovery import discovery
from app.discovery.models import Criteria
import app.quality_engine as quality
from scripts.smoke_test_llm_only import make_db, seed


class FakeMessage:
    def __init__(self, text: str, input_tokens: int = 100, output_tokens: int = 20):
        self.content = [SimpleNamespace(text=text)]
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


def test_compact_prediction_wire() -> None:
    calls: list[dict] = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return FakeMessage('{"p":0.7,"c":"m","pos":["short_task"],"risk":[]}')

    class FakeClient:
        def __init__(self, api_key: str):
            self.messages = FakeMessages()

    old_module = llm.anthropic
    old_key = os.environ.get("ANTHROPIC_API_KEY")
    llm.anthropic = SimpleNamespace(Anthropic=FakeClient)
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    try:
        result = llm.call_claude_json(
            system="Return JSON.",
            payload={"task": "test", "empty": None, "nested": {"x": "", "keep": 0}},
            max_tokens=100,
        )
    finally:
        llm.anthropic = old_module
        if old_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = old_key

    sent = calls[0]["messages"][0]["content"]
    assert sent == '{"task":"test","nested":{"keep":0}}'
    assert result.input_tokens == 100 and result.output_tokens == 20
    assert result.data and result.data["p"] == 0.7


def test_discovery_cache() -> None:
    calls: list[dict] = []
    response = {
        "summary": "One strong channel.",
        "channels": [{
            "name": "Example University Research Registry",
            "channel_type": "registry",
            "url": "https://example.edu/registry",
            "contact_url": "https://example.edu/registry/contact",
            "location": "Online",
            "population_fit": "Matches the requested student population.",
            "access_method": "Request an approved registry posting.",
            "compliant_contact": "Registry partnership",
            "compliance_notes": "Use opt-in recruitment only.",
            "estimated_reach": "Public registry",
            "scale_activity": "Active university registry",
            "local_fit": "Strong",
            "evidence": ["Official registry page"],
            "tags": ["gatekeeper_outreach"],
        }],
        "warnings": [],
    }

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return FakeMessage(json.dumps(response))

    class FakeClient:
        def __init__(self, api_key: str):
            self.messages = FakeMessages()

    old_module = discovery.anthropic
    old_key = os.environ.get("ANTHROPIC_API_KEY")
    discovery.anthropic = SimpleNamespace(Anthropic=FakeClient)
    discovery._DISCOVERY_CACHE.clear()
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    criteria = Criteria(population="graduate students", location="Denver", in_person=False)
    try:
        first = discovery.discover(criteria)
        second = discovery.discover(criteria)
    finally:
        discovery.anthropic = old_module
        discovery._DISCOVERY_CACHE.clear()
        if old_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = old_key

    assert len(calls) == 1
    assert len(first.channels) == 1
    assert second.source.endswith("_cache")
    assert calls[0]["max_tokens"] == 1400
    assert calls[0]["tools"][0]["max_uses"] == 4


def test_quality_gating_and_compaction() -> None:
    questions = {
        i: SimpleNamespace(id=i, order_index=i, question_type="text", question_text=f"Question {i}")
        for i in range(1, 21)
    }
    answers = {i: "x" * 900 for i in questions}

    old_mode = os.environ.get("QUALITY_LLM_MODE")
    os.environ["QUALITY_LLM_MODE"] = "high_risk"
    try:
        assert not quality._should_run_llm(
            question_map=questions,
            rule_penalty=0,
            anomaly_score=0,
            preliminary_score=100,
            survey_reward=20,
        )
        pairs = quality._build_semantic_qa_pairs(questions, answers)
        assert len(pairs) == 8
        assert all(len(item["answer"]) <= 600 for item in pairs)
        os.environ["QUALITY_LLM_MODE"] = "balanced"
        assert quality._should_run_llm(
            question_map=questions,
            rule_penalty=0,
            anomaly_score=0,
            preliminary_score=100,
            survey_reward=20,
        )
    finally:
        if old_mode is None:
            os.environ.pop("QUALITY_LLM_MODE", None)
        else:
            os.environ["QUALITY_LLM_MODE"] = old_mode


def test_compact_quality_response() -> None:
    import anthropic

    calls: list[dict] = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return FakeMessage('{"r":4,"s":3,"c":4,"x":true,"risk":8,"why":"Answers conflict"}')

    class FakeClient:
        def __init__(self, api_key: str):
            self.messages = FakeMessages()

    old_client = anthropic.Anthropic
    old_key = os.environ.get("ANTHROPIC_API_KEY")
    anthropic.Anthropic = FakeClient
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    question_map = {1: SimpleNamespace(id=1, order_index=1, question_type="text", question_text="Explain your choice")}
    try:
        risk, parsed, reasons = quality._run_llm_semantic_eval(
            survey_title="Test",
            survey_description="Description",
            question_map=question_map,
            answers_by_qid={1: "A detailed answer"},
        )
    finally:
        anthropic.Anthropic = old_client
        if old_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = old_key

    assert risk == 15.0
    assert parsed and parsed["semantic_relevance"] == 4
    assert "Answers conflict" in reasons
    assert calls[0]["max_tokens"] <= 320


def test_survey_analysis_aggregation_and_cache() -> None:
    import anthropic
    import api.main as main

    db = make_db()
    publisher, user1, _, survey, _ = seed(db)
    text_question = Question(
        survey_id=survey.id,
        question_text="What did you like?",
        question_type="text",
        is_required=False,
        order_index=2,
    )
    db.add(text_question)
    db.commit()

    for i in range(15):
        participant = User(
            email=f"analysis{i}@example.com",
            password="x",
            username=f"analysis{i}",
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
        db.add(participant)
        db.commit()
        response = Response(
            survey_id=survey.id,
            participant_id=participant.id,
            status="completed",
            completed_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=i),
        )
        db.add(response)
        db.commit()
        first_question = db.query(Question).filter(Question.survey_id == survey.id).order_by(Question.order_index).first()
        db.add_all([
            Answer(response_id=response.id, question_id=first_question.id, answer_value="A" if i < 10 else "B"),
            Answer(response_id=response.id, question_id=text_question.id, answer_value=f"Unique long response {i} " + "detail " * 80),
        ])
        db.commit()

    calls: list[dict] = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return FakeMessage("## Key findings\n- 10 chose A\n## Recommendation\nContinue.")

    class FakeClient:
        def __init__(self, api_key: str):
            self.messages = FakeMessages()

    old_client = anthropic.Anthropic
    old_key = os.environ.get("ANTHROPIC_API_KEY")
    anthropic.Anthropic = FakeClient
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    main._SURVEY_ANALYSIS_CACHE.clear()

    def override_db():
        yield db

    main.app.dependency_overrides[main.get_db] = override_db
    main.app.dependency_overrides[main.get_current_user] = lambda: publisher
    client = TestClient(main.app)
    try:
        first = client.post(f"/surveys/{survey.id}/analyze")
        second = client.post(f"/surveys/{survey.id}/analyze")
    finally:
        main.app.dependency_overrides.clear()
        main._SURVEY_ANALYSIS_CACHE.clear()
        anthropic.Anthropic = old_client
        if old_key is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = old_key

    assert first.status_code == 200 and second.status_code == 200
    assert len(calls) == 1
    assert second.json()["cached"] is True
    content = calls[0]["messages"][0]["content"]
    data = json.loads(content.split("Data:", 1)[1])
    text_items = [item for item in data["questions"] if item["type"] == "text"]
    assert len(text_items[0]["text_samples"]) == 10
    assert all(len(sample) <= 320 for sample in text_items[0]["text_samples"])
    assert calls[0]["max_tokens"] == 750


def main() -> None:
    test_compact_prediction_wire()
    test_discovery_cache()
    test_quality_gating_and_compaction()
    test_compact_quality_response()
    test_survey_analysis_aggregation_and_cache()
    print("Token optimization smoke tests passed.")


if __name__ == "__main__":
    main()
