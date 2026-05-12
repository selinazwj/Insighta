"""
ive registered all adapters here
this is the single source of truth for which adapters exist
"""
from app.verification.adapters.self_declared import SelfDeclaredAdapter

# the registry — occupation_tag maps to adapter instance
ADAPTERS = {
  "self_declared": SelfDeclaredAdapter(),
  # when you write more adapters, add them here:
  # "physician": NPIRegistryAdapter(),
  # "student": EduEmailAdapter(),
  # "lawyer": BarDirectoryAdapter(),
}


def get_adapter(tag: str):
  """
  ive made this helper to look up adapters
  raises KeyError if tag doesnt exist
  """
  return ADAPTERS[tag]