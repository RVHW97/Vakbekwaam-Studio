import os
import uuid
import shutil
from flask import render_template, redirect, url_for, flash, request, current_app, make_response, abort
from flask_login import login_required, current_user
from PIL import Image
from app import db
from app.models import (Kaart, KaartAfbeelding, KaartWijziging, KaartKoppeling,
                         ThemaKaartLink, ThemaQRLink, QRCode,
                         KAART_TYPES, KERNTAKEN, QR_CATEGORIEEN, kenmerken_kerntaak_label,
                         THEMA_MAX_KAARTEN_TOTAAL, THEMA_MAX_QR_TOP, THEMA_MAX_QR_BOTTOM,
                         THEMA_QR_LABEL_MAX)
from app.kaarten import bp
from app.kaarten.forms import FORMULIEREN, INHOUD_VELDEN

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
HEADER_FOTO_MAX_BYTES = 5 * 1024 * 1024
HEADER_FOTO_MIN_W = 1200
HEADER_FOTO_MIN_H = 400
HEADER_FOTO_RATIO = 3.0


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file):
    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        unique_name = f'{uuid.uuid4().hex}.{ext}'
        upload_folder = current_app.config['UPLOAD_FOLDER']
        os.makedirs(upload_folder, exist_ok=True)
        file.save(os.path.join(upload_folder, unique_name))
        return unique_name, file.filename
    return None, None


def save_kaart_header_foto(file, kaart_type):
    """Kies juiste opslagstrategie voor de hoofdfoto van een kaart.

    Themakaart: vrije ratio (achtergrondfoto die de hele kaart vult).
    Anderen: 3:1 strip (header-foto in de smalle bovenrand).
    """
    if kaart_type == 'thema':
        return save_foto(file, prefix='thema_bg', ratio=None)
    return save_header_foto(file)


def save_header_foto(file):
    if not file or not file.filename or not allowed_file(file.filename):
        return None, 'Ongeldig bestandstype. Gebruik JPG, PNG of WebP.'
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > HEADER_FOTO_MAX_BYTES:
        return None, 'Bestand is te groot (max 5 MB).'
    img = Image.open(file)
    if img.width < HEADER_FOTO_MIN_W or img.height < HEADER_FOTO_MIN_H:
        return None, f'Afbeelding te klein (minimaal {HEADER_FOTO_MIN_W}×{HEADER_FOTO_MIN_H} px).'
    current_ratio = img.width / img.height
    if current_ratio > HEADER_FOTO_RATIO:
        new_w = int(img.height * HEADER_FOTO_RATIO)
        left = (img.width - new_w) // 2
        img = img.crop((left, 0, left + new_w, img.height))
    elif current_ratio < HEADER_FOTO_RATIO:
        new_h = int(img.width / HEADER_FOTO_RATIO)
        top = (img.height - new_h) // 2
        img = img.crop((0, top, img.width, top + new_h))
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    filename = f'header_{uuid.uuid4().hex}.jpg'
    upload_folder = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_folder, exist_ok=True)
    img.save(os.path.join(upload_folder, filename), 'JPEG', quality=90)
    return filename, None


FOTO_MAX_BYTES = 5 * 1024 * 1024
FOTO_MIN_PX = 200  # minimaal 200px breed of hoog


def save_foto(file, prefix='foto', ratio=None):
    """Flexibele foto-opslag. Optionele ratio-crop (bijv. 4/3)."""
    if not file or not file.filename or not allowed_file(file.filename):
        return None, 'Ongeldig bestandstype. Gebruik JPG, PNG of WebP.'
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > FOTO_MAX_BYTES:
        return None, 'Bestand is te groot (max 5 MB).'
    img = Image.open(file)
    if img.width < FOTO_MIN_PX or img.height < FOTO_MIN_PX:
        return None, f'Afbeelding te klein (minimaal {FOTO_MIN_PX}×{FOTO_MIN_PX} px).'
    if ratio:
        current_ratio = img.width / img.height
        if current_ratio > ratio:
            new_w = int(img.height * ratio)
            left = (img.width - new_w) // 2
            img = img.crop((left, 0, left + new_w, img.height))
        elif current_ratio < ratio:
            new_h = int(img.width / ratio)
            top = (img.height - new_h) // 2
            img = img.crop((0, top, img.width, top + new_h))
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    filename = f'{prefix}_{uuid.uuid4().hex}.jpg'
    upload_folder = current_app.config['UPLOAD_FOLDER']
    os.makedirs(upload_folder, exist_ok=True)
    img.save(os.path.join(upload_folder, filename), 'JPEG', quality=90)
    return filename, None


def _kaart_naam_uit_form(form, kaart_type):
    """Haal de logische naam op uit het formulier.

    Themakaart: geen apart naam-veld → titel doet dienst als interne naam.
    """
    if kaart_type == 'thema':
        return (getattr(form, 'titel').data or '').strip() or 'Themakaart'
    return form.naam.data


def _kaart_naam_uit_request(request_form, kaart_type, fallback=''):
    """Zelfde als hierboven, maar voor auto-save (request.form i.p.v. WTForms)."""
    if kaart_type == 'thema':
        return (request_form.get('titel') or '').strip() or fallback or 'Themakaart'
    return (request_form.get('naam') or fallback).strip() or fallback


def log_wijziging(kaart, actie, omschrijving=''):
    w = KaartWijziging(
        kaart_id=kaart.id,
        gebruiker_id=current_user.id if current_user.is_authenticated else None,
        actie=actie,
        omschrijving=omschrijving,
    )
    db.session.add(w)


def verwijder_bestand(bestandsnaam):
    try:
        pad = os.path.join(current_app.config['UPLOAD_FOLDER'], bestandsnaam)
        if os.path.exists(pad):
            os.remove(pad)
    except OSError:
        pass


@bp.route('/')
@login_required
def overzicht():
    # Mappenoverzicht: per kaarttype aantallen tonen
    mappen = []
    totaal_archief = Kaart.query.filter_by(status='gearchiveerd').count()
    for type_key, type_info in KAART_TYPES.items():
        actief = Kaart.query.filter(
            Kaart.type == type_key,
            Kaart.status != 'gearchiveerd'
        ).count()
        mappen.append({
            'key': type_key,
            'naam': type_info['naam'],
            'prefix': type_info['prefix'],
            'aantal': actief,
        })
    return render_template('kaarten/mappen.html', mappen=mappen,
                           totaal_archief=totaal_archief)


@bp.route('/type/<kaart_type>')
@login_required
def overzicht_type(kaart_type):
    if kaart_type not in KAART_TYPES:
        flash('Onbekend kaarttype.', 'danger')
        return redirect(url_for('kaarten.overzicht'))
    weergave = request.args.get('weergave', 'actief')
    query_string = (request.args.get('q') or '').strip()
    kerntaak_filter = (request.args.get('kerntaak') or '').strip()
    query = Kaart.query.filter_by(type=kaart_type)
    if weergave == 'archief':
        query = query.filter_by(status='gearchiveerd')
    else:
        weergave = 'actief'
        query = query.filter(Kaart.status != 'gearchiveerd')
    if kerntaak_filter and kerntaak_filter in KERNTAKEN:
        query = query.filter(Kaart.kerntaak == kerntaak_filter)
    if query_string:
        zoekterm = f'%{query_string}%'
        query = query.filter(db.or_(
            Kaart.naam.ilike(zoekterm),
            Kaart.nummer.ilike(zoekterm),
            Kaart.inhoud.ilike(zoekterm),
        ))
    kaarten = query.order_by(Kaart.bijgewerkt_op.desc()).all()
    type_info = KAART_TYPES[kaart_type]
    return render_template('kaarten/overzicht.html', kaarten=kaarten,
                           types=KAART_TYPES, kerntaken=KERNTAKEN, weergave=weergave,
                           kaart_type=kaart_type, type_info=type_info, q=query_string,
                           kerntaak_filter=kerntaak_filter)


@bp.route('/archief')
@login_required
def archief():
    kaarten = Kaart.query.filter_by(status='gearchiveerd').order_by(Kaart.bijgewerkt_op.desc()).all()
    return render_template('kaarten/overzicht.html', kaarten=kaarten,
                           types=KAART_TYPES, weergave='archief',
                           kaart_type=None, type_info=None)


@bp.route('/nieuw')
@login_required
def kies_type():
    return render_template('kaarten/kies_type.html', types=KAART_TYPES)


@bp.route('/nieuw/<kaart_type>', methods=['GET', 'POST'])
@login_required
def aanmaken(kaart_type):
    if kaart_type not in KAART_TYPES:
        flash('Ongeldig kaarttype.', 'danger')
        return redirect(url_for('kaarten.kies_type'))

    form_class = FORMULIEREN[kaart_type]
    form = form_class()

    if form.validate_on_submit():
        inhoud = {}
        for veld in INHOUD_VELDEN[kaart_type]:
            inhoud[veld] = getattr(form, veld).data or ''

        kaart = Kaart(
            type=kaart_type,
            nummer=Kaart.volgende_nummer(kaart_type),
            naam=_kaart_naam_uit_form(form, kaart_type),
            kerntaak=form.kerntaak.data or None,
            status='concept',
            auteur_id=current_user.id,
            bijgewerkt_door_id=current_user.id,
        )
        kaart.set_inhoud(inhoud)

        if form.header_foto.data and form.header_foto.data.filename:
            foto_naam, foto_fout = save_kaart_header_foto(form.header_foto.data, kaart_type)
            if foto_fout:
                flash(f'Foto: {foto_fout}', 'warning')
            else:
                kaart.header_foto = foto_naam

        db.session.add(kaart)
        db.session.flush()

        log_wijziging(kaart, 'Aangemaakt')
        db.session.commit()
        auto_save_tab = (request.form.get('auto_save_tab') or '').strip()
        if auto_save_tab:
            flash(f'Concept opgeslagen als {kaart.nummer}.', 'success')
            return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + f'#tab-{auto_save_tab}')
        flash(f'{kaart.type_naam} "{kaart.naam}" opgeslagen als concept ({kaart.nummer}).', 'success')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))

    if hasattr(form, 'kenmerken_kerntaak'):
        form.kenmerken_kerntaak.label.text = kenmerken_kerntaak_label(form.kerntaak.data)
    type_info = KAART_TYPES[kaart_type]
    return render_template('kaarten/formulier.html', form=form, kaart_type=kaart_type,
                           type_info=type_info, bewerken=False, kaart=None)


@bp.route('/<int:kaart_id>/bewerken', methods=['GET', 'POST'])
@login_required
def bewerken(kaart_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    form_class = FORMULIEREN[kaart.type]

    if request.method == 'POST':
        form = form_class()
    else:
        # Prefill met huidige inhoud
        huidige = kaart.get_inhoud()
        data = {'naam': kaart.naam, 'kerntaak': kaart.kerntaak or ''}
        for veld in INHOUD_VELDEN[kaart.type]:
            data[veld] = huidige.get(veld, '')
        form = form_class(data=data)

    wijziging_toelichting = (request.form.get('wijziging_toelichting') or '').strip()
    auto_save_tab = (request.form.get('auto_save_tab') or '').strip()
    is_auto_save = bool(auto_save_tab)
    toelichting_fout = None

    # === AUTO-SAVE BIJ TAB-WISSEL ===
    # Bypass de strikte WTForms-validatie (Length/DataRequired): concept mag incompleet
    # of over-lang zijn. Zo gaat er nooit data verloren tijdens tab-wissel.
    if request.method == 'POST' and is_auto_save:
        inhoud = {}
        for veld in INHOUD_VELDEN[kaart.type]:
            inhoud[veld] = (request.form.get(veld) or '').strip()

        kaart.naam = _kaart_naam_uit_request(request.form, kaart.type, fallback=kaart.naam)
        kaart.kerntaak = (request.form.get('kerntaak') or kaart.kerntaak) or None
        kaart.set_inhoud(inhoud)
        kaart.bijgewerkt_door_id = current_user.id

        if request.form.get('verwijder_header_foto') and kaart.header_foto:
            verwijder_bestand(kaart.header_foto)
            kaart.header_foto = None

        hdr = request.files.get('header_foto')
        if hdr and hdr.filename:
            foto_naam, foto_fout = save_kaart_header_foto(hdr, kaart.type)
            if foto_fout:
                flash(f'Foto: {foto_fout}', 'warning')
            elif foto_naam:
                if kaart.header_foto:
                    verwijder_bestand(kaart.header_foto)
                kaart.header_foto = foto_naam

        if request.form.get('verwijder_ensceneringstips_foto') and kaart.ensceneringstips_foto:
            verwijder_bestand(kaart.ensceneringstips_foto)
            kaart.ensceneringstips_foto = None

        tips_foto = request.files.get('ensceneringstips_foto')
        if tips_foto and tips_foto.filename:
            foto_naam, foto_fout = save_foto(tips_foto, prefix='tips', ratio=4/3)
            if foto_fout:
                flash(f'Tips-foto: {foto_fout}', 'warning')
            elif foto_naam:
                if kaart.ensceneringstips_foto:
                    verwijder_bestand(kaart.ensceneringstips_foto)
                kaart.ensceneringstips_foto = foto_naam

        log_wijziging(kaart, 'Auto-save (tab-wissel)', '')
        db.session.commit()
        flash(f'Concept opgeslagen ({kaart.nummer}).', 'info')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + f'#tab-{auto_save_tab}')

    # === NORMALE OPSLAAN (submit-knop) ===
    if request.method == 'POST' and not wijziging_toelichting:
        toelichting_fout = 'Vul een korte toelichting in waarom je deze kaart wijzigt.'

    if form.validate_on_submit() and not toelichting_fout:
        inhoud = {}
        for veld in INHOUD_VELDEN[kaart.type]:
            inhoud[veld] = getattr(form, veld).data or ''

        kaart.naam = _kaart_naam_uit_form(form, kaart.type)
        kaart.kerntaak = form.kerntaak.data or None
        kaart.set_inhoud(inhoud)
        kaart.bijgewerkt_door_id = current_user.id

        if request.form.get('verwijder_header_foto'):
            if kaart.header_foto:
                verwijder_bestand(kaart.header_foto)
                kaart.header_foto = None

        if form.header_foto.data and form.header_foto.data.filename:
            foto_naam, foto_fout = save_kaart_header_foto(form.header_foto.data, kaart.type)
            if foto_fout:
                flash(f'Foto: {foto_fout}', 'warning')
            else:
                if kaart.header_foto:
                    verwijder_bestand(kaart.header_foto)
                kaart.header_foto = foto_naam

        # Ensceneringstips foto
        if request.form.get('verwijder_ensceneringstips_foto'):
            if kaart.ensceneringstips_foto:
                verwijder_bestand(kaart.ensceneringstips_foto)
                kaart.ensceneringstips_foto = None

        tips_foto = request.files.get('ensceneringstips_foto')
        if tips_foto and tips_foto.filename:
            foto_naam, foto_fout = save_foto(tips_foto, prefix='tips', ratio=4/3)
            if foto_fout:
                flash(f'Tips-foto: {foto_fout}', 'warning')
            else:
                if kaart.ensceneringstips_foto:
                    verwijder_bestand(kaart.ensceneringstips_foto)
                kaart.ensceneringstips_foto = foto_naam

        # Afbeeldingen verwijderen
        verwijder_ids = request.form.getlist('verwijder_afbeelding_ids')
        for afb_id in verwijder_ids:
            afb = KaartAfbeelding.query.get(int(afb_id))
            if afb and afb.kaart_id == kaart.id:
                verwijder_bestand(afb.bestandsnaam)
                db.session.delete(afb)

        log_wijziging(kaart, 'Bewerkt', wijziging_toelichting)
        db.session.commit()
        flash(f'Kaart "{kaart.naam}" bijgewerkt.', 'success')
        if request.form.get('volgende_actie') == 'pdf':
            return redirect(url_for('kaarten.download_pdf', kaart_id=kaart.id))
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))

    # Override submit label voor bewerken
    form.submit.label.text = 'Wijzigingen opslaan'
    if hasattr(form, 'kenmerken_kerntaak'):
        form.kenmerken_kerntaak.label.text = kenmerken_kerntaak_label(form.kerntaak.data or kaart.kerntaak)
    type_info = KAART_TYPES[kaart.type]

    # Gekoppelde kaarten + beschikbare kaarten voor koppel-dropdown (alleen scenariokaart)
    gekoppelde_kaarten = []
    beschikbaar_per_type = {}
    if kaart.type == 'scenario':
        gekoppelde_kaarten = kaart.get_gekoppelde_kaarten()
        huidige_ids = {k.id for k in gekoppelde_kaarten}
        huidige_ids.add(kaart.id)
        for type_key, t_info in KAART_TYPES.items():
            rijen = Kaart.query.filter(
                Kaart.type == type_key,
                Kaart.status != 'gearchiveerd',
                ~Kaart.id.in_(huidige_ids),
            ).order_by(Kaart.nummer).all()
            if rijen:
                beschikbaar_per_type[type_key] = {'naam': t_info['naam'], 'kaarten': rijen}

    # Themakaart: koppelingen aan andere kaarten (per tussentitel) + QR-codes (één lijst)
    thema_kaart_links = {0: [], 1: [], 2: []}
    thema_qr_links_op_volgorde = []
    thema_beschikbare_kaarten_per_type = {}  # {'instructie': [...], 'scenario': [...], 'opdracht': [...]}
    thema_beschikbare_qrs_per_categorie = {}  # {'oefening': [...], ...}
    if kaart.type == 'thema':
        thema_kaart_links = kaart.get_thema_kaart_links()
        thema_qr_links_op_volgorde = kaart.get_thema_qr_links_op_volgorde()

        gekoppelde_ids = {r.gekoppelde_kaart_id for groep in thema_kaart_links.values() for r in groep}
        gekoppelde_ids.add(kaart.id)
        beschikbare_kaarten = Kaart.query.filter(
            Kaart.type != 'thema',
            Kaart.status != 'gearchiveerd',
            ~Kaart.id.in_(gekoppelde_ids),
        ).order_by(Kaart.nummer).all()
        # Groepeer per type, in vaste volgorde (instructie / scenario / opdracht)
        for type_key in ('instructie', 'scenario', 'opdracht'):
            kaarten = [k for k in beschikbare_kaarten if k.type == type_key]
            if kaarten:
                thema_beschikbare_kaarten_per_type[type_key] = {
                    'naam': KAART_TYPES[type_key]['naam'],
                    'kaarten': kaarten,
                }

        gekoppelde_qr_ids = {r.qr_code_id for r in thema_qr_links_op_volgorde}
        beschikbare_qrs = QRCode.query.filter(
            QRCode.actief == True,  # noqa: E712
            ~QRCode.id.in_(gekoppelde_qr_ids),
        ).order_by(QRCode.naam).all()
        # Groepeer per QR-categorie, in volgorde van QR_CATEGORIEEN
        for cat_key, cat_naam in QR_CATEGORIEEN.items():
            qrs = [q for q in beschikbare_qrs if q.categorie == cat_key]
            if qrs:
                thema_beschikbare_qrs_per_categorie[cat_key] = {
                    'naam': cat_naam,
                    'qrs': qrs,
                }

    return render_template('kaarten/formulier.html', form=form, kaart_type=kaart.type,
                           type_info=type_info, bewerken=True, kaart=kaart,
                           wijziging_toelichting=wijziging_toelichting,
                           toelichting_fout=toelichting_fout,
                           gekoppelde_kaarten=gekoppelde_kaarten,
                           beschikbaar_per_type=beschikbaar_per_type,
                           thema_kaart_links=thema_kaart_links,
                           thema_qr_links_op_volgorde=thema_qr_links_op_volgorde,
                           thema_beschikbare_kaarten_per_type=thema_beschikbare_kaarten_per_type,
                           thema_beschikbare_qrs_per_categorie=thema_beschikbare_qrs_per_categorie,
                           THEMA_MAX_KAARTEN_TOTAAL=THEMA_MAX_KAARTEN_TOTAAL,
                           THEMA_MAX_QR_TOP=THEMA_MAX_QR_TOP,
                           THEMA_MAX_QR_BOTTOM=THEMA_MAX_QR_BOTTOM,
                           THEMA_QR_LABEL_MAX=THEMA_QR_LABEL_MAX)


@bp.route('/<int:kaart_id>/koppel-actie', methods=['POST'])
@login_required
def koppel_actie(kaart_id):
    """Klein endpoint om koppelingen te beheren vanuit de verwijzingen-tab."""
    kaart = Kaart.query.get_or_404(kaart_id)
    actie = request.form.get('actie')
    doel_id = request.form.get('doel_id', type=int)
    if actie == 'toevoegen' and doel_id and doel_id != kaart.id:
        doel = Kaart.query.get(doel_id)
        if doel:
            bestaat = KaartKoppeling.query.filter(db.or_(
                db.and_(KaartKoppeling.kaart_id == kaart.id,
                        KaartKoppeling.gekoppelde_kaart_id == doel_id),
                db.and_(KaartKoppeling.kaart_id == doel_id,
                        KaartKoppeling.gekoppelde_kaart_id == kaart.id),
            )).first()
            if not bestaat:
                db.session.add(KaartKoppeling(
                    kaart_id=kaart.id,
                    gekoppelde_kaart_id=doel_id,
                    aangemaakt_door_id=current_user.id,
                ))
                log_wijziging(kaart, 'Koppeling toegevoegd', f'Gekoppeld aan {doel.nummer} — {doel.naam}')
                db.session.commit()
                flash(f'Kaart gekoppeld aan {doel.nummer}.', 'success')
            else:
                flash('Deze koppeling bestaat al.', 'info')
    elif actie == 'verwijderen' and doel_id:
        gewist = KaartKoppeling.query.filter(db.or_(
            db.and_(KaartKoppeling.kaart_id == kaart.id,
                    KaartKoppeling.gekoppelde_kaart_id == doel_id),
            db.and_(KaartKoppeling.kaart_id == doel_id,
                    KaartKoppeling.gekoppelde_kaart_id == kaart.id),
        )).delete()
        if gewist:
            doel = Kaart.query.get(doel_id)
            if doel:
                log_wijziging(kaart, 'Koppeling verwijderd', f'Loskoppeld van {doel.nummer} — {doel.naam}')
            db.session.commit()
            flash('Koppeling losgekoppeld.', 'success')
    return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + '#tab-verwijzingen')


# ====================== THEMAKAART KOPPELINGEN ======================

@bp.route('/<int:kaart_id>/thema/kaart-link/toevoegen', methods=['POST'])
@login_required
def thema_kaart_link_toevoegen(kaart_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    if kaart.type != 'thema':
        abort(404)
    doel_id = request.form.get('doel_id', type=int)
    groep_index = request.form.get('groep_index', type=int)
    if groep_index is None or groep_index not in (0, 1, 2) or not doel_id:
        flash('Ongeldige koppeling.', 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))
    if doel_id == kaart.id:
        flash('Een themakaart kan niet aan zichzelf gekoppeld worden.', 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))
    # Limiet: max 20 totaal
    if kaart.get_thema_aantal_kaarten() >= THEMA_MAX_KAARTEN_TOTAAL:
        flash(f'Maximaal {THEMA_MAX_KAARTEN_TOTAAL} kaart-koppelingen op een themakaart.', 'warning')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))
    doel = Kaart.query.get(doel_id)
    if not doel or doel.type == 'thema':
        flash('Kaart niet gevonden of zelf een themakaart.', 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))
    bestaat = ThemaKaartLink.query.filter_by(kaart_id=kaart.id, gekoppelde_kaart_id=doel_id).first()
    if bestaat:
        flash('Deze kaart is al gekoppeld.', 'info')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))
    # Volgende volgorde-nummer in deze groep
    laatste = ThemaKaartLink.query.filter_by(kaart_id=kaart.id, groep_index=groep_index)\
                                  .order_by(ThemaKaartLink.volgorde.desc()).first()
    volgorde = (laatste.volgorde + 1) if laatste else 0
    db.session.add(ThemaKaartLink(
        kaart_id=kaart.id,
        gekoppelde_kaart_id=doel_id,
        groep_index=groep_index,
        volgorde=volgorde,
    ))
    log_wijziging(kaart, 'Kaart-koppeling toegevoegd',
                  f'{doel.nummer} — {doel.naam} (groep {groep_index + 1})')
    db.session.commit()
    flash(f'Kaart {doel.nummer} toegevoegd aan groep {groep_index + 1}.', 'success')
    return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))


@bp.route('/<int:kaart_id>/thema/kaart-link/<int:link_id>/verwijderen', methods=['POST'])
@login_required
def thema_kaart_link_verwijderen(kaart_id, link_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    if kaart.type != 'thema':
        abort(404)
    link = ThemaKaartLink.query.filter_by(id=link_id, kaart_id=kaart.id).first()
    if not link:
        abort(404)
    doel = link.gekoppelde_kaart
    db.session.delete(link)
    if doel:
        log_wijziging(kaart, 'Kaart-koppeling verwijderd', f'{doel.nummer} — {doel.naam}')
    db.session.commit()
    flash('Kaart-koppeling verwijderd.', 'info')
    return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))


@bp.route('/<int:kaart_id>/thema/qr-link/toevoegen', methods=['POST'])
@login_required
def thema_qr_link_toevoegen(kaart_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    if kaart.type != 'thema':
        abort(404)
    qr_id = request.form.get('qr_id', type=int)
    label = (request.form.get('label') or '').strip()[:THEMA_QR_LABEL_MAX]
    if not qr_id:
        flash('Geen QR-code geselecteerd.', 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))
    qr = QRCode.query.get(qr_id)
    if not qr:
        flash('QR-code niet gevonden.', 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))
    qr_max = THEMA_MAX_QR_TOP + THEMA_MAX_QR_BOTTOM
    aantal_totaal = ThemaQRLink.query.filter_by(kaart_id=kaart.id).count()
    if aantal_totaal >= qr_max:
        flash(f'Maximaal {qr_max} QR-codes op een themakaart.', 'warning')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))
    bestaat = ThemaQRLink.query.filter_by(kaart_id=kaart.id, qr_code_id=qr_id).first()
    if bestaat:
        flash('Deze QR-code is al gekoppeld aan deze themakaart.', 'info')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))
    # Nieuwe items komen achteraan
    laatste = ThemaQRLink.query.filter_by(kaart_id=kaart.id)\
                               .order_by(ThemaQRLink.volgorde.desc()).first()
    volgorde = (laatste.volgorde + 1) if laatste else 0
    db.session.add(ThemaQRLink(
        kaart_id=kaart.id,
        qr_code_id=qr_id,
        rij='auto',
        volgorde=volgorde,
        label=label,
    ))
    log_wijziging(kaart, 'QR-koppeling toegevoegd', qr.naam)
    db.session.commit()
    flash(f'QR-code "{qr.naam}" toegevoegd.', 'success')
    return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))


def _wissel_qr_volgorde(kaart, link, richting):
    """Wissel volgorde tussen 'link' en de buur in 'richting' ('omhoog' / 'omlaag')."""
    alle = list(ThemaQRLink.query.filter_by(kaart_id=kaart.id)
                                  .order_by(ThemaQRLink.volgorde, ThemaQRLink.id).all())
    try:
        idx = alle.index(link)
    except ValueError:
        return False
    buur_idx = idx - 1 if richting == 'omhoog' else idx + 1
    if buur_idx < 0 or buur_idx >= len(alle):
        return False
    buur = alle[buur_idx]
    link.volgorde, buur.volgorde = buur.volgorde, link.volgorde
    db.session.commit()
    return True


@bp.route('/<int:kaart_id>/thema/qr-link/<int:link_id>/omhoog', methods=['POST'])
@login_required
def thema_qr_link_omhoog(kaart_id, link_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    if kaart.type != 'thema':
        abort(404)
    link = ThemaQRLink.query.filter_by(id=link_id, kaart_id=kaart.id).first()
    if not link:
        abort(404)
    _wissel_qr_volgorde(kaart, link, 'omhoog')
    return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + '#tab-qr')


@bp.route('/<int:kaart_id>/thema/qr-link/<int:link_id>/omlaag', methods=['POST'])
@login_required
def thema_qr_link_omlaag(kaart_id, link_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    if kaart.type != 'thema':
        abort(404)
    link = ThemaQRLink.query.filter_by(id=link_id, kaart_id=kaart.id).first()
    if not link:
        abort(404)
    _wissel_qr_volgorde(kaart, link, 'omlaag')
    return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + '#tab-qr')


@bp.route('/<int:kaart_id>/thema/qr-link/<int:link_id>/verwijderen', methods=['POST'])
@login_required
def thema_qr_link_verwijderen(kaart_id, link_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    if kaart.type != 'thema':
        abort(404)
    link = ThemaQRLink.query.filter_by(id=link_id, kaart_id=kaart.id).first()
    if not link:
        abort(404)
    qr_naam = link.qr_code.naam if link.qr_code else 'onbekend'
    db.session.delete(link)
    log_wijziging(kaart, 'QR-koppeling verwijderd', qr_naam)
    db.session.commit()
    flash('QR-koppeling verwijderd.', 'info')
    return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))


@bp.route('/<int:kaart_id>/thema/qr-link/<int:link_id>/label', methods=['POST'])
@login_required
def thema_qr_link_label(kaart_id, link_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    if kaart.type != 'thema':
        abort(404)
    link = ThemaQRLink.query.filter_by(id=link_id, kaart_id=kaart.id).first()
    if not link:
        abort(404)
    nieuw_label = (request.form.get('label') or '').strip()[:THEMA_QR_LABEL_MAX]
    link.label = nieuw_label
    db.session.commit()
    return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))


def _zoek_kaarten(query_string, kaart_type=None, status=None, exclude_id=None):
    """Doorzoek naam, nummer en JSON-inhoud."""
    q = Kaart.query
    if kaart_type and kaart_type in KAART_TYPES:
        q = q.filter(Kaart.type == kaart_type)
    if status:
        q = q.filter(Kaart.status == status)
    else:
        q = q.filter(Kaart.status != 'gearchiveerd')
    if exclude_id:
        q = q.filter(Kaart.id != exclude_id)
    if query_string:
        zoekterm = f'%{query_string}%'
        q = q.filter(db.or_(
            Kaart.naam.ilike(zoekterm),
            Kaart.nummer.ilike(zoekterm),
            Kaart.inhoud.ilike(zoekterm),
        ))
    return q.order_by(Kaart.bijgewerkt_op.desc()).all()


@bp.route('/zoeken')
@login_required
def zoeken():
    query_string = (request.args.get('q') or '').strip()
    kaart_type = request.args.get('type') or ''
    status = request.args.get('status') or ''
    resultaten = _zoek_kaarten(query_string, kaart_type or None, status or None) if query_string else []
    return render_template('kaarten/zoeken.html', resultaten=resultaten,
                           q=query_string, kaart_type=kaart_type, status=status,
                           types=KAART_TYPES)


@bp.route('/<int:kaart_id>/publiceren', methods=['POST'])
@login_required
def publiceren(kaart_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    if kaart.status == 'gearchiveerd':
        flash('Gearchiveerde kaarten kunnen niet direct gepubliceerd worden. Heractiveer eerst.', 'warning')
        return redirect(url_for('kaarten.overzicht'))

    # Bepaal of er sinds de laatste publicatie iets is gewijzigd
    nieuwe_versie = False
    if (kaart.versie or 0) == 0:
        nieuwe_versie = True
    else:
        gewijzigd_sinds = KaartWijziging.query.filter(
            KaartWijziging.kaart_id == kaart.id,
            KaartWijziging.actie.in_(['Bewerkt', 'Aangemaakt']),
            KaartWijziging.datum > kaart.versie_datum,
        ).first()
        if gewijzigd_sinds:
            nieuwe_versie = True

    kaart.status = 'gepubliceerd'
    kaart.bijgewerkt_door_id = current_user.id

    if nieuwe_versie:
        from datetime import datetime as _dt
        kaart.versie = (kaart.versie or 0) + 1
        kaart.versie_datum = _dt.utcnow()
        log_wijziging(kaart, 'Gepubliceerd', f'Versie {kaart.versie}')
        db.session.commit()
        flash(f'Kaart "{kaart.naam}" is gepubliceerd als versie {kaart.versie}.', 'success')
    else:
        log_wijziging(kaart, 'Gepubliceerd', f'Geen wijzigingen — blijft versie {kaart.versie}')
        db.session.commit()
        flash(f'Kaart "{kaart.naam}" is opnieuw gepubliceerd. Geen wijzigingen, blijft versie {kaart.versie}.', 'info')

    return redirect(url_for('kaarten.overzicht'))


@bp.route('/<int:kaart_id>/depubliceren', methods=['POST'])
@login_required
def depubliceren(kaart_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    kaart.status = 'concept'
    kaart.bijgewerkt_door_id = current_user.id
    log_wijziging(kaart, 'Teruggezet naar concept')
    db.session.commit()
    flash(f'Kaart "{kaart.naam}" staat weer als concept.', 'info')
    return redirect(url_for('kaarten.overzicht'))


@bp.route('/<int:kaart_id>/archiveren', methods=['POST'])
@login_required
def archiveren(kaart_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    kaart.status = 'gearchiveerd'
    kaart.bijgewerkt_door_id = current_user.id
    log_wijziging(kaart, 'Gearchiveerd')
    db.session.commit()
    flash(f'Kaart "{kaart.naam}" is gearchiveerd.', 'info')
    return redirect(url_for('kaarten.overzicht'))


@bp.route('/<int:kaart_id>/heractiveren', methods=['POST'])
@login_required
def heractiveren(kaart_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    kaart.status = 'concept'
    kaart.bijgewerkt_door_id = current_user.id
    log_wijziging(kaart, 'Uit archief gehaald')
    db.session.commit()
    flash(f'Kaart "{kaart.naam}" is uit het archief gehaald en staat nu als concept.', 'success')
    return redirect(url_for('kaarten.overzicht'))


@bp.route('/<int:kaart_id>/kopieren', methods=['POST'])
@login_required
def kopieren(kaart_id):
    origineel = Kaart.query.get_or_404(kaart_id)
    nieuwe = Kaart(
        type=origineel.type,
        nummer=Kaart.volgende_nummer(origineel.type),
        naam=f'{origineel.naam} (kopie)',
        status='concept',
        inhoud=origineel.inhoud,
        auteur_id=current_user.id,
        bijgewerkt_door_id=current_user.id,
    )
    db.session.add(nieuwe)
    db.session.flush()

    # Afbeeldingen kopiëren (bestand + record)
    upload_folder = current_app.config['UPLOAD_FOLDER']
    for afb in origineel.afbeeldingen:
        ext = afb.bestandsnaam.rsplit('.', 1)[-1] if '.' in afb.bestandsnaam else ''
        nieuwe_naam = f'{uuid.uuid4().hex}.{ext}' if ext else uuid.uuid4().hex
        bron = os.path.join(upload_folder, afb.bestandsnaam)
        doel = os.path.join(upload_folder, nieuwe_naam)
        try:
            if os.path.exists(bron):
                shutil.copyfile(bron, doel)
        except OSError:
            continue
        db.session.add(KaartAfbeelding(
            kaart_id=nieuwe.id,
            bestandsnaam=nieuwe_naam,
            originele_naam=afb.originele_naam,
            beschrijving=afb.beschrijving,
        ))

    log_wijziging(nieuwe, 'Aangemaakt', f'Gekopieerd van {origineel.nummer}')
    db.session.commit()
    flash(f'Kopie aangemaakt als {nieuwe.nummer}.', 'success')
    return redirect(url_for('kaarten.bewerken', kaart_id=nieuwe.id))


@bp.route('/<int:kaart_id>/pdf')
@login_required
def download_pdf(kaart_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    from app.kaarten.pdf import genereer_pdf
    pdf = genereer_pdf(kaart)
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    bestandsnaam = f'{kaart.nummer}_{kaart.naam}.pdf'.replace(' ', '_')
    response.headers['Content-Disposition'] = f'inline; filename="{bestandsnaam}"'
    return response
