"""Internal channel discovery engine.

This package is intentionally additive and does not touch survey, matching, or
payment flows. It exposes a fixed DiscoveryResult contract so the discovery
method can evolve without rewriting ranking, storage, or future UI.
"""

