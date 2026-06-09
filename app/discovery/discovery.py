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
      "url": "official/public page URL",
      "contact_url": "best URL for compliant outreach, moderator contact, org contact, or posting rules",
      "location": "location or online",
      "population_fit": "why this channel matches the criteria",
      "access_method": "compliant gatekeeper/public-posting approach",
      "compliant_contact": "short table-ready contact method, e.g. org partnership, clinic referral, moderator-approved opt-in post",
      "compliance_notes": "IRB/platform-safe notes",
      "estimated_reach": "public size/activity estimate when available",
      "scale_activity": "member count, rating, event cadence, clinic/group volume, or directory breadth",
      "local_fit": "table-ready fit for local/in-person criteria, e.g. Boston in-person group, online but not geo-concentrated",
      "evidence": ["short public evidence/source notes"],
      "tags": ["gatekeeper_outreach", "public_posting", "no_personal_data"]
    }
  ],
  "warnings": ["gaps, uncertainty, or missing exact counts"]
}

Prioritize real clinics, community orgs, registries, campus offices, advocacy groups, and moderated forums.
For every channel, include a concrete official or public URL when one exists.
Format the result so operators can render it as a table with columns: channel, type, scale_activity, local_fit, compliant_contact, URL.
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
    text = f"{criteria.population} {criteria.location or ''} {criteria.study_topic or ''} {criteria.notes or ''}".lower()
    if ("boston" in text or "波士顿" in text) and "anxiety" in text:
        return [
            Channel(
                name="DBSA Boston",
                channel_type="local organization",
                url="https://www.dbsalliance.org/support/chapters-and-support-groups/find-a-support-group/",
                contact_url="https://www.dbsalliance.org/support/chapters-and-support-groups/find-a-support-group/",
                location="Boston / Belmont, MA",
                population_fit="Peer support community for mood disorders; relevant as a trusted mental-health gatekeeper for anxiety-adjacent recruitment.",
                access_method="Ask the chapter or hosting site about distributing an IRB-approved flyer to opt-in group members.",
                compliant_contact="DBSA organization partnership",
                compliance_notes="Approach chapter/group organizers only; do not ask for attendee rosters or patient/member contacts.",
                estimated_reach="Local peer-support groups; public locator confirms face-to-face/online support group model.",
                scale_activity="Local organization with recurring support groups",
                local_fit="★★★★★ Boston-area in-person fit if the local chapter/group confirms current meeting location",
                evidence=["DBSA official support-group locator", "McLean/Cole Resource Center history suggests Boston-area peer-support relevance"],
                tags=["gatekeeper_outreach", "local_org", "no_personal_data"],
                access_feasibility_score=0.95,
                geo_fit_score=1.0,
                reach_score=0.72,
            ),
            Channel(
                name="Skip the Small Talk Boston",
                channel_type="local Meetup",
                url="https://www.meetup.com/skip-the-small-talk-boston/",
                contact_url="https://www.meetup.com/skip-the-small-talk-boston/",
                location="Boston, MA",
                population_fit="Local events attract people seeking structured, psychologically safe social interaction.",
                access_method="Contact organizers through Meetup or official event channels; request permission for an opt-in flyer or partner post.",
                compliant_contact="Organizer partnership",
                compliance_notes="Use organizer-approved posting only; do not message individual members.",
                estimated_reach="4,466 Meetup members; rating 4.5; recurring Boston events with typical 30-40 attendance notes.",
                scale_activity="4.5 rating, 4,466 members, multiple recurring events",
                local_fit="★★★★★ Local, in-person Boston events",
                evidence=["Meetup page lists Boston, 4.5 rating, 4,466 members", "Event notes describe typical attendance around 30-40"],
                tags=["organizer_outreach", "public_event", "no_personal_data"],
                access_feasibility_score=0.9,
                geo_fit_score=1.0,
                reach_score=0.86,
            ),
            Channel(
                name="Speak Up Cambridge / Speak With Confidence Boston",
                channel_type="local Meetup",
                url="https://www.meetup.com/find/us--ma--boston/social-anxiety/",
                contact_url="https://www.meetup.com/find/us--ma--boston/social-anxiety/",
                location="Cambridge / Boston, MA",
                population_fit="Public-speaking and social-confidence events overlap with social anxiety and communication-confidence populations.",
                access_method="Contact Meetup organizers and ask for a study flyer or brief announcement approved by the host.",
                compliant_contact="Meetup organizer outreach",
                compliance_notes="Recruit through organizer-approved opt-in posting; avoid direct member scraping or DMs.",
                estimated_reach="Meetup search shows Speak With Confidence Boston and Speak Up Cambridge events with visible attendees/ratings.",
                scale_activity="Meetup ratings around 4.4-4.9; single events around 17-21 attendees in public listings",
                local_fit="★★★★★ Boston/Cambridge in-person events",
                evidence=["Meetup Boston social-anxiety search lists Speak With Confidence Boston and Speak Up Cambridge events"],
                tags=["organizer_outreach", "local_meetup", "no_personal_data"],
                access_feasibility_score=0.88,
                geo_fit_score=1.0,
                reach_score=0.72,
            ),
            Channel(
                name="Boston social anxiety Meetup groups",
                channel_type="local Meetup directory",
                url="https://www.meetup.com/find/us--ma--boston/social-anxiety/",
                contact_url="https://www.meetup.com/find/us--ma--boston/social-anxiety/",
                location="Boston, MA / online",
                population_fit="Directory captures nearby social anxiety, communication, support, and confidence-building groups.",
                access_method="Screen groups, then contact organizers for approved opt-in posting or partnership.",
                compliant_contact="Organizer outreach after group screening",
                compliance_notes="Respect each group rules; no scraping members; no individual outreach unless a person opts in.",
                estimated_reach="Multiple public Meetup events and groups; some are local, some online/out-of-area.",
                scale_activity="Multiple groups/events; visible ratings and attendee counts vary by listing",
                local_fit="★★★★☆ Local candidates exist, but each listing needs manual screening",
                evidence=["Meetup search page lists social anxiety events near Boston within 18 miles"],
                tags=["organizer_outreach", "directory_screening", "no_personal_data"],
                access_feasibility_score=0.86,
                geo_fit_score=0.92,
                reach_score=0.76,
            ),
            Channel(
                name="Psychology Today Boston anxiety groups",
                channel_type="clinic / therapist directory",
                url="https://www.psychologytoday.com/us/groups/ma/boston?category=anxiety",
                contact_url="https://www.psychologytoday.com/us/groups/ma/boston?category=anxiety",
                location="Boston, MA",
                population_fit="Therapy and support-group providers are high-trust gatekeepers for anxiety-related populations.",
                access_method="Contact listed group practices/clinics for referral partnership or approved flyer distribution.",
                compliant_contact="Clinic partnership referral",
                compliance_notes="Ask providers to share study information with eligible people who can opt in; never request patient lists.",
                estimated_reach="Boston-area directory with many anxiety group listings.",
                scale_activity="Large local provider directory for anxiety groups",
                local_fit="★★★★☆ Strong local precision, but each clinic requires manual partnership outreach",
                evidence=["Psychology Today category page for Boston anxiety groups"],
                tags=["clinic_partner", "directory_screening", "no_personal_data"],
                access_feasibility_score=0.82,
                geo_fit_score=0.95,
                reach_score=0.82,
            ),
            Channel(
                name="ADAA community resources",
                channel_type="national advocacy organization",
                url="https://adaa.org/find-help/support/community-resources",
                contact_url="https://adaa.org/find-help/support/community-resources",
                location="National / online",
                population_fit="Anxiety and Depression Association of America is highly relevant but not Boston-specific.",
                access_method="Use ADAA resources to identify compliant support/community channels; ask any local partner for approved sharing.",
                compliant_contact="Institutional partnership / local member screening",
                compliance_notes="Use as a gatekeeper/resource layer; do not harvest community members.",
                estimated_reach="National anxiety/depression advocacy resource.",
                scale_activity="National advocacy/resource hub",
                local_fit="★★★☆☆ Relevant but needs Boston filtering",
                evidence=["ADAA official community resources page"],
                tags=["advocacy_org", "resource_screening", "no_personal_data"],
                access_feasibility_score=0.72,
                geo_fit_score=0.48,
                reach_score=0.88,
            ),
            Channel(
                name="r/socialanxiety and r/Anxiety",
                channel_type="national online forum",
                url="https://www.reddit.com/r/socialanxiety/",
                contact_url="https://www.reddit.com/r/Anxiety/",
                location="Online / national",
                population_fit="Large anxiety-related communities, but weak for Boston in-person recruitment.",
                access_method="Review subreddit rules and ask moderators before posting an opt-in recruitment post.",
                compliant_contact="Moderator-approved opt-in post",
                compliance_notes="No scraping usernames, no unsolicited DMs, no personal-data harvesting.",
                estimated_reach="Large active Reddit communities; geography is not concentrated in Boston.",
                scale_activity="Large active subreddits; exact current size should be checked via Reddit API",
                local_fit="★★☆☆☆ Online and not Boston-concentrated",
                evidence=["Public subreddit pages for r/socialanxiety and r/Anxiety"],
                tags=["moderator_permission", "public_posting", "no_personal_data"],
                access_feasibility_score=0.64,
                geo_fit_score=0.32,
                reach_score=0.92,
            ),
        ]
    return [
        Channel(
            name=f"{location} clinics and counseling centers serving {topic}",
            channel_type="clinic",
            url=None,
            contact_url=None,
            location=location,
            population_fit=f"Clinical gatekeepers may already serve participants matching {criteria.population}.",
            access_method="Ask clinic administrators or research coordinators to distribute an IRB-approved flyer or referral link to eligible, opt-in patients.",
            compliant_contact="Clinic partnership referral",
            compliance_notes="Gatekeeper outreach only; do not request patient lists or personal contact information.",
            estimated_reach="Unknown until partner confirms active patient volume.",
            scale_activity="Unknown until partner confirms active volume",
            local_fit=f"Likely local fit for {location}" if criteria.in_person else "Can support local or remote recruitment",
            evidence=["Offline fallback: configure ANTHROPIC_API_KEY to run live web discovery."],
            tags=["gatekeeper_outreach", "clinic_partner", "no_personal_data"],
        ),
        Channel(
            name=f"{location} community organizations for {criteria.population}",
            channel_type="org",
            url=None,
            contact_url=None,
            location=location,
            population_fit=f"Community organizations can reach niche groups with existing trust.",
            access_method="Contact the organization, explain the study, and request newsletter, flyer, or event-board distribution.",
            compliant_contact="Organization partnership",
            compliance_notes="Use organization-approved posting; participants must self-select into the study.",
            estimated_reach="Unknown until public source lookup or partner confirmation.",
            scale_activity="Unknown until public source lookup",
            local_fit=f"Likely local fit for {location}" if criteria.in_person else "Can support local or remote recruitment",
            evidence=["Offline fallback: live source discovery unavailable without ANTHROPIC_API_KEY."],
            tags=["gatekeeper_outreach", "community_org", "no_personal_data"],
        ),
        Channel(
            name=f"Moderated online forums related to {criteria.population}",
            channel_type="forum",
            url=None,
            contact_url=None,
            location="online",
            population_fit=f"Public or moderated forums may include people discussing {topic}.",
            access_method="Ask moderators for permission to post a recruitment blurb or study flyer.",
            compliant_contact="Moderator-approved opt-in post",
            compliance_notes="Respect platform rules and moderator decisions; never scrape users or DMs.",
            estimated_reach="Unknown until platform/API lookup.",
            scale_activity="Unknown until platform/API lookup",
            local_fit="Weak for in-person recruitment unless a local forum exists" if criteria.in_person else "Good for remote recruitment",
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
