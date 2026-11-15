"""Researcher wallet records; participant earnings remain a separate account."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Boolean, CheckConstraint, UniqueConstraint, event
from app.models import Base


class WalletAccount(Base):
    __tablename__ = 'wallet_accounts'
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    blocked = Column(Boolean, nullable=False, default=False)
    billing_profile = Column(JSON, nullable=True)


class WalletLedger(Base):
    __tablename__ = 'wallet_ledger'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    delta_cents = Column(Integer, nullable=False)
    currency = Column(String, nullable=False, default='usd')
    kind = Column(String, nullable=False)
    reference = Column(String, nullable=False, unique=True)
    description = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (CheckConstraint('delta_cents != 0', name='wallet_nonzero_entry'),)


class WalletTopUp(Base):
    __tablename__ = 'wallet_topups'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    token = Column(String, nullable=False, unique=True)
    amount_cents = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default='pending')
    stripe_session_id = Column(String, nullable=True, unique=True)
    checkout_url = Column(String, nullable=True)
    payment_intent_id = Column(String, nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)
    __table_args__ = (CheckConstraint('amount_cents > 0', name='wallet_positive_topup'),)


class WalletFundingLot(Base):
    __tablename__ = 'wallet_funding_lots'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    reference = Column(String, nullable=False, unique=True)
    payment_intent_id = Column(String, nullable=False, index=True)
    credited_cents = Column(Integer, nullable=False)
    available_cents = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (CheckConstraint('available_cents >= 0 AND available_cents <= credited_cents', name='wallet_lot_bounds'),)


class WalletCoupon(Base):
    __tablename__ = 'wallet_coupons'
    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False, unique=True)
    amount_cents = Column(Integer, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    max_redemptions = Column(Integer, nullable=False)
    redemption_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (CheckConstraint('amount_cents > 0 AND max_redemptions > 0 AND redemption_count >= 0 AND redemption_count <= max_redemptions', name='wallet_coupon_bounds'),)


class WalletPromoGrant(Base):
    __tablename__ = 'wallet_promo_grants'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    coupon_id = Column(Integer, ForeignKey('wallet_coupons.id'), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    remaining_cents = Column(Integer, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('user_id', 'coupon_id', name='wallet_coupon_once_per_user'),
                      CheckConstraint('remaining_cents >= 0 AND remaining_cents <= amount_cents', name='wallet_promo_bounds'))


class WalletPayment(Base):
    __tablename__ = 'wallet_payments'
    id = Column(Integer, primary_key=True)
    survey_id = Column(Integer, ForeignKey('surveys.id'), nullable=False, unique=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    cash_cents = Column(Integer, nullable=False)
    promo_cents = Column(Integer, nullable=False)
    pricing_snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (CheckConstraint('cash_cents >= 0 AND promo_cents >= 0', name='wallet_payment_nonnegative'),)


class WalletAllocation(Base):
    __tablename__ = 'wallet_allocations'
    id = Column(Integer, primary_key=True)
    payment_id = Column(Integer, ForeignKey('wallet_payments.id'), nullable=False, index=True)
    lot_id = Column(Integer, ForeignKey('wallet_funding_lots.id'), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint('payment_id', 'lot_id', name='wallet_payment_lot_once'),
                      CheckConstraint('amount_cents > 0', name='wallet_allocation_positive'))


class WalletPromoUse(Base):
    __tablename__ = 'wallet_promo_uses'
    id = Column(Integer, primary_key=True)
    payment_id = Column(Integer, ForeignKey('wallet_payments.id'), nullable=False, index=True)
    grant_id = Column(Integer, ForeignKey('wallet_promo_grants.id'), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    __table_args__ = (CheckConstraint('amount_cents > 0', name='wallet_promo_use_positive'),)


class WalletRefundPart(Base):
    __tablename__ = 'wallet_refund_parts'
    id = Column(Integer, primary_key=True)
    settlement_id = Column(Integer, ForeignKey('study_settlements.id'), nullable=False, index=True)
    lot_id = Column(Integer, ForeignKey('wallet_funding_lots.id'), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    payment_intent_id = Column(String, nullable=False)
    stripe_refund_id = Column(String, nullable=True, unique=True)
    status = Column(String, nullable=False, default='ready')
    __table_args__ = (UniqueConstraint('settlement_id', 'lot_id', name='wallet_settlement_lot_once'),
                      CheckConstraint('amount_cents > 0', name='wallet_refund_part_positive'))


class BillingInvoice(Base):
    __tablename__ = 'billing_invoices'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    reference = Column(String, nullable=False, unique=True)
    number = Column(String, nullable=False, unique=True)
    snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


def _immutable(mapper, connection, target):
    raise ValueError('Wallet ledger and issued invoice records are immutable.')


for model in (WalletLedger, BillingInvoice):
    event.listen(model, 'before_update', _immutable)
    event.listen(model, 'before_delete', _immutable)
