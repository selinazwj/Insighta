"""Immutable billing snapshots and downloadable researcher invoices/receipts."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
import os
from html import escape


def issue_invoice(db, user, reference, title, lines, paid_cents, payment_method, document_kind='study_invoice'):
    from app.wallet_models import BillingInvoice, WalletAccount
    existing = db.query(BillingInvoice).filter_by(reference=reference).first()
    if existing: return existing
    account = db.get(WalletAccount, user.id)
    profile = (account.billing_profile if account else None) or {}
    customer = profile.get('name') or user.username or user.email
    invoice = BillingInvoice(user_id=user.id, reference=reference,
        number='INS-'+reference.upper().replace(':','-'), snapshot={
            'issuer':os.environ.get('INVOICE_ISSUER_NAME','Insighta'),
            'issuer_address':os.environ.get('INVOICE_ISSUER_ADDRESS',''),
            'issuer_email':os.environ.get('INVOICE_ISSUER_EMAIL','insightacom@gmail.com'),
            'customer':customer, 'customer_email':user.email,
            'institution':profile.get('institution',''), 'billing_address':profile.get('address',''),
            'title':title, 'lines':lines, 'paid_cents':paid_cents, 'currency':'usd',
            'payment_method':payment_method, 'document_kind':document_kind,
            'issued_at':datetime.utcnow().isoformat()})
    db.add(invoice); db.flush()
    return invoice


def render_invoice(invoice):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if 'InvoiceSans' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('InvoiceSans', str(Path(__file__).parent / 'assets/fonts/NotoSansSC-Regular.ttf')))
    s = invoice.snapshot
    stream=BytesIO()
    doc=SimpleDocTemplate(stream,pagesize=A4,rightMargin=42,leftMargin=42,topMargin=40,bottomMargin=40)
    styles=getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = 'InvoiceSans'
    body=ParagraphStyle('invoice_body',parent=styles['BodyText'],fontSize=10,leading=15,spaceAfter=7)
    muted=ParagraphStyle('invoice_muted',parent=body,textColor=colors.HexColor('#57635d'))
    def p(text,style=body): return Paragraph(escape(str(text)).replace('\n','<br/>'),style)
    kind='Wallet funding receipt' if s['document_kind']=='topup_receipt' else 'Invoice'
    story=[p(kind,styles['Title']), Spacer(1,12), p(s['issuer'],styles['Heading2']),
           p(s['issuer_address'],muted),p(s['issuer_email'],muted),Spacer(1,8),
           p(f'Number: {invoice.number}'),p('Issued: '+s['issued_at'][:10]+' (UTC)'),
           p('Billed to: '+s['customer']),p(s['institution']),p(s['billing_address']),p(s['customer_email'],muted),
           Spacer(1,12),p(s['title'],styles['Heading2']),Spacer(1,8)]
    rows=[[p('Description'),p('USD')]]
    for line in s['lines']:
        amount=line['amount_cents']/100
        rows.append([p(line['description']),p(f'{amount:,.2f}')])
    rows.append([p('Total paid'),p(f"{s['paid_cents']/100:,.2f}")])
    table=Table(rows,colWidths=[400,111],hAlign='LEFT',repeatRows=1)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#eef3ef')),
        ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),
        ('TOPPADDING',(0,0),(-1,-1),10),('BOTTOMPADDING',(0,0),(-1,-1),10),
        ('LINEBELOW',(0,0),(-1,0),.5,colors.HexColor('#cdd7cf')),
        ('LINEABOVE',(0,-1),(-1,-1),.5,colors.HexColor('#cdd7cf'))]))
    story += [table,Spacer(1,16),p('Payment method: '+s['payment_method']),p('Amount due: USD 0.00')]
    if s['document_kind']=='topup_receipt':
        story.append(p('This receipt records wallet funding. Individual study invoices describe the services purchased from that balance.',muted))
    story.append(p('Refunds and settlement adjustments, when applicable, appear separately in the wallet and study settlement history.',muted))
    doc.build(story)
    return stream.getvalue()
