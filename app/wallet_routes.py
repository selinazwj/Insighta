from datetime import datetime, timezone
import asyncio
import re
import secrets
import stripe
from fastapi import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, Response
from app.models import Survey, StudySettlement
from app.wallet_models import WalletTopUp, WalletCoupon, WalletLedger, WalletPromoGrant, BillingInvoice, WalletRefundPart
from app.pricing import money_cents, positive_count, PricingError
from sqlalchemy.exc import IntegrityError
from app import wallet
from app.invoices import render_invoice


async def json_object(request):
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, 'Send a valid JSON object.')
    if not isinstance(body, dict):
        raise HTTPException(400, 'Send a valid JSON object.')
    return body


def register_wallet_routes(app, templates, get_db, get_current_user, admin_key_matches, base_url):
    from fastapi import Depends, Request, Query

    @app.get('/publisher/wallet')
    def wallet_page(request: Request, db=Depends(get_db)):
        login = RedirectResponse('/login?role=researcher&next=%2Fpublisher%2Fwallet', status_code=303)
        user_id = request.cookies.get('user_id')
        try:
            if not 0 < int(user_id) <= 2**63 - 1:
                return login
        except (ValueError, TypeError):
            return login
        try:
            current_user = get_current_user(user_id=user_id, db=db)
        except HTTPException as exc:
            if exc.status_code == 401:
                return login
            raise
        return templates.TemplateResponse('wallet.html', {'request':request, 'current_user':current_user,
            'wallet':wallet.summary(db,current_user.id), 'topup_token':secrets.token_urlsafe(24),
            'entries':db.query(WalletLedger).filter_by(user_id=current_user.id).order_by(WalletLedger.id.desc()).limit(100).all(),
            'topups':db.query(WalletTopUp).filter_by(user_id=current_user.id).order_by(WalletTopUp.id.desc()).limit(50).all(),
            'grants':db.query(WalletPromoGrant).filter_by(user_id=current_user.id).order_by(WalletPromoGrant.id.desc()).limit(50).all(),
            'invoices':db.query(BillingInvoice).filter_by(user_id=current_user.id).order_by(BillingInvoice.id.desc()).limit(100).all(),
            'now':datetime.utcnow(), 'stripe_configured':bool(stripe.api_key)})

    @app.get('/api/wallet')
    def wallet_status(current_user=Depends(get_current_user),db=Depends(get_db)):
        return wallet.summary(db,current_user.id)

    @app.post('/api/wallet/topups')
    async def top_up(request: Request,current_user=Depends(get_current_user),db=Depends(get_db)):
        body=await json_object(request)
        if not isinstance(body,dict): raise HTTPException(400,'Send a top-up object.')
        try: amount=money_cents(body.get('amount'))
        except PricingError as exc: raise HTTPException(400,str(exc))
        if amount<100: raise HTTPException(400,'Minimum wallet top-up is $1.00.')
        token=str(body.get('token') or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}',token): raise HTTPException(400,'Reload the wallet page before adding funds.')
        if not stripe.api_key: raise HTTPException(503,'Stripe top-ups are not configured. No funds have been charged or credited.')
        wallet.lock_wallet(db,current_user.id)
        key=f'{current_user.id}:{token}'
        row=db.query(WalletTopUp).filter_by(token=key).first()
        if row and row.amount_cents!=amount: raise HTTPException(409,'This top-up token belongs to a different amount. Reload the page.')
        if row and row.status=='paid': return {'redirect_url':'/publisher/wallet','topup_id':row.id}
        if row and row.checkout_url: return {'redirect_url':row.checkout_url,'topup_id':row.id}
        if not row:
            row=WalletTopUp(user_id=current_user.id,token=key,amount_cents=amount,status='pending')
            db.add(row);db.flush()
        db.commit()
        try:
            checkout=await asyncio.to_thread(stripe.checkout.Session.create,
                mode='payment',payment_method_types=['card'],
                line_items=[{'price_data':{'currency':'usd','unit_amount':amount,
                    'product_data':{'name':'Insighta researcher wallet top-up'}},'quantity':1}],
                success_url=base_url().rstrip('/')+'/publisher/wallet?topup=processing',
                cancel_url=base_url().rstrip('/')+'/publisher/wallet',
                metadata={'wallet_topup_id':str(row.id),'user_id':str(current_user.id)},
                idempotency_key=f'wallet-topup:{row.id}:v1')
        except stripe.error.StripeError:
            raise HTTPException(502,'Checkout could not be started. No wallet balance was credited; retry this top-up.')
        wallet.lock_wallet(db,current_user.id)
        row=db.query(WalletTopUp).filter_by(id=row.id).populate_existing().one()
        row.stripe_session_id, row.checkout_url=checkout.id,checkout.url
        db.commit()
        return {'redirect_url':checkout.url,'topup_id':row.id}

    @app.post('/api/wallet/redeem')
    async def redeem(request: Request,current_user=Depends(get_current_user),db=Depends(get_db)):
        body=await json_object(request)
        if not isinstance(body,dict): raise HTTPException(400,'Send a coupon code.')
        wallet.redeem_coupon(db,current_user.id,body.get('code'))
        db.commit()
        return {'ok':True,**wallet.summary(db,current_user.id)}

    @app.post('/api/wallet/billing')
    async def billing(request: Request,current_user=Depends(get_current_user),db=Depends(get_db)):
        body=await json_object(request)
        if not isinstance(body,dict): raise HTTPException(400,'Send billing details.')
        profile={}
        for key,maximum in [('name',160),('institution',200),('address',600)]:
            value=body.get(key,'')
            if not isinstance(value,str) or len(value)>maximum: raise HTTPException(400,'Billing details are too long or invalid.')
            profile[key]=value.strip()
        _,account=wallet.lock_wallet(db,current_user.id,allow_blocked=True)
        account.billing_profile=profile
        db.commit()
        return {'ok':True}

    @app.get('/api/wallet/invoices/{invoice_id}.pdf')
    def invoice_pdf(invoice_id:int,current_user=Depends(get_current_user),db=Depends(get_db)):
        invoice=db.query(BillingInvoice).filter_by(id=invoice_id,user_id=current_user.id).first()
        if not invoice: raise HTTPException(404,'Invoice not found')
        return Response(render_invoice(invoice),media_type='application/pdf',headers={
            'Content-Disposition':f'attachment; filename="{invoice.number}.pdf"','Cache-Control':'private, no-store'})

    @app.get('/api/surveys/{survey_id}/invoice')
    def study_invoice(survey_id:int,current_user=Depends(get_current_user),db=Depends(get_db)):
        from app.settlements import lock_study
        study=lock_study(db,survey_id)
        if not study or study.publisher_id!=current_user.id: raise HTTPException(404,'Study not found')
        if study.payment_status!='paid' or not study.pricing_snapshot: raise HTTPException(409,'An invoice is available after this quoted study is paid.')
        invoice=wallet.issue_study_invoice(db,study)
        db.commit()
        return Response(render_invoice(invoice),media_type='application/pdf',headers={
            'Content-Disposition':f'attachment; filename="{invoice.number}.pdf"','Cache-Control':'private, no-store'})

    @app.post('/api/surveys/{survey_id}/refund-destination')
    async def refund_destination(survey_id:int,request: Request,current_user=Depends(get_current_user),db=Depends(get_db)):
        from app.settlements import lock_study
        body=await json_object(request)
        if not isinstance(body,dict) or body.get('destination') not in {'wallet','card'}:
            raise HTTPException(400,'Choose wallet or original card.')
        study=lock_study(db,survey_id)
        if not study or study.publisher_id!=current_user.id: raise HTTPException(404,'Study not found')
        if not study.pricing_snapshot or study.payment_status!='paid': raise HTTPException(409,'This order is not funded.')
        row=db.query(StudySettlement).filter_by(survey_id=survey_id).first()
        if row and row.frozen_at: raise HTTPException(409,'The refund destination is locked after settlement calculation.')
        study.refund_destination=body['destination']
        if row: row.refund_destination=body['destination']
        db.commit()
        return {'ok':True,'destination':study.refund_destination}

    @app.get('/admin/wallet')
    def admin_wallet(request:Request,admin_key:str=Query(None)):
        if not admin_key_matches(admin_key): raise HTTPException(403,'Unauthorized')
        return templates.TemplateResponse('wallet_coupons.html',{'request':request,'admin_key':admin_key})

    @app.post('/admin/api/wallet/coupons')
    async def create_coupon(request: Request,db=Depends(get_db)):
        body=await json_object(request)
        if not isinstance(body,dict) or not admin_key_matches(body.get('admin_key')): raise HTTPException(403,'Unauthorized')
        try:
            amount=money_cents(body.get('amount'))
            expiry=datetime.fromisoformat(str(body.get('expires_at','')).replace('Z','+00:00'))
            if expiry.tzinfo: expiry=expiry.astimezone(timezone.utc).replace(tzinfo=None)
            limit=positive_count(body.get('max_redemptions',1))
        except (ValueError,TypeError): raise HTTPException(400,'Enter a valid amount, expiration and redemption limit.')
        code=str(body.get('code') or secrets.token_hex(8)).strip().upper()
        if (amount<=0 or expiry<=datetime.utcnow() or not 1<=limit<=100000
            or not re.fullmatch(r'[A-Z0-9_-]{6,64}',code)):
            raise HTTPException(400,'Use a positive amount, future UTC expiration, whole-number redemption limit, and a 6-64 character coupon code.')
        if db.query(WalletCoupon).filter_by(code=code).first(): raise HTTPException(409,'This coupon code already exists.')
        coupon=WalletCoupon(code=code,amount_cents=amount,expires_at=expiry,max_redemptions=limit)
        db.add(coupon)
        try: db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409,'This coupon code already exists.')
        return {'code':code,'amount_cents':amount,'expires_at':expiry.isoformat()+'Z','max_redemptions':limit}
