"""Run bounded, idempotent MiMo analysis over the private local archive."""

from __future__ import annotations

import json
import hashlib
import os
from dataclasses import replace

from .archive import Archive
from .content import MAX_IMAGE_BYTES, MAX_IMAGES, extract_content
from .mimo import MiMoConfig, MiMoError, _request, _translation_chunks, analyze_email
from .remote_images import fetch_selected

PROMPT_VERSION = 2
MAX_BODY_CHARACTERS = 120_000
SINGLE_PASS_CHARACTERS = 3_500
LONG_SUMMARY_PROMPT = """把以下同一封邮件各段的中文摘要、事实和待办合并成一段简洁中文摘要。只根据提供内容，不猜测；不要遗漏明确的重要事项、日期或报名要求。只输出摘要正文。"""


def _analyze_section(*, sender: str, subject: str, date: str, body: str,
                     images: tuple, config: MiMoConfig, depth: int = 0) -> dict:
    try:
        return analyze_email(sender=sender, subject=subject, date=date, body=body,
                             images=images, config=config)
    except MiMoError as exc:
        if (depth >= 2 or len(body) < 1_200 or
                not any(hint in str(exc) for hint in ("not valid JSON", "truncated"))):
            raise
    midpoint = len(body) // 2
    boundary = body.rfind("\n", 0, midpoint)
    if boundary < midpoint // 2:
        boundary = midpoint
    left = _analyze_section(sender=sender, subject=subject, date=date,
                            body=body[:boundary], images=images, config=config, depth=depth + 1)
    right = _analyze_section(sender=sender, subject=subject, date=date,
                             body=body[boundary:], images=(), config=config, depth=depth + 1)
    return {
        "useful": left["useful"] or right["useful"],
        "title_zh": left["title_zh"] or right["title_zh"],
        "summary_zh": "\n".join((left["summary_zh"], right["summary_zh"])),
        "translation_zh": "\n\n".join((left["translation_zh"], right["translation_zh"])),
        "action_required": left["action_required"] or right["action_required"],
        "action_items": left["action_items"] + right["action_items"],
        "important_facts": left["important_facts"] + right["important_facts"],
        "_usage": {"total_tokens": int(left.get("_usage", {}).get("total_tokens", 0))
                   + int(right.get("_usage", {}).get("total_tokens", 0))},
        "_translation_usages": left.get("_translation_usages", [])
        + right.get("_translation_usages", []),
    }


def _analyze_long_email(*, sender: str, subject: str, date: str, body: str,
                        images: tuple, config: MiMoConfig, archive: Archive | None = None,
                        message_id: int | None = None) -> dict:
    chunks = _translation_chunks(body)
    chunk_config = replace(config, max_tokens=max(config.max_tokens, 8192))
    results: list[dict] = []
    for index, chunk in enumerate(chunks):
        digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        cached = archive.get_analysis_chunk(
            message_id=message_id, model=config.model, prompt_version=PROMPT_VERSION,
            chunk_index=index, chunk_sha256=digest,
        ) if archive is not None and message_id is not None else None
        if cached is None:
            cached = _analyze_section(sender=sender, subject=subject, date=date, body=chunk,
                                      images=images if index == 0 else (), config=chunk_config)
            if archive is not None and message_id is not None:
                archive.save_analysis_chunk(
                    message_id=message_id, model=config.model, prompt_version=PROMPT_VERSION,
                    chunk_index=index, chunk_sha256=digest, result=cached,
                )
        results.append(cached)
    title = next((item["title_zh"] for item in results if item["useful"] and item["title_zh"]), results[0]["title_zh"])
    action_items: list[dict] = []
    action_keys: set[tuple[str, str | None]] = set()
    facts: list[str] = []
    fact_keys: set[str] = set()
    for item in results:
        for action in item["action_items"]:
            key = (action["action"].strip().casefold(), action.get("deadline_iso"))
            if key not in action_keys:
                action_items.append(action)
                action_keys.add(key)
        for fact in item["important_facts"]:
            key = fact.strip().casefold()
            if key and key not in fact_keys:
                facts.append(fact)
                fact_keys.add(key)
    condensed = [{"summary_zh": item["summary_zh"], "action_items": item["action_items"],
                  "important_facts": item["important_facts"][:8]} for item in results]
    summary, summary_usage = _request(config, json.dumps(condensed, ensure_ascii=False),
                                      system_prompt=LONG_SUMMARY_PROMPT)
    translations = [item["translation_zh"].strip() for item in results]
    if any(not translated for translated in translations):
        raise MiMoError("MiMo returned an empty chunk translation")
    usages = [item.get("_usage", {}) for item in results]
    for item in results:
        usages.extend(item.get("_translation_usages", []))
    usages.append(summary_usage)
    return {
        "useful": any(item["useful"] for item in results),
        "title_zh": title,
        "summary_zh": summary,
        "translation_zh": "\n\n".join(translations),
        "action_required": bool(action_items) or any(item["action_required"] for item in results),
        "action_items": action_items,
        "important_facts": facts,
        "_chunk_count": len(chunks),
        "_usage": {"total_tokens": sum(int(usage.get("total_tokens", 0)) for usage in usages)},
        "_chunk_usages": usages,
    }


def analyze_pending(archive: Archive, *, config: MiMoConfig, limit: int = 5, dry_run: bool = False) -> dict[str, object]:
    if not 1 <= limit <= 100:
        raise ValueError("analysis limit must be between 1 and 100")
    rows = archive.pending_analysis(model=config.model, prompt_version=PROMPT_VERSION, limit=limit)
    analyzed = 0
    image_count = 0
    remote_count = 0
    remote_candidate_count = 0
    errors: list[str] = []
    for message_id, raw in rows:
        extracted = extract_content(raw)
        remote_candidate_count += len(extracted.remote_candidates)
        remote_mode = os.environ.get("CLOVER_MAIL_REMOTE_IMAGES") == "selective"
        if not extracted.text and not extracted.images and not (remote_mode and extracted.remote_candidates):
            errors.append(f"message {message_id}: no usable text or local image")
            if not dry_run:
                archive.mark_analysis_failure(message_id=message_id, model=config.model,
                                              prompt_version=PROMPT_VERSION)
            continue
        if len(extracted.text) > MAX_BODY_CHARACTERS:
            errors.append(f"message {message_id}: body exceeds complete-translation limit")
            if not dry_run:
                archive.mark_analysis_failure(message_id=message_id, model=config.model,
                                              prompt_version=PROMPT_VERSION)
            continue
        if dry_run:
            image_count += len(extracted.images)
            remote_count += extracted.remote_image_count
            continue
        selected_remote = ()
        if remote_mode and extracted.remote_candidates:
            selected_remote = fetch_selected(
                extracted.remote_candidates,
                max_count=min(2, MAX_IMAGES - len(extracted.images)),
                max_bytes=MAX_IMAGE_BYTES - sum(len(image.data) for image in extracted.images),
            )
        images = extracted.images + selected_remote
        if not extracted.text and not images:
            errors.append(f"message {message_id}: no usable text or selected image")
            archive.mark_analysis_failure(message_id=message_id, model=config.model,
                                          prompt_version=PROMPT_VERSION)
            continue
        image_count += len(images)
        remote_count += max(0, extracted.remote_image_count - len(selected_remote))
        try:
            if len(extracted.text) > SINGLE_PASS_CHARACTERS:
                result = _analyze_long_email(
                    sender=extracted.sender, subject=extracted.subject, date=extracted.date,
                    body=extracted.text, images=images, config=config,
                    archive=archive, message_id=message_id,
                )
            else:
                result = analyze_email(
                    sender=extracted.sender,
                    subject=extracted.subject,
                    date=extracted.date,
                    body=extracted.text,
                    images=images,
                    config=config,
                )
        except MiMoError as exc:
            errors.append(f"message {message_id}: {exc}")
            archive.mark_analysis_failure(message_id=message_id, model=config.model,
                                          prompt_version=PROMPT_VERSION)
            continue
        result["_source_images"] = {
            "selected_local": len(extracted.images),
            "selected_remote": len(selected_remote),
            "remote_not_loaded": max(0, extracted.remote_image_count - len(selected_remote)),
        }
        archive.save_analysis(
            message_id=message_id,
            model=config.model,
            prompt_version=PROMPT_VERSION,
            result=result,
            selected_images=len(images),
        )
        analyzed += 1
    return {
        "considered": len(rows),
        "analyzed": analyzed,
        "selected_local_images": image_count,
        "remote_images_not_loaded": remote_count,
        "remote_candidates": remote_candidate_count,
        "errors": errors,
        "dry_run": dry_run,
    }
