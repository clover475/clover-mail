"""Interpret a natural-language question, then retrieve mail locally."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from .archive import Archive
from .apple_mail import parsedate_utc
from .mimo import MiMoConfig, MiMoError, _request, parse_json_object

QUERY_PROMPT = """把用户的邮件查询转成检索条件。只返回 JSON，不回答问题，不猜测邮件内容：
{"entity_terms":["发件人/机构/产品名称的原文或常见写法"],"topic_terms":["主题关键词及中英同义词"],"since_days":整数或null,"open_only":布尔值}
每组最多 5 个短词。最近两周=14 天，最近一个月=30 天。询问未完成或待处理事项时 open_only=true。不要把『最近』『哪些』『有没有』『邮件』当检索词。"""

STATUS_WORDS = {"待处理", "未完成", "还没完成", "还没有完成", "没有完成", "待跟进", "待办", "需处理", "需要处理", "未处理", "行动项", "to-do", "todo", "unfinished", "pending", "open"}
TOPIC_CUES = ("比赛", "竞赛", "注册", "报名", "付款", "支付", "活动", "课程", "考试", "会议", "求职", "招聘", "实习", "认证", "证书", "截止", "Newsletter")


def _asks_open(question: str) -> bool:
    lowered = question.casefold()
    return any(word in question for word in STATUS_WORDS if not word.isascii()) or any(
        re.search(r"\b" + re.escape(word) + r"\b", lowered)
        for word in STATUS_WORDS if word.isascii()
    )


def _fallback_plan(question: str) -> dict:
    lowered = question.casefold()
    if "最近两周" in question:
        days = 14
    elif "最近一个月" in question or "最近一月" in question:
        days = 30
    elif "最近一周" in question:
        days = 7
    else:
        days = None
    latin = re.findall(r"[A-Za-z][A-Za-z0-9.+-]*(?:\s+[A-Za-z][A-Za-z0-9.+-]*)*", question)
    entities = [term.strip() for term in latin if term.strip().casefold() not in {"newsletter"}][:5]
    topics = [cue for cue in TOPIC_CUES if cue.casefold() in lowered][:5]
    if not entities and not topics:
        residual = re.sub(r"最近(?:一个月|一月|两周|一周)|有哪些|有没有|相关|邮件|帮我找|帮我查|之前|什么|哪些|需要|但是|还没有|完成|的|了|我|吗|？|\?", "", question).strip()
        if len(residual) >= 2:
            topics = [residual[:20]]
    return {
        "entity_terms": entities, "topic_terms": topics, "since_days": days,
        "open_only": _asks_open(question),
        "_local_fallback": True,
    }


def _plan(question: str, config: MiMoConfig) -> tuple[dict, dict]:
    try:
        text, usage = _request(config, question, system_prompt=QUERY_PROMPT)
        plan = parse_json_object(text, context="query plan")
    except MiMoError:
        return _fallback_plan(question), {}
    for key in ("entity_terms", "topic_terms"):
        terms = plan.get(key)
        if not isinstance(terms, list) or len(terms) > 5 or any(not isinstance(term, str) or not term.strip() for term in terms):
            return _fallback_plan(question), usage
    days = plan.get("since_days")
    if days is not None and (not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 3650):
        return _fallback_plan(question), usage
    if not isinstance(plan.get("open_only"), bool):
        plan["open_only"] = _fallback_plan(question)["open_only"]
    return plan, usage


def ask(archive: Archive, question: str, *, config: MiMoConfig, limit: int = 20) -> dict:
    if not question.strip():
        raise ValueError("question must not be empty")
    if not 1 <= limit <= 100:
        raise ValueError("query limit must be between 1 and 100")
    plan, usage = _plan(question, config)
    days = plan["since_days"]
    entities = plan["entity_terms"]
    # Status words describe a filter, not mail subject matter. When no concrete
    # topic remains, retrieve recent analyzed mail and filter its action items.
    topics = [term for term in plan["topic_terms"] if term.strip().casefold() not in STATUS_WORDS]
    by_id: dict[int, dict] = {}
    entity_ids: set[int] = set()
    topic_ids: set[int] = set()
    hit_count: dict[int, int] = {}
    for term in entities:
        for item in archive.search(term, days=days, limit=100):
            by_id[item["id"]] = item
            entity_ids.add(item["id"])
            hit_count[item["id"]] = hit_count.get(item["id"], 0) + 1
    for term in topics:
        for item in archive.search(term, days=days, limit=100):
            by_id[item["id"]] = item
            topic_ids.add(item["id"])
            hit_count[item["id"]] = hit_count.get(item["id"], 0) + 1
    if entities and topics:
        # A model can put a broad topic or an exact product name in the wrong
        # bucket. Keep topic matches when its strict intersection is empty.
        selected_ids = (entity_ids & topic_ids) or topic_ids or entity_ids
    elif entities:
        selected_ids = entity_ids
    elif topics:
        selected_ids = topic_ids
    else:
        for item in archive.recent_analyses(days=days, limit=100):
            by_id[item["id"]] = item
        selected_ids = set(by_id)
    matches = sorted(
        (by_id[ident] for ident in selected_ids),
        key=lambda item: (hit_count.get(item["id"], 0),
                          (parsedate_utc(item["date"]) if item["date"] else None)
                          or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    if plan["open_only"]:
        matches = [item for item in matches if item["status"] not in {"已完成", "忽略"} and item["action_items"]]
    return {"question": question, "plan": plan, "matches": matches[:limit], "usage": usage}
