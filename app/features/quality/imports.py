"""Features / quality / imports."""

from datetime import datetime
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.responses import JSONResponse
from io import BytesIO
from pathlib import Path
from sqlalchemy.orm import Session
from typing import List
from typing import Optional
import uuid
from app.bootstrap.settings import QUALITY_IMPORT_UPLOAD_DIR
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.jobs.service import enqueue_quality_excel_import_job
from app.models import Question
from app.models import Response
from app.models import ResponseQualityCheck
from app.models import Survey
from app.models import User
from app.quality_engine import _duration_seconds_between
from app.quality_engine import anthropic_api_key_configured
from app.quality_engine import apply_auto_approve_checks
from app.quality_engine import batch_anomaly_scores_for_features
from app.quality_engine import compute_excel_row_quality
from app.quality_engine import create_excel_quality_check
from app.quality_engine import resolve_excel_row_context


def _process_quality_excel_import(
    db: Session,
    *,
    survey: Survey,
    filename: str,
    content: bytes,
    auto_approve: bool,
    auto_approve_min_score: Optional[float],
    use_llm: bool = False,
) -> dict:
    from app.features.quality.reviews import _parse_auto_approve_min_score
    from app.features.quality.serialization import _json_safe_value
    from app.features.quality.serialization import _normalize_excel_header
    from app.features.quality.serialization import _value_as_float

    try:
        from openpyxl import load_workbook
    except Exception:
        raise HTTPException(500, "openpyxl is required. Please install dependency first.")

    try:
        workbook = load_workbook(BytesIO(content), data_only=True)
    except Exception as exc:
        raise HTTPException(400, f"Invalid Excel file: {exc}")

    worksheet = workbook.active
    rows = list(worksheet.iter_rows(values_only=True))
    if len(rows) < 2:
        raise HTTPException(400, "Excel should include a header row and at least one data row")
    if len(rows) - 1 > 500:
        raise HTTPException(400, "Excel has too many rows. Please upload 500 responses or fewer per file.")

    raw_headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    headers = [_normalize_excel_header(h) for h in raw_headers]
    if not any(headers):
        raise HTTPException(400, "Header row is empty")

    survey_id = survey.id
    replaced_rows = db.query(ResponseQualityCheck).filter(
        ResponseQualityCheck.survey_id == survey_id,
        ResponseQualityCheck.source_type == "excel",
    ).delete(synchronize_session=False)

    questions = db.query(Question).filter(Question.survey_id == survey_id).all()
    question_map = {q.id: q for q in questions}

    header_to_qid = {}
    for idx, header in enumerate(headers):
        if not header:
            continue
        for q in questions:
            candidates = {
                str(q.id).strip().lower(),
                f"q_{q.id}".lower(),
                _normalize_excel_header(q.question_text),
            }
            if header in candidates:
                header_to_qid[idx] = q.id
                break

    historical = []
    completed = db.query(Response).filter(
        Response.survey_id == survey_id,
        Response.status == "completed",
        Response.started_at.isnot(None),
        Response.completed_at.isnot(None),
    ).all()
    for item in completed:
        sec = _duration_seconds_between(item.started_at, item.completed_at)
        if sec:
            historical.append(sec)

    duration_columns = {"duration", "duration_seconds", "time_spent", "completion_seconds"}
    parsed_rows = []

    for row_index, row in enumerate(rows[1:], start=2):
        if row is None:
            continue
        values = list(row)
        if not any(v is not None and str(v).strip() != "" for v in values):
            continue

        answers_by_qid = {}
        row_dict = {}
        for idx, value in enumerate(values):
            header_name = raw_headers[idx] if idx < len(raw_headers) else f"col_{idx+1}"
            safe_value = _json_safe_value(value)
            row_dict[header_name] = safe_value
            if idx in header_to_qid:
                answers_by_qid[header_to_qid[idx]] = safe_value

        duration_seconds = None
        for idx, header in enumerate(headers):
            if header in duration_columns and idx < len(values):
                duration_seconds = _value_as_float(values[idx])
                if duration_seconds is not None:
                    break

        row_question_map, row_answers, row_features = resolve_excel_row_context(
            row_dict=row_dict,
            mapped_question_map=question_map,
            mapped_answers=answers_by_qid,
            duration_seconds=duration_seconds,
        )
        parsed_rows.append({
            "row_index": row_index,
            "row_dict": row_dict,
            "question_map": row_question_map,
            "answers_by_qid": row_answers,
            "duration_seconds": duration_seconds,
            "features": row_features,
        })

    if not parsed_rows:
        raise HTTPException(400, "No data rows found in Excel.")

    all_features = [item["features"] for item in parsed_rows]
    batch_anomalies = batch_anomaly_scores_for_features(all_features)
    imported = 0
    by_label = {"high": 0, "medium": 0, "low": 0, "fraud_risk": 0}
    llm_rows_reviewed = 0
    llm_rows_failed = 0
    created_checks: List[ResponseQualityCheck] = []
    use_auto_approve = bool(auto_approve)
    auto_min_score = _parse_auto_approve_min_score(auto_approve_min_score if use_auto_approve else 100)

    for index, item in enumerate(parsed_rows):
        result = compute_excel_row_quality(
            row_dict=item["row_dict"],
            mapped_question_map=item["question_map"],
            mapped_answers=item["answers_by_qid"],
            duration_seconds=item["duration_seconds"],
            historical_durations=historical,
            survey_title=survey.title,
            survey_description=survey.description,
            survey_reward=float(survey.reward_amount or 0.0),
            # Excel imports are local-only by default. Semantic LLM review is
            # performed only when the researcher explicitly opts in.
            run_llm=bool(use_llm),
            precomputed_anomaly=batch_anomalies[index] if index < len(batch_anomalies) else None,
        )
        created_checks.append(create_excel_quality_check(
            db,
            survey_id=survey_id,
            source_ref=f"{filename}:row_{item['row_index']}",
            raw_response_json=item["row_dict"],
            result=result,
        ))
        if result.llm_result_json:
            if result.llm_result_json.get("error"):
                llm_rows_failed += 1
            elif not result.llm_result_json.get("skipped"):
                llm_rows_reviewed += 1
        by_label[result.quality_label] = by_label.get(result.quality_label, 0) + 1
        imported += 1

    if imported == 0:
        raise HTTPException(400, "No data rows found in Excel.")

    auto_approved_count = 0
    auto_rejected_count = 0
    if use_auto_approve:
        auto_approved_count, auto_rejected_count = apply_auto_approve_checks(created_checks, auto_min_score)

    db.commit()
    return {
        "message": "excel imported and quality checks generated",
        "survey_id": survey_id,
        "imported_rows": imported,
        "replaced_rows": replaced_rows,
        "label_distribution": by_label,
        "used_generic_columns": len(question_map) == 0,
        "auto_approve": {
            "enabled": use_auto_approve,
            "min_score": auto_min_score if use_auto_approve else None,
            "approved_count": auto_approved_count,
            "rejected_count": auto_rejected_count,
        },
        "llm_summary": {
            "requested": bool(use_llm),
            "rows_reviewed": llm_rows_reviewed,
            "rows_failed": llm_rows_failed,
            "api_key_configured": anthropic_api_key_configured(),
        },
    }


def import_excel_for_quality_check(
    survey_id: int,
    file: UploadFile = File(...),
    auto_approve: Optional[str] = Form(None),
    auto_approve_min_score: Optional[float] = Form(None),
    use_llm: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(
        Survey.id == survey_id,
        Survey.publisher_id == current_user.id,
    ).first()
    if not survey:
        raise HTTPException(404, "Survey not found")

    filename = file.filename or "upload.xlsx"
    lower_name = filename.lower()
    if not (lower_name.endswith(".xlsx") or lower_name.endswith(".xlsm")):
        raise HTTPException(400, "Only .xlsx/.xlsm files are supported for now")

    content = file.file.read()
    if not content:
        raise HTTPException(400, "Excel file is empty")

    QUALITY_IMPORT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_name = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{survey_id}_{uuid.uuid4().hex}_{Path(filename).name}"
    upload_path = QUALITY_IMPORT_UPLOAD_DIR / upload_name
    upload_path.write_bytes(content)

    use_auto_approve = str(auto_approve or "").strip().lower() in {"1", "true", "yes", "on"}
    use_llm_review = str(use_llm or "").strip().lower() in {"1", "true", "yes", "on"}
    job = enqueue_quality_excel_import_job(
        db,
        survey_id=survey_id,
        user_id=current_user.id,
        upload_path=str(upload_path),
        filename=filename,
        auto_approve=use_auto_approve,
        auto_approve_min_score=auto_approve_min_score if use_auto_approve else None,
        use_llm=use_llm_review,
    )
    db.commit()
    return JSONResponse({
        "message": "quality import queued",
        "queued": True,
        "job_id": job.id,
        "survey_id": survey_id,
        "llm_requested": use_llm_review,
    }, status_code=202)


__all__ = ['_process_quality_excel_import', 'import_excel_for_quality_check']
