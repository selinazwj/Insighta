"""
ive written the resolver that picks the right adapter for a user
"""
from app.verification.adapters import get_adapter


def resolve_adapter(user):
  """
  looks at the users occupation_tag and returns the right adapter
  for now everyone gets self_declared, but later this will branch:
  - physician -> NPI adapter
  - student -> edu email adapter
  - lawyer -> bar directory adapter
  """
  
  # get the occupation tag, default to self_declared if not set
  tag = getattr(user, 'occupation_tag', None) or 'self_declared'
  
  # for now, force everyone to self_declared until we write real adapters
  tag = 'self_declared'
  
  return get_adapter(tag)