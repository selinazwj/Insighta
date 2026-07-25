"""Registry for provider-backed verification adapters.

No adapter is registered until it passes security and privacy review.
Self-declaration is intentionally excluded.
"""

ADAPTERS = {}


def get_adapter(tag: str):
  """
  ive made this helper to look up adapters
  raises KeyError if tag doesnt exist
  """
  return ADAPTERS[tag]
