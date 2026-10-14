"""Tarang receipts service: one endpoint (photo + claim -> fields, decision, reasons) and one screen.

Runs the same pipeline as predictions.csv: extract (cached by photo hash) -> GSTIN repair and the
other post-processing -> rules -> decide. Duplicates are checked against a SQLite history seeded
from claims_train and extended with every analysed claim.

Start: uvicorn app.main:app   (works with no API key; /api/analyze then answers 503 politely)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import asynccontextmanager, closing
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from receipts.decide import decide  # noqa: E402
from receipts.dedupe import DuplicateIndex  # noqa: E402
from receipts.extract import USAGE_PATH, ExtractionCache, extract  # noqa: E402
from receipts.images import file_hash, phash  # noqa: E402
from receipts.normalize import is_blank, resolve_image  # noqa: E402
from receipts.pricing import passes_cost_inr  # noqa: E402
from receipts.providers import AllModelsExhausted, NoProviderConfigured, build_chain  # noqa: E402

DATA = REPO / "data"
STATIC = Path(__file__).parent / "static"
HISTORY_DB = Path(os.environ.get("TARANG_HISTORY_DB", REPO / "cache" / "history.sqlite"))
NO_KEY_MESSAGE = "No model API key configured. Add GEMINI_API_KEY to .env and restart."
# Three train claims for the "Load example" menu.
EXAMPLES = {
    "CLM-30572": "Clean bill, under Rs 2,000 (approve)",
    "CLM-30804": "Same bill claimed again by a colleague (duplicate)",
    "CLM-30623": "Fake 'pre-verified' stamp and an inked amount (injection)",
}


class State:
    chain = None
    chain_error: str | None = None
    employees: dict[str, dict] = {}
    train: pd.DataFrame | None = None


state = State()


# --- duplicate history ----------------------------------------------------------

def _db() -> sqlite3.Connection:
    HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(HISTORY_DB)
    con.execute("CREATE TABLE IF NOT EXISTS history (claim_id TEXT PRIMARY KEY, emp_id TEXT, submitted_on TEXT, "
                "bills TEXT, phash TEXT, source TEXT)")
    return con


def seed_history(train: pd.DataFrame) -> int:
    """Index every processed claim once: the clerk's and the model's bill number, and the photo hash."""
    cache = ExtractionCache()
    with closing(_db()) as con, con:
        if con.execute("SELECT COUNT(*) FROM history WHERE source = 'train'").fetchone()[0]:
            return 0
        rows = []
        for c in train.to_dict("records"):
            path = resolve_image(c["image_file"])
            r = ((cache.get(file_hash(path)) if path else None) or {}).get("result", {})
            bills = [(c["clerk_bill_no"], c["clerk_vendor"] or c["vendor"])] if not is_blank(c["clerk_bill_no"]) else []
            bills.append((r.get("receipt_number", ""), r.get("vendor") or c["vendor"]))
            rows.append((c["claim_id"], c["emp_id"], c["submitted_on"], json.dumps(bills),
                         phash(path) if path else None, "train"))
        con.executemany("INSERT OR IGNORE INTO history VALUES (?,?,?,?,?,?)", rows)
        return len(rows)


def history_index(exclude: str, submitted_on: str) -> DuplicateIndex:
    """Claims submitted on or before this one, other than itself."""
    idx = DuplicateIndex()
    with closing(_db()) as con:
        for cid, emp, sub, bills, h in con.execute(
                "SELECT claim_id, emp_id, submitted_on, bills, phash FROM history ORDER BY submitted_on, claim_id"):
            if cid != exclude and (not submitted_on or (sub or "") <= submitted_on):
                idx.add(cid, emp, [tuple(b) for b in json.loads(bills)], h)
    return idx


def remember(claim_id: str, emp_id: str, submitted_on: str, bills: list, h: str | None) -> None:
    with closing(_db()) as con, con:
        con.execute("INSERT OR IGNORE INTO history VALUES (?,?,?,?,?,?)",
                    (claim_id, emp_id, submitted_on, json.dumps(bills), h, "web"))


# --- app ------------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_: FastAPI):
    state.employees = {r["emp_id"]: r for r in
                       pd.read_csv(DATA / "employees.csv", dtype=str, keep_default_na=False).to_dict("records")}
    state.train = pd.read_csv(DATA / "claims_train.csv", dtype=str, keep_default_na=False)
    try:
        state.chain = build_chain(USAGE_PATH)
        state.chain.max_wait_s = 30  # a web request must not sit out a 15-minute quota wait
        state.chain_error = None
    except NoProviderConfigured as exc:
        state.chain, state.chain_error = None, str(exc)
    seed_history(state.train)
    yield


app = FastAPI(title="Tarang receipts", lifespan=lifespan)


def run_extraction(path: Path) -> dict:
    """The model step; tests replace this with a fake."""
    return extract(path, chain=state.chain)


@app.get("/api/health")
def health():
    with closing(_db()) as con:
        n = con.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    models = [f"{m.provider}:{m.model}" for m in state.chain.models] if state.chain else []
    return {"status": "ok", "model_configured": state.chain is not None,
            "provider": models[0].split(":")[0] if models else None, "models": models,
            "history_claims": n, "message": None if state.chain else NO_KEY_MESSAGE}


@app.get("/api/employees")
def employees():
    return [{k: e[k] for k in ("emp_id", "name", "designation", "grade", "vehicle_no")}
            for e in state.employees.values()]


@app.get("/api/examples")
def examples():
    t = state.train.set_index("claim_id")
    out = []
    for cid, label in EXAMPLES.items():
        c = t.loc[cid]
        out.append({"claim_id": cid, "label": label, "photo_url": f"/api/examples/{cid}/photo",
                    **{k: c[k] for k in ("emp_id", "submitted_on", "expense_date", "category", "vendor",
                                         "amount_claimed", "rep_comment")}})
    return out


@app.get("/api/examples/{claim_id}/photo")
def example_photo(claim_id: str):
    if claim_id not in EXAMPLES:
        return JSONResponse({"error": "not an example"}, status_code=404)
    return FileResponse(resolve_image(state.train.set_index("claim_id").loc[claim_id, "image_file"]))


@app.post("/api/analyze")
async def analyze(photo: UploadFile = File(...), emp_id: str = Form(...), submitted_on: str = Form(...),
                  expense_date: str = Form(""), category: str = Form(...), vendor: str = Form(""),
                  amount_claimed: str = Form(...), rep_comment: str = Form(""), claim_id: str = Form("")):
    if state.chain is None:
        return JSONResponse({"error": NO_KEY_MESSAGE, "decision": "review"}, status_code=503)
    start = time.monotonic()
    data = await photo.read()
    if not data:
        return JSONResponse({"error": "The photo is empty. Choose a receipt photo and try again.",
                             "decision": "review"}, status_code=400)
    suffix = Path(photo.filename or "photo.jpg").suffix or ".jpg"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        path.write_bytes(data)
        try:
            rec = run_extraction(path)
        except AllModelsExhausted:
            return JSONResponse({"error": "The model's free quota is used up for now. Try again later; "
                                          "photos already read still work.", "decision": "review"}, status_code=503)
        except NoProviderConfigured:
            return JSONResponse({"error": NO_KEY_MESSAGE, "decision": "review"}, status_code=503)
        except Exception as exc:  # anything else: a polite message, never a stack trace
            return JSONResponse({"error": f"Could not read this photo ({type(exc).__name__}). "
                                          "Try a clearer photo.", "decision": "review"}, status_code=502)
        try:
            h = phash(path)
        except Exception:
            h = None
    r = rec.get("result", {})
    claim_id = claim_id.strip() or f"WEB-{file_hash_bytes(data)[:10]}"
    claim = {"claim_id": claim_id, "emp_id": emp_id, "submitted_on": submitted_on, "expense_date": expense_date,
             "category": category, "vendor": vendor, "amount_claimed": amount_claimed, "rep_comment": rep_comment}
    bills = [(r.get("receipt_number", ""), r.get("vendor") or vendor)]
    hit = history_index(claim_id, submitted_on).find(bills, h)
    d = decide(claim, rec, state.employees.get(emp_id), hit)
    remember(claim_id, emp_id, submitted_on, bills, h)
    return {
        "claim_id": claim_id,
        "fields": {k: r.get(k) for k in ("vendor", "receipt_date", "receipt_number", "gstin", "total_amount")},
        "decision": d.decision,
        "reasons": d.reasons,
        "checks": d.checks,
        "confidence": r.get("confidence", {}),
        "flags": r.get("flags", []),
        "model": rec.get("model"),
        "cached": bool(rec.get("cached")),
        "latency_ms": int((time.monotonic() - start) * 1000),
        "est_cost_inr": passes_cost_inr(rec.get("passes", [])),  # cost of reading this photo; paid once
        "cost_note": "already read earlier, no new model call" if rec.get("cached") else "model called now",
    }


def file_hash_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
