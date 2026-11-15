import asyncio
from datetime import datetime
from urllib.parse import urlencode
from fastapi import Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse
from app.models import Survey
from app.niche_models import NicheRequest, NicheQuote
from app import niche


def register_niche_routes(app, templates, get_db, get_current_user, admin_key_matches, base_url):
    def owner_study(db, study_id, user):
        study = db.query(Survey).filter_by(id=study_id, publisher_id=user.id).first()
        if not study or not niche.request_for(db, study.id):
            raise HTTPException(404, 'Quote request not found.')
        return study

    def owner_context(request, db, user, study, error=None):
        row = niche.request_for(db, study.id)
        quote = db.get(NicheQuote, row.current_quote_id) if row.current_quote_id else None
        return {'request':request, 'current_user':user, 'study':study, 'niche_request':row,
                'quote':quote, 'expired':bool(quote and (quote.expires_at <= datetime.utcnow()
                    or quote.checkout_expires_at and quote.checkout_expires_at <= datetime.utcnow())), 'error':error}

    def admin_rows(db):
        result = []
        for row, study in db.query(NicheRequest, Survey).join(Survey, Survey.id == NicheRequest.survey_id).order_by(NicheRequest.id.desc()).all():
            quote = db.get(NicheQuote, row.current_quote_id) if row.current_quote_id else None
            result.append({'request':row, 'study':study, 'quote':quote})
        return result

    @app.get('/publisher/study/{study_id}/quote')
    def quote_page(study_id: int, request: Request, db=Depends(get_db)):
        try:
            user = get_current_user(user_id=request.cookies.get('user_id'), db=db)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            return RedirectResponse('/login?'+urlencode({'role':'researcher','next':f'/publisher/study/{study_id}/quote'}), status_code=303)
        study = owner_study(db, study_id, user)
        return templates.TemplateResponse('niche_quote.html', owner_context(request, db, user, study))

    @app.post('/publisher/study/{study_id}/quote/checkout')
    async def quote_checkout(study_id: int, request: Request, user=Depends(get_current_user), db=Depends(get_db)):
        study = owner_study(db, study_id, user)
        form = await request.form()
        try:
            if form.get('accept_quote') != '1':
                raise HTTPException(400, 'Confirm the quote before continuing to payment.')
            try:
                quote_id = int(form.get('quote_id', ''))
            except (TypeError, ValueError):
                raise HTTPException(400, 'Open the current quote and try again.')
            url = await asyncio.to_thread(niche.checkout_for_quote, db, study, quote_id, form.get('refund_destination'), base_url())
            db.commit()
            return RedirectResponse(url, status_code=303)
        except HTTPException as exc:
            db.rollback()
            return templates.TemplateResponse('niche_quote.html', owner_context(request, db, user, study, exc.detail), status_code=exc.status_code)

    @app.get('/admin/niche')
    def admin_niche(request: Request, admin_key: str = Query(None), db=Depends(get_db)):
        if not admin_key_matches(admin_key):
            raise HTTPException(403, 'Administrator authorization required.')
        return templates.TemplateResponse('niche_admin.html', {'request':request, 'admin_key':admin_key, 'rows':admin_rows(db), 'error':None})

    @app.post('/admin/niche/{request_id}/quotes')
    async def create_offer(request_id: int, request: Request, db=Depends(get_db)):
        form = await request.form()
        key = form.get('admin_key')
        if not admin_key_matches(key):
            raise HTTPException(403, 'Administrator authorization required.')
        row = db.get(NicheRequest, request_id)
        if not row:
            raise HTTPException(404, 'Request not found.')
        try:
            await asyncio.to_thread(niche.offer_quote, db, db.get(Survey, row.survey_id), dict(form))
            db.commit()
        except HTTPException as exc:
            db.rollback()
            return templates.TemplateResponse('niche_admin.html', {'request':request, 'admin_key':key,
                'rows':admin_rows(db), 'error':exc.detail}, status_code=exc.status_code)
        return RedirectResponse('/admin/niche?'+urlencode({'admin_key':key}), status_code=303)
