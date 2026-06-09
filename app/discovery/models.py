from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Criteria(BaseModel):
    population: str = Field(..., min_length=2, description="Target participant population")
    location: Optional[str] = Field(None, description="City, region, country, or radius phrase")
    in_person: bool = Field(False, description="Whether the study requires in-person participation")
    age_min: Optional[int] = Field(None, ge=0, le=120)
    age_max: Optional[int] = Field(None, ge=0, le=120)
    sample_size: Optional[int] = Field(None, ge=1)
    study_topic: Optional[str] = Field(None, description="Research topic or condition")
    notes: Optional[str] = Field(None, description="Additional niche recruitment constraints")


class Channel(BaseModel):
    name: str
    channel_type: str = Field(..., description="clinic, org, registry, forum, campus, community, etc.")
    url: Optional[str] = None
    contact_url: Optional[str] = None
    location: Optional[str] = None
    population_fit: str
    access_method: str = Field(..., description="How Insighta should approach the gatekeeper or public channel")
    compliant_contact: Optional[str] = Field(None, description="Short operator-facing compliant contact method")
    compliance_notes: str
    estimated_reach: Optional[str] = None
    scale_activity: Optional[str] = Field(None, description="Public size, member count, cadence, ratings, or activity signal")
    local_fit: Optional[str] = Field(None, description="Fit for the requested local/in-person criteria")
    evidence: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    access_feasibility_score: float = Field(0.0, ge=0.0, le=1.0)
    geo_fit_score: float = Field(0.0, ge=0.0, le=1.0)
    reach_score: float = Field(0.0, ge=0.0, le=1.0)
    total_score: float = Field(0.0, ge=0.0, le=1.0)


class DiscoveryResult(BaseModel):
    criteria: Criteria
    channels: List[Channel] = Field(default_factory=list)
    summary: str = ""
    compliance_rule: str = (
        "Discover public community metadata and trusted gatekeepers only. "
        "Never scrape individuals, harvest personal contact information, or output private personal data."
    )
    source: str = "offline"
    warnings: List[str] = Field(default_factory=list)
