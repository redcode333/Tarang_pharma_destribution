"""Service endpoints with a mocked extractor: no network, no key needed."""
from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def svc(tmp_path_factory):
    import os
    os.environ["TARANG_HISTORY_DB"] = str(tmp_path_factory.mktemp("db") / "history.sqlite")
    sys.path.insert(0, str(REPO))
    import app.main as main
    main = importlib.reload(main)  # pick up the temporary history path
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        yield main, client


class FakeChain:
    models = [type("M", (), {"provider": "gemini", "model": "fake-flash"})()]


def record(**result):
    base = {"vendor": "SAI RAM TEA & SNACKS", "receipt_date": "2026-07-31", "receipt_number": "999001",
            "gstin": "", "gstin_valid": False, "gstin_status": "", "total_amount": 190.0, "flags": [],
            "possible_alteration": False, "vehicle_no_on_bill": "", "guest_name": "", "nights": None,
            "pre_tax_amount": None, "confidence": {"vendor": .95, "receipt_date": .95, "receipt_number": .95,
                                                   "gstin": .9, "total_amount": .95}}
    return {"status": "ok", "model": "fake-flash", "cached": False, "result": base | result,
            "passes": [{"model": "gemini-3.5-flash-lite", "input_tokens": 2000, "output_tokens": 300, "rotation": 0}]}


def photo_bytes(color="white") -> bytes:
    """A distinct random pattern per colour name: plain colours all share one perceptual hash."""
    import random
    rnd = random.Random(color)
    img = Image.new("RGB", (64, 96), "white")
    img.putdata([(v, v, v) for v in (rnd.choice((0, 255)) for _ in range(64 * 96))])
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def form(**kw):
    return {"emp_id": "TPD-0471", "submitted_on": "2026-08-01", "expense_date": "31/07/2026", "category": "Food",
            "vendor": "Sai Ram", "amount_claimed": "190", "rep_comment": ""} | kw


def post(client, data=None, color="white"):
    return client.post("/api/analyze", data=form(**(data or {})),
                       files={"photo": ("r.jpg", photo_bytes(color), "image/jpeg")})


def test_health_and_static_page(svc):
    main, client = svc
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["history_claims"] >= 280
    page = client.get("/")
    assert page.status_code == 200 and "Analyze" in page.text
    assert len(client.get("/api/employees").json()) == 16
    assert [x["claim_id"] for x in client.get("/api/examples").json()] == list(main.EXAMPLES)


def test_no_key_answers_503_politely(svc, monkeypatch):
    main, client = svc
    monkeypatch.setattr(main.state, "chain", None)
    assert client.get("/api/health").json()["model_configured"] is False
    r = post(client)
    assert r.status_code == 503
    assert r.json() == {"error": "No model API key configured. Add GEMINI_API_KEY to .env and restart.",
                        "decision": "review"}


def test_clean_claim_is_approved_with_checks(svc, monkeypatch):
    main, client = svc
    monkeypatch.setattr(main.state, "chain", FakeChain())
    monkeypatch.setattr(main, "run_extraction", lambda path: record())
    b = post(client, {"claim_id": "T-1"}).json()
    assert b["decision"] == "approve" and b["reasons"] == []
    assert b["fields"]["total_amount"] == 190.0 and b["model"] == "fake-flash"
    assert all(c["passed"] for c in b["checks"])
    assert b["est_cost_inr"] > 0 and b["latency_ms"] >= 0


def test_injection_and_alteration_go_to_review(svc, monkeypatch):
    main, client = svc
    monkeypatch.setattr(main.state, "chain", FakeChain())
    monkeypatch.setattr(main, "run_extraction", lambda path: record(
        receipt_number="777002", flags=["instruction_text_on_bill", "possible_alteration"], possible_alteration=True))
    b = post(client, {"claim_id": "T-2"}, color="gray").json()
    assert b["decision"] == "review"
    assert "Receipt contains instructions to the approval system" in b["reasons"]
    assert "Amount appears altered" in b["reasons"]


def test_second_claim_of_the_same_bill_is_a_duplicate(svc, monkeypatch):
    main, client = svc
    monkeypatch.setattr(main.state, "chain", FakeChain())
    monkeypatch.setattr(main, "run_extraction", lambda path: record(receipt_number="555003"))
    assert post(client, {"claim_id": "T-3"}, color="black").json()["decision"] == "approve"
    b = post(client, {"claim_id": "T-4", "submitted_on": "2026-08-02", "emp_id": "TPD-0412"}, color="red").json()
    assert b["decision"] == "review" and b["reasons"][0] == "Possible duplicate of T-3 (TPD-0471)"


def test_2000_or_more_is_review(svc, monkeypatch):
    main, client = svc
    monkeypatch.setattr(main.state, "chain", FakeChain())
    monkeypatch.setattr(main, "run_extraction", lambda path: record(receipt_number="444004", total_amount=2400.0))
    b = post(client, {"claim_id": "T-5", "amount_claimed": "2,400"}, color="blue").json()
    assert b["decision"] == "review" and "Rs 2,000 or more" in b["reasons"][-1]


def test_model_failure_is_a_message_not_a_stack_trace(svc, monkeypatch):
    main, client = svc
    monkeypatch.setattr(main.state, "chain", FakeChain())

    def boom(path):
        raise RuntimeError("secret internals")
    monkeypatch.setattr(main, "run_extraction", boom)
    r = post(client, {"claim_id": "T-6"})
    assert r.status_code == 502 and "secret internals" not in r.text and r.json()["decision"] == "review"
