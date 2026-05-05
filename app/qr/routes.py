import os
import re
import secrets
import unicodedata

from flask import (render_template, redirect, url_for, flash, request,
                   current_app, abort, send_from_directory, send_file)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from app import db
from app.qr import bp
from app.qr.forms import QRForm
from app.models import QRCode, ThemaQRLink, QR_CATEGORIE_KEUZES, QR_STIJLEN


QR_PDF_SUBFOLDER = 'qr_pdf'
QR_PDF_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def _slugify(raw):
    if not raw:
        return ''
    norm = unicodedata.normalize('NFKD', raw).encode('ascii', 'ignore').decode()
    norm = norm.lower().strip()
    norm = re.sub(r'[^a-z0-9]+', '-', norm)
    norm = re.sub(r'-+', '-', norm).strip('-')
    return norm[:30]


def _unieke_slug(basis, exclude_id=None):
    """Genereer een slug die uniek is in de database."""
    basis = basis or 'qr'
    kandidaat = basis
    teller = 2
    while True:
        q = QRCode.query.filter_by(slug=kandidaat)
        if exclude_id:
            q = q.filter(QRCode.id != exclude_id)
        if not q.first():
            return kandidaat
        suffix = f'-{teller}'
        kandidaat = (basis[:30 - len(suffix)] + suffix)
        teller += 1
        if teller > 50:
            kandidaat = f'qr-{secrets.token_hex(4)}'
            return kandidaat


def _verwerk_pdf_upload(form_file, bestaand=None):
    """Sla een geüploade PDF op. Retourneert nieuwe bestandsnaam of None."""
    if not form_file or not form_file.filename:
        return bestaand
    form_file.seek(0, os.SEEK_END)
    size = form_file.tell()
    form_file.seek(0)
    if size > QR_PDF_MAX_BYTES:
        flash(f'PDF is te groot (max {QR_PDF_MAX_BYTES // (1024*1024)} MB).', 'danger')
        return bestaand

    upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], QR_PDF_SUBFOLDER)
    os.makedirs(upload_dir, exist_ok=True)

    veilige_naam = secure_filename(form_file.filename) or 'document.pdf'
    unieke = f'{secrets.token_hex(8)}_{veilige_naam}'
    pad = os.path.join(upload_dir, unieke)
    form_file.save(pad)

    # verwijder oude PDF als die bestond
    if bestaand:
        oud_pad = os.path.join(upload_dir, bestaand)
        if os.path.exists(oud_pad):
            try:
                os.remove(oud_pad)
            except OSError:
                pass

    return unieke


def _verwijder_pdf(bestandsnaam):
    if not bestandsnaam:
        return
    pad = os.path.join(current_app.config['UPLOAD_FOLDER'], QR_PDF_SUBFOLDER, bestandsnaam)
    if os.path.exists(pad):
        try:
            os.remove(pad)
        except OSError:
            pass


# ---------------- Overzicht ----------------

@bp.route('/')
@login_required
def overzicht():
    categorie = request.args.get('categorie', '').strip()
    status = request.args.get('status', '').strip()
    alleen_van_mij = request.args.get('mij') == '1'

    q = QRCode.query
    if categorie:
        q = q.filter(QRCode.categorie == categorie)
    if alleen_van_mij:
        q = q.filter(QRCode.eigenaar_id == current_user.id)
    qrs = q.order_by(QRCode.bijgewerkt_op.desc()).all()

    if status:
        qrs = [x for x in qrs if x.status == status]

    return render_template('qr/overzicht.html',
                           qrs=qrs,
                           qr_categorieen=QR_CATEGORIE_KEUZES,
                           filter_categorie=categorie,
                           filter_status=status,
                           alleen_van_mij=alleen_van_mij)


# ---------------- Nieuw ----------------

@bp.route('/nieuw', methods=['GET', 'POST'])
@login_required
def nieuw():
    form = QRForm()

    if form.validate_on_submit():
        # Minstens één doel nodig
        heeft_pdf = bool(form.pdf_bestand.data and form.pdf_bestand.data.filename)
        if not form.doel_url.data and not heeft_pdf:
            flash('Geef een doel-URL of upload een PDF.', 'danger')
            return render_template('qr/formulier.html', form=form, titel='Nieuwe QR-code', qr=None)

        if form.categorie.data == 'anders' and not form.categorie_anders.data.strip():
            flash('Vul de eigen categorie in.', 'danger')
            return render_template('qr/formulier.html', form=form, titel='Nieuwe QR-code', qr=None)

        slug_basis = _slugify(form.naam.data)
        slug = _unieke_slug(slug_basis)

        pdf_naam = _verwerk_pdf_upload(form.pdf_bestand.data)

        qr = QRCode(
            slug=slug,
            naam=form.naam.data.strip(),
            omschrijving=(form.omschrijving.data or '').strip(),
            doel_url=(form.doel_url.data or '').strip() or None,
            pdf_bestand=pdf_naam,
            categorie=form.categorie.data,
            categorie_anders=(form.categorie_anders.data or '').strip(),
            tekst_onder=(form.tekst_onder.data or '').strip(),
            stijl=form.stijl.data if form.stijl.data in QR_STIJLEN else 'navy',
            actief=True,
            eigenaar_id=current_user.id,
        )
        db.session.add(qr)
        db.session.commit()
        flash(f'QR-code "{qr.naam}" aangemaakt.', 'success')
        return redirect(url_for('qr.bewerken', qr_id=qr.id))

    return render_template('qr/formulier.html', form=form, titel='Nieuwe QR-code', qr=None)


# ---------------- Bewerken ----------------

@bp.route('/<int:qr_id>/bewerken', methods=['GET', 'POST'])
@login_required
def bewerken(qr_id):
    qr = db.session.get(QRCode, qr_id)
    if not qr:
        abort(404)
    if not qr.mag_bewerken(current_user):
        flash('Je mag deze QR-code niet bewerken.', 'danger')
        return redirect(url_for('qr.overzicht'))

    form = QRForm(obj=qr)

    if form.validate_on_submit():
        heeft_pdf_nieuw = bool(form.pdf_bestand.data and form.pdf_bestand.data.filename)
        verwijder_pdf = form.pdf_verwijderen.data
        nieuwe_pdf_naam = qr.pdf_bestand

        if verwijder_pdf and not heeft_pdf_nieuw:
            _verwijder_pdf(qr.pdf_bestand)
            nieuwe_pdf_naam = None
        elif heeft_pdf_nieuw:
            nieuwe_pdf_naam = _verwerk_pdf_upload(form.pdf_bestand.data, bestaand=qr.pdf_bestand)

        doel_url = (form.doel_url.data or '').strip() or None
        if not doel_url and not nieuwe_pdf_naam:
            flash('Geef een doel-URL of upload een PDF.', 'danger')
            return render_template('qr/formulier.html', form=form, titel='QR-code bewerken', qr=qr)

        if form.categorie.data == 'anders' and not form.categorie_anders.data.strip():
            flash('Vul de eigen categorie in.', 'danger')
            return render_template('qr/formulier.html', form=form, titel='QR-code bewerken', qr=qr)

        qr.naam = form.naam.data.strip()
        qr.omschrijving = (form.omschrijving.data or '').strip()
        qr.doel_url = doel_url
        qr.pdf_bestand = nieuwe_pdf_naam
        qr.categorie = form.categorie.data
        qr.categorie_anders = (form.categorie_anders.data or '').strip()
        qr.tekst_onder = (form.tekst_onder.data or '').strip()
        qr.stijl = form.stijl.data if form.stijl.data in QR_STIJLEN else 'navy'

        db.session.commit()
        flash('QR-code bijgewerkt.', 'success')
        return redirect(url_for('qr.bewerken', qr_id=qr.id))

    return render_template('qr/formulier.html', form=form, titel='QR-code bewerken', qr=qr)


# ---------------- Pauzeren / Verwijderen ----------------

@bp.route('/<int:qr_id>/pauzeren', methods=['POST'])
@login_required
def pauzeren(qr_id):
    qr = db.session.get(QRCode, qr_id)
    if not qr:
        abort(404)
    if not qr.mag_bewerken(current_user):
        flash('Je mag deze QR-code niet beheren.', 'danger')
        return redirect(url_for('qr.overzicht'))
    qr.actief = not qr.actief
    db.session.commit()
    flash(('QR gepauzeerd.' if not qr.actief else 'QR geactiveerd.'), 'info')
    return redirect(request.referrer or url_for('qr.overzicht'))


@bp.route('/<int:qr_id>/verwijderen', methods=['POST'])
@login_required
def verwijderen(qr_id):
    qr = db.session.get(QRCode, qr_id)
    if not qr:
        abort(404)
    if not qr.mag_bewerken(current_user):
        flash('Je mag deze QR-code niet verwijderen.', 'danger')
        return redirect(url_for('qr.overzicht'))
    _verwijder_pdf(qr.pdf_bestand)
    # Eventuele themakaart-koppelingen opruimen
    ThemaQRLink.query.filter_by(qr_code_id=qr.id).delete()
    db.session.delete(qr)
    db.session.commit()
    flash('QR-code verwijderd.', 'info')
    return redirect(url_for('qr.overzicht'))


# ---------------- PDF bekijken ----------------

@bp.route('/pdf/<path:bestand>')
def pdf(bestand):
    """Serveert een geüploade PDF (wordt gevonden via QR-redirect)."""
    upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], QR_PDF_SUBFOLDER)
    return send_from_directory(upload_dir, bestand)


# ---------------- Downloads ----------------

def _gevraagde_stijl(qr):
    """Stijl uit query-string als die geldig is (live preview), anders opgeslagen stijl."""
    gevraagd = (request.args.get('stijl') or '').strip()
    if gevraagd in QR_STIJLEN:
        return gevraagd
    return qr.stijl if qr.stijl in QR_STIJLEN else 'navy'


@bp.route('/<int:qr_id>/download.<formaat>')
@login_required
def download(qr_id, formaat):
    qr = db.session.get(QRCode, qr_id)
    if not qr:
        abort(404)

    stijl = _gevraagde_stijl(qr)
    from app.qr import generator as gen

    veilige_naam = re.sub(r'[^a-z0-9-]+', '-', qr.slug)[:40] or 'qr'
    suffix = f'-{stijl}'

    if formaat == 'png':
        buf = gen.render_png_bytes(qr, stijl=stijl)
        return send_file(buf, mimetype='image/png', as_attachment=True,
                         download_name=f'qr-{veilige_naam}{suffix}.png')
    if formaat == 'svg':
        buf = gen.render_svg_bytes(qr, stijl=stijl)
        return send_file(buf, mimetype='image/svg+xml', as_attachment=True,
                         download_name=f'qr-{veilige_naam}{suffix}.svg')
    if formaat == 'pdf':
        buf = gen.render_pdf_bytes(qr, stijl=stijl)
        return send_file(buf, mimetype='application/pdf', as_attachment=True,
                         download_name=f'qr-{veilige_naam}{suffix}.pdf')

    abort(404)


@bp.route('/<int:qr_id>/preview.png')
@login_required
def preview_png(qr_id):
    """Inline PNG-preview voor op de bewerk-pagina (geen download).

    Optioneel ?tekst=0 om de tekst-onder uit te schakelen (themakaart-gebruik).
    """
    qr = db.session.get(QRCode, qr_id)
    if not qr:
        abort(404)
    stijl = _gevraagde_stijl(qr)
    met_tekst = request.args.get('tekst') != '0'
    from app.qr import generator as gen
    buf = gen.render_png_bytes(qr, stijl=stijl, met_tekst=met_tekst)
    return send_file(buf, mimetype='image/png')
