from __future__ import annotations

from .models import Channel, Criteria


def _contains(text: str | None, needle: str) -> bool:
    return needle.lower() in (text or "").lower()


def _access_score(channel: Channel) -> float:
    method = f"{channel.access_method} {' '.join(channel.tags)} {channel.channel_type}".lower()
    if any(term in method for term in ("clinic", "registry", "organization", "org", "association", "gatekeeper")):
        return 0.9
    if any(term in method for term in ("campus", "student", "department", "center")):
        return 0.82
    if any(term in method for term in ("moderator", "forum", "public post", "reddit", "community")):
        return 0.68
    return 0.55


def _geo_score(channel: Channel, criteria: Criteria) -> float:
    if not criteria.location:
        return 0.7
    location = criteria.location.lower()
    haystack = f"{channel.location or ''} {channel.name} {' '.join(channel.evidence)}".lower()
    if location in haystack:
        return 1.0
    if criteria.in_person and any(term in haystack for term in ("local", "clinic", "campus", "hospital", "center")):
        return 0.75
    if not criteria.in_person and any(term in haystack for term in ("online", "national", "remote", "forum")):
        return 0.82
    return 0.45 if criteria.in_person else 0.62


def _reach_score(channel: Channel) -> float:
    reach = (channel.estimated_reach or "").lower()
    evidence = " ".join(channel.evidence).lower()
    text = f"{reach} {evidence}"
    if any(term in text for term in ("10,000", "10000", "large", "national", "thousands")):
        return 0.92
    if any(term in text for term in ("1,000", "1000", "hundreds", "active")):
        return 0.78
    if any(term in text for term in ("clinic", "registry", "campus", "organization")):
        return 0.68
    return 0.5


def _population_score(channel: Channel, criteria: Criteria) -> float:
    text = f"{channel.population_fit} {channel.name} {' '.join(channel.tags)}".lower()
    population_terms = [term for term in criteria.population.lower().replace(",", " ").split() if len(term) > 3]
    topic_terms = [term for term in (criteria.study_topic or "").lower().replace(",", " ").split() if len(term) > 3]
    terms = population_terms + topic_terms
    if not terms:
        return 0.7
    hits = sum(1 for term in terms if term in text)
    return min(1.0, 0.45 + (hits / max(len(terms), 1)) * 0.55)


def rank(channels: list[Channel], criteria: Criteria) -> list[Channel]:
    ranked: list[Channel] = []
    for channel in channels:
        access = channel.access_feasibility_score or _access_score(channel)
        geo = channel.geo_fit_score or _geo_score(channel, criteria)
        reach = channel.reach_score or _reach_score(channel)
        population = _population_score(channel, criteria)
        total = (access * 0.34) + (geo * 0.24) + (reach * 0.22) + (population * 0.2)
        channel.access_feasibility_score = round(min(access, 1.0), 3)
        channel.geo_fit_score = round(min(geo, 1.0), 3)
        channel.reach_score = round(min(reach, 1.0), 3)
        channel.total_score = round(min(total, 1.0), 3)
        ranked.append(channel)
    return sorted(ranked, key=lambda item: item.total_score, reverse=True)

