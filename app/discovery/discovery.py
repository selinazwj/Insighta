from __future__ import annotations

import json
import os
import re
from typing import Any

from .models import Channel, Criteria, DiscoveryResult

try:
    import anthropic
except Exception:  # pragma: no cover - incomplete local installs should not break app import.
    anthropic = None


DEFAULT_MODEL = "claude-sonnet-4-5"


SYSTEM_PROMPT = """You are Insighta's internal Channel Discovery Engine.

Product rule:
Discover where target populations congregate and how to approach them through trusted gatekeepers.
Never scrape individuals, harvest personal contact information, infer private identities, or output personal contact data.

Return only JSON matching this shape:
{
  "summary": "short operator-facing summary",
  "channels": [
    {
      "name": "channel name",
      "channel_type": "clinic|org|registry|forum|campus|community|other",
      "url": "official or public URL if available",
      "location": "location or online",
      "population_fit": "why this channel matches the criteria",
      "access_method": "compliant gatekeeper/public-posting approach",
      "compliance_notes": "IRB/platform-safe notes",
      "estimated_reach": "public size/activity estimate when available",
      "evidence": ["short public evidence/source notes"],
      "tags": ["gatekeeper_outreach", "public_posting", "no_personal_data"]
    }
  ],
  "warnings": ["gaps, uncertainty, or missing exact counts"]
}

Prioritize real clinics, community orgs, registries, campus offices, advocacy groups, and moderated forums.
Do not include individual people, personal emails, personal phone numbers, or private profiles."""


def _criteria_payload(criteria: Criteria) -> dict[str, Any]:
    if hasattr(criteria, "model_dump"):
        return criteria.model_dump()
    return criteria.dict()


def _message_to_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _extract_json(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text or "", flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _offline_channels(criteria: Criteria) -> list[Channel]:
    location = criteria.location or "local/online"
    topic = criteria.study_topic or criteria.population
    return [
        Channel(
            name=f"{location} clinics and counseling centers serving {topic}",
            channel_type="clinic",
            url=None,
            location=location,
            population_fit=f"Clinical gatekeepers may already serve participants matching {criteria.population}.",
            access_method="Ask clinic administrators or research coordinators to distribute an IRB-approved flyer or referral link to eligible, opt-in patients.",
            compliance_notes="Gatekeeper outreach only; do not request patient lists or personal contact information.",
            estimated_reach="Unknown until partner confirms active patient volume.",
            evidence=["Offline fallback: configure ANTHROPIC_API_KEY to run live web discovery."],
            tags=["gatekeeper_outreach", "clinic_partner", "no_personal_data"],
        ),
        Channel(
            name=f"{location} community organizations for {criteria.population}",
            channel_type="org",
            url=None,
            location=location,
            population_fit=f"Community organizations can reach niche groups with existing trust.",
            access_method="Contact the organization, explain the study, and request newsletter, flyer, or event-board distribution.",
            compliance_notes="Use organization-approved posting; participants must self-select into the study.",
            estimated_reach="Unknown until public source lookup or partner confirmation.",
            evidence=["Offline fallback: live source discovery unavailable without ANTHROPIC_API_KEY."],
            tags=["gatekeeper_outreach", "community_org", "no_personal_data"],
        ),
        Channel(
            name=f"Moderated online forums related to {criteria.population}",
            channel_type="forum",
            url=None,
            location="online",
            population_fit=f"Public or moderated forums may include people discussing {topic}.",
            access_method="Ask moderators for permission to post a recruitment blurb or study flyer.",
            compliance_notes="Respect platform rules and moderator decisions; never scrape users or DMs.",
            estimated_reach="Unknown until platform/API lookup.",
            evidence=["Offline fallback: use official APIs or web-search integration for exact channels."],
            tags=["public_posting", "moderator_permission", "no_personal_data"],
        ),
    ]


def _normalize_result(data: dict[str, Any], criteria: Criteria, source: str) -> DiscoveryResult:
    channels: list[Channel] = []
    for raw in data.get("channels", []) or []:
        if not isinstance(raw, dict):
            continue
        try:
            channels.append(Channel(**raw))
        except Exception:
            continue
    return DiscoveryResult(
        criteria=criteria,
        channels=channels,
        summary=str(data.get("summary") or ""),
        source=source,
        warnings=[str(item) for item in data.get("warnings", []) or []],
    )


def discover(criteria: Criteria) -> DiscoveryResult:
    if anthropic is None or not os.environ.get("ANTHROPIC_API_KEY"):
        return DiscoveryResult(
            criteria=criteria,
            channels=_offline_channels(criteria),
            summary="Offline fallback result. Configure ANTHROPIC_API_KEY to enable live channel discovery.",
            source="offline_fallback",
            warnings=["ANTHROPIC_API_KEY is not configured, so channels are examples rather than live discoveries."],
        )

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    try:
        message = client.messages.create(
            model=os.environ.get("CHANNEL_DISCOVERY_MODEL", DEFAULT_MODEL),
            max_tokens=int(os.environ.get("CHANNEL_DISCOVERY_MAX_TOKENS", "2600")),
            temperature=0.1,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Find ranked recruitment channels for this research study. "
                        "Return compliant gatekeeper/public-posting options only.\n\n"
                        + json.dumps(_criteria_payload(criteria), ensure_ascii=False)
                    ),
                }
            ],
        )
    except Exception as exc:  # pragma: no cover - external API dependent.
        return DiscoveryResult(
            criteria=criteria,
            channels=_offline_channels(criteria),
            summary="Live discovery failed; returned offline fallback channels.",
            source="offline_fallback",
            warnings=[f"Anthropic discovery failed: {exc}"],
        )

    text = _message_to_text(message)
    data = _extract_json(text)
    if not data:
        return DiscoveryResult(
            criteria=criteria,
            channels=_offline_channels(criteria),
            summary="Live discovery did not return valid JSON; returned offline fallback channels.",
            source="offline_fallback",
            warnings=["Claude response was not valid JSON."],
        )
    result = _normalize_result(data, criteria, source="anthropic_web_search")
    if not result.channels:
        result.channels = _offline_channels(criteria)
        result.warnings.append("Live discovery returned no valid channels; offline fallback channels included.")
    return result

