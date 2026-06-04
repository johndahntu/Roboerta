from __future__ import annotations

import base64
import io
import json
from typing import Any

from openai import OpenAI
from openpyxl import load_workbook
from pypdf import PdfReader

from app.config import OPENAI_API_KEY, OPENAI_MODEL
from app.database import GROUP_ORDER


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
ALLOWED_TEXT_TYPES = {"text/plain", "text/csv", "application/json"}
PDF_TYPE = "application/pdf"
EXCEL_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}


class RoboertaAIError(RuntimeError):
    pass


def _get_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RoboertaAIError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=OPENAI_API_KEY)


def _parse_json_output(output_text: str) -> dict[str, Any]:
    candidate = output_text.strip()
    if candidate.startswith("```"):
        parts = candidate.split("```")
        candidate = next((part for part in parts if "{" in part and "}" in part), candidate)
        candidate = candidate.replace("json", "", 1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise RoboertaAIError(f"OpenAI returned invalid JSON: {candidate[:400]}") from exc


def _extract_excel_text(content: bytes) -> str:
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    parts: list[str] = []
    for sheet in workbook.worksheets:
        parts.append(f"# Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            values = [str(value).strip() for value in row if value not in (None, "")]
            if values:
                parts.append(" | ".join(values))
    return "\n".join(parts)


def _extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        if page_text:
            pages.append(f"# Page {index}\n{page_text}")
    return "\n\n".join(pages)


def _build_content_parts(client: OpenAI, uploads: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    parts: list[dict[str, Any]] = []
    temporary_file_ids: list[str] = []
    for upload in uploads:
        content = upload["content"]
        content_type = upload["content_type"] or "application/octet-stream"
        filename = upload["filename"]
        if content_type in ALLOWED_IMAGE_TYPES:
            encoded = base64.b64encode(content).decode("utf-8")
            parts.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{content_type};base64,{encoded}",
                }
            )
            parts.append({"type": "input_text", "text": f"Filename: {filename}"})
            continue
        if content_type == PDF_TYPE:
            uploaded = client.files.create(file=(filename, content, content_type), purpose="user_data")
            temporary_file_ids.append(uploaded.id)
            parts.append({"type": "input_file", "file_id": uploaded.id})
            parts.append({"type": "input_text", "text": f"Filename: {filename}"})
            pdf_text = _extract_pdf_text(content)
            if pdf_text:
                parts.append({"type": "input_text", "text": f"Extracted PDF text:\n{pdf_text[:50000]}"})
            continue
        if content_type in EXCEL_TYPES:
            excel_text = _extract_excel_text(content)
            parts.append({"type": "input_text", "text": f"Filename: {filename}\n{excel_text[:50000]}"})
            continue
        if content_type in ALLOWED_TEXT_TYPES:
            text = content.decode("utf-8", errors="ignore")
            parts.append({"type": "input_text", "text": f"Filename: {filename}\n{text[:50000]}"})
            continue
        raise RoboertaAIError(f"Unsupported file type: {filename} ({content_type})")
    return parts, temporary_file_ids


def _run_json_request(system_prompt: str, instruction: str, uploads: list[dict[str, Any]]) -> dict[str, Any]:
    client = _get_client()
    parts, file_ids = _build_content_parts(client, uploads)
    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": parts + [{"type": "input_text", "text": instruction}]},
            ],
        )
        return _parse_json_output(response.output_text)
    finally:
        for file_id in file_ids:
            try:
                client.files.delete(file_id)
            except Exception:
                continue


def parse_weekly_ad(uploads: list[dict[str, Any]]) -> dict[str, Any]:
    system_prompt = """
You extract structured weekly grocery ad data.
Return strict JSON only with this shape:
{
  "ad_date": "YYYY-MM-DD or empty string",
  "items": [
    {
      "name": "string",
      "price_text": "string",
      "page_number": 1,
      "tags": ["front_page_items", "price_lock_items", "just_4_u_items", "five_friday_items", "member_price_items", "regular_items"],
      "notes": "string"
    }
  ]
}
Rules:
- Include one entry per ad item.
- Use front_page_items when the item appears on the front page.
- Use just_4_u_items for clip-or-click or just-for-u offers.
- Use five_friday_items for $5 Friday items.
- Use member_price_items for member price items.
- Use price_lock_items for price lock items.
- Include regular_items for any item that does not fit the special tags.
- Tags may contain multiple values when applicable.
- Keep names concise and normalized to what a store employee would recognize.
""".strip()
    instruction = "Parse these weekly ad files and return the JSON structure exactly."
    payload = _run_json_request(system_prompt, instruction, uploads)
    items = payload.get("items", [])
    if not items:
        raise RoboertaAIError("No weekly ad items were extracted from the uploaded files.")
    for item in items:
        item.setdefault("price_text", "")
        item.setdefault("page_number", None)
        item.setdefault("tags", ["regular_items"])
        item.setdefault("notes", "")
    return payload


def parse_submission(kind: str, upload: dict[str, Any]) -> dict[str, Any]:
    source_instruction = "photo of products or tags" if kind == "photo" else "planner or display section document"
    system_prompt = f"""
You extract shopping items from a {source_instruction}.
Return strict JSON only with this shape:
{{
  "items": [
    {{
      "item_name": "string",
      "source_section": "string",
      "notes": "string"
    }}
  ]
}}
Rules:
- For planner documents, preserve the display section name in source_section when present.
- For photos, use source_section only when the image clearly shows a department or display section.
- Do not invent items that are not visible in the input.
""".strip()
    instruction = f"Extract all relevant items from this {kind} and return the JSON structure exactly."
    payload = _run_json_request(system_prompt, instruction, [upload])
    items = payload.get("items", [])
    if not items:
        raise RoboertaAIError("No items were extracted from the submitted file.")
    for item in items:
        item.setdefault("source_section", "")
        item.setdefault("notes", "")
    return payload


def compare_ad_to_submission(
    ad_date: str,
    ad_items: list[dict[str, Any]],
    submission_kind: str,
    submission_items: list[dict[str, Any]],
) -> dict[str, Any]:
    ad_payload = [
        {
            "name": item["name"],
            "price_text": item.get("price_text", ""),
            "page_number": item.get("page_number"),
            "tags": item.get("tags", []),
            "notes": item.get("notes", ""),
        }
        for item in ad_items
    ]
    system_prompt = f"""
You compare a weekly grocery ad against a submitted {submission_kind}.
Return strict JSON only with this shape:
{{
  "highlights": ["string"],
  "groups": {{
    "front_page_items": [{{"item_name": "string", "matched_ad_name": "string", "ad_price": "string", "source_section": "string", "notes": "string"}}],
    "price_lock_items": [],
    "just_4_u_items": [],
    "five_friday_items": [],
    "member_price_items": [],
    "regular_items": []
  }}
}}
Rules:
- Group matches using these exact keys: {', '.join(GROUP_ORDER)}.
- A submitted item can appear in multiple groups only if the ad entry clearly belongs to multiple tags.
- Keep source_section from the submitted item when available.
- Only include strong matches.
- highlights should be short, factual, and useful.
""".strip()
    instruction = (
        f"Weekly ad date: {ad_date}\n"
        f"Weekly ad items JSON:\n{json.dumps(ad_payload, indent=2)}\n\n"
        f"Submitted items JSON:\n{json.dumps(submission_items, indent=2)}\n\n"
        "Compare these sets and return the JSON structure exactly."
    )
    payload = _run_json_request(system_prompt, instruction, [])
    groups = payload.get("groups", {})
    normalized_groups = {group_name: groups.get(group_name, []) for group_name in GROUP_ORDER}
    return {
        "highlights": payload.get("highlights", []),
        "groups": normalized_groups,
    }
