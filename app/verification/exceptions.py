"""
ive defined the custom exceptions the verification system can raise
"""

class VerificationError(Exception):
  """base exception for all verification failures"""
  pass


class ProviderDown(VerificationError):
  """external provider (NPI registry, Stripe, etc) is unreachable"""
  pass


class InvalidInput(VerificationError):
  """user gave us bad data — wrong NPI format, invalid file, etc"""
  pass


class VerificationExpired(VerificationError):
  """the verification session timed out"""
  pass


class AlreadyVerified(VerificationError):
  """user is trying to verify again when they already passed"""
  pass