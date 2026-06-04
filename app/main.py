from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import database
from app.services.openai_service import RoboertaAIError, compare_ad_to_submission, parse_submission, parse_weekly_ad


BASE_DIR = Path(__file__).resolve().parent.parent


def redirect_with_status(message: str = "", error: str = "", focus: str = "") -> RedirectResponse:
    query_parts = []
    if message:
        query_parts.append(f"message={message}")
    if error:
        query_parts.append(f"error={error}")
    if focus:
        query_parts.append(f"focus={focus}")
    suffix = f"/?{'&'.join(query_parts)}" if query_parts else "/"
    return RedirectResponse(url=suffix, status_code=303)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.init_db()
    database.prune_expired_reports()
    yield


app = FastAPI(title="RoboertaAI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
database.init_db()


@app.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    message: str = "",
    error: str = "",
    focus: str = "",
    view_group: str = "",
) -> HTMLResponse:
    database.prune_expired_reports()
    active_ad = database.get_active_ad()
    reports = database.list_reports()
    allowed_view_groups = {"five_friday_items", "front_page_items", "price_lock_items"}
    selected_view_group = view_group if view_group in allowed_view_groups else ""

    ad_updated_label = "Not updated"
    if active_ad and active_ad.get("created_at"):
        try:
            parsed_created = datetime.fromisoformat(active_ad["created_at"])
            ad_updated_label = parsed_created.strftime("%Y-%m-%d %I:%M %p")
        except ValueError:
            ad_updated_label = str(active_ad["created_at"])

    filtered_ad_items: list[dict[str, Any]] = []
    filtered_ad_groups: dict[str, list[dict[str, Any]]] = {}
    filtered_ad_title = "All Weekly Ad Items"

    # Secondary category order shown when a filter is active
    secondary_category_order = [
        "just_4_u_items",
        "member_price_items",
        "price_lock_items",
        "four_x_points_items",
        "front_page_items",
        "five_friday_items",
        "regular_items",
    ]

    if active_ad:
        all_ad_items = active_ad.get("items", [])
        if selected_view_group:
            filtered_ad_items = [item for item in all_ad_items if selected_view_group in item.get("tags", [])]
            filtered_ad_title = database.GROUP_LABELS.get(selected_view_group, "Filtered Items")
            # Group filtered items by secondary promo tags
            assigned_ids: set[int] = set()
            for category in secondary_category_order:
                if category == selected_view_group:
                    continue
                group_items = [item for item in filtered_ad_items if category in item.get("tags", [])]
                if group_items:
                    filtered_ad_groups[category] = group_items
                    assigned_ids.update(id(item) for item in group_items)
            # Items that only carry the primary filter tag land in _other
            uncategorised = [item for item in filtered_ad_items if id(item) not in assigned_ids]
            if uncategorised:
                filtered_ad_groups["_other"] = uncategorised
        else:
            filtered_ad_items = all_ad_items

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "page_title": "RoboertaAI",
            "ad_updated_label": ad_updated_label,
            "message": message,
            "error": error,
            "focus": focus,
            "active_ad": active_ad,
            "reports": reports,
            "group_labels": database.GROUP_LABELS,
            "group_order": database.GROUP_ORDER,
            "selected_view_group": selected_view_group,
            "filtered_ad_items": filtered_ad_items,
            "filtered_ad_groups": filtered_ad_groups,
            "filtered_ad_title": filtered_ad_title,
            "category_group_order": [c for c in ["just_4_u_items", "member_price_items", "price_lock_items", "four_x_points_items", "front_page_items", "five_friday_items", "regular_items"] if c != selected_view_group] + ["_other"],
        },
    )


@app.head("/")
async def home_head() -> HTMLResponse:
    return HTMLResponse(status_code=200)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def read_upload(upload: UploadFile) -> dict[str, Any]:
    return {
        "filename": upload.filename or "upload",
        "content_type": upload.content_type or "application/octet-stream",
        "content": await upload.read(),
    }


@app.post("/admin/weekly-ad")
async def upload_weekly_ad(
    ad_date: str = Form(""),
    files: list[UploadFile] = File(...),
) -> RedirectResponse:
    valid_uploads = [upload for upload in files if upload.filename]
    if not valid_uploads:
        return redirect_with_status(error="Upload at least one weekly ad file.")
    try:
        uploads = [await read_upload(upload) for upload in valid_uploads]
        parsed = parse_weekly_ad(uploads)
        final_ad_date = ad_date or parsed.get("ad_date") or "Unknown"
        database.replace_weekly_ad(final_ad_date, [item["filename"] for item in uploads], parsed["items"])
    except RoboertaAIError as exc:
        return redirect_with_status(error=str(exc))
    return redirect_with_status(message=f"Weekly ad loaded for {final_ad_date}.")


@app.post("/reports")
async def create_report(
    photo: Optional[UploadFile] = File(None),
    planner: Optional[UploadFile] = File(None),
) -> RedirectResponse:
    has_photo = photo is not None and bool(photo.filename)
    has_planner = planner is not None and bool(planner.filename)
    if has_photo == has_planner:
        return redirect_with_status(error="Submit either a photo or a planner, but not both.")

    active_ad = database.get_active_ad()
    if active_ad is None:
        return redirect_with_status(error="Load the weekly ad before generating a report.")

    upload = photo if has_photo else planner
    kind = "photo" if has_photo else "planner"
    assert upload is not None
    try:
        upload_payload = await read_upload(upload)
        submission = parse_submission(kind, upload_payload)
        comparison = compare_ad_to_submission(active_ad["ad_date"], active_ad["items"], kind, submission["items"])
        report_id = database.create_report(kind, upload_payload["filename"], comparison["highlights"], comparison["groups"])
    except RoboertaAIError as exc:
        return redirect_with_status(error=str(exc))
    return redirect_with_status(message="Report generated.", focus=f"report-{report_id}")


@app.post("/reports/{report_id}/matches/{match_id}/toggle")
async def toggle_match(report_id: int, match_id: int) -> RedirectResponse:
    database.toggle_match_done(report_id, match_id)
    return redirect_with_status(focus=f"report-{report_id}")


@app.post("/reports/{report_id}/delete")
async def delete_report(report_id: int) -> RedirectResponse:
    database.delete_report(report_id)
    return redirect_with_status(message="Report deleted.")


@app.get("/reports/{report_id}/print", response_class=HTMLResponse)
async def print_report(request: Request, report_id: int) -> HTMLResponse:
    report = database.get_report(report_id)
    if report is None:
        return templates.TemplateResponse(
            request,
            "print_report.html",
            {"report": None, "group_order": database.GROUP_ORDER, "group_labels": database.GROUP_LABELS},
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "print_report.html",
        {"report": report, "group_order": database.GROUP_ORDER, "group_labels": database.GROUP_LABELS},
    )
