import os
import json
import uuid
import shutil
import re
import bleach
from flask import render_template, redirect, url_for, flash, request, current_app, make_response, abort
from flask_login import login_required, current_user
from PIL import Image, ImageOps
from app import db


# === Kenniskaart rich-text sanitizer ===
# Whitelist van tags/attributen die de editor mag produceren. Alles daarbuiten
# wordt door bleach.clean() gestript — beschermt tegen XSS bij weergave in de app
# en tegen ongewenste opmaak in de PDF (fonts, inline scripts, etc.).
KENNIS_TOEGESTANE_TAGS = [
    'p', 'br', 'strong', 'b', 'em', 'i', 'u',
    'h3', 'h4',
    'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'span',
]
# Enige attribuut dat we toelaten is `style="color: <hex>"` op <span>. Kleuren
# worden verder gefilterd tot de 3 huisstijl-kleuren (navy, brandweer-rood, zwart).
KENNIS_HUISSTIJL_KLEUREN = {'#1B2A4A', '#C8102E', '#000000', '#1b2a4a', '#c8102e'}
_KLEUR_STYLE_RE = re.compile(r'color\s*:\s*(#[0-9a-fA-F]{6})', re.IGNORECASE)


def _kennis_style_filter(tag, name, value):
    """bleach attribute-filter: sta alleen 'style' toe op <span>, alleen met
    color-waarde uit de huisstijl. Al het andere wordt geweigerd."""
    if name != 'style' or tag != 'span':
        return False
    match = _KLEUR_STYLE_RE.search(value or '')
    if not match:
        return False
    kleur = match.group(1)
    return kleur in KENNIS_HUISSTIJL_KLEUREN


def sanitize_kennis_html(ruw_html):
    """Sanitize + normaliseer kenniskaart-rich-text. Leverstijl: één HTML-string
    die veilig in Jinja én in WeasyPrint gerenderd kan worden."""
    if not ruw_html:
        return ''
    schoon = bleach.clean(
        ruw_html,
        tags=KENNIS_TOEGESTANE_TAGS,
        attributes={'span': _kennis_style_filter},
        strip=True,
    )
    # Reduceer whitespace-blokken en verwijder lege paragrafen die contenteditable
    # vaak achterlaat na een leegmaken-actie.
    schoon = re.sub(r'<p>\s*(?:<br\s*/?>)?\s*</p>', '', schoon)
    return schoon.strip()


def verwerk_kennis_hoofdstukken(json_string):
    """Parse kenniskaart-hoofdstukken-JSON (v0.7.13): per hoofdstuk sanitize
    tekst_html met bleach én upload nieuwe foto's per slot.

    Per hoofdstuk: {slot, titel, tekst_html, fotos:[{slot,bestand,...}], foto_orientatie}.
    File-inputs volgen dezelfde conventie als werkwijze: 'werkwijze_foto_<slot>'
    en 'werkwijze_orig_<slot>' — zo hoeven we de cropper-JS niet te dupliceren.
    """
    # Alleen import hier — bovenaan zit er al een globale import, dit voorkomt
    # herhaling van de namespace-vermelding lager in de functie.
    from app.kaarten.forms import (KENNIS_MAX_HOOFDSTUKKEN,
                                    KENNIS_MAX_FOTOS_PER_HOOFDSTUK,
                                    KENNIS_HOOFDSTUK_TITEL_MAX)
    try:
        hoofdstukken = json.loads(json_string or '[]')
    except (ValueError, TypeError):
        return json_string or '[]'
    if not isinstance(hoofdstukken, list):
        return json_string or '[]'

    schoon = []
    for h in hoofdstukken[:KENNIS_MAX_HOOFDSTUKKEN]:
        if not isinstance(h, dict):
            continue
        slot = (h.get('slot') or '').strip()
        titel = (h.get('titel') or '').strip()[:KENNIS_HOOFDSTUK_TITEL_MAX]
        tekst_html = sanitize_kennis_html(h.get('tekst_html') or '')
        foto_orientatie = (h.get('foto_orientatie') or 'liggend').strip()
        foto_plaatsing = (h.get('foto_plaatsing') or 'onder').strip()
        if foto_plaatsing not in ('onder', 'naast'):
            foto_plaatsing = 'onder'

        nieuwe_fotos = []
        for foto in (h.get('fotos') or [])[:KENNIS_MAX_FOTOS_PER_HOOFDSTUK]:
            if not isinstance(foto, dict):
                continue
            f_slot = (foto.get('slot') or '').strip()
            bestand = (foto.get('bestand') or '').strip()
            bestand_origineel = (foto.get('bestand_origineel') or '').strip()
            if f_slot:
                orig_upload = request.files.get('werkwijze_orig_' + f_slot)
                if orig_upload and orig_upload.filename:
                    orig_naam, orig_fout = save_foto(orig_upload, prefix='kennis_h_orig', ratio=None)
                    if orig_naam:
                        if bestand_origineel:
                            verwijder_bestand(bestand_origineel)
                        bestand_origineel = orig_naam
                    elif orig_fout:
                        flash(f'Hoofdstuk-foto (origineel): {orig_fout}', 'warning')
                crop_upload = request.files.get('werkwijze_foto_' + f_slot)
                if crop_upload and crop_upload.filename:
                    foto_naam, foto_fout = save_foto(crop_upload, prefix='kennis_h', ratio=None)
                    if foto_naam:
                        if bestand:
                            verwijder_bestand(bestand)
                        bestand = foto_naam
                    elif foto_fout:
                        flash(f'Hoofdstuk-foto: {foto_fout}', 'warning')
                if bestand and not bestand_origineel:
                    bestand_origineel = bestand
            nieuwe_fotos.append({
                'slot': f_slot,
                'bestand': bestand,
                'bestand_origineel': bestand_origineel,
            })

        schoon.append({
            'slot': slot,
            'titel': titel,
            'tekst_html': tekst_html,
            'fotos': nieuwe_fotos,
            'foto_orientatie': foto_orientatie,
            'foto_plaatsing': foto_plaatsing,
        })
    return json.dumps(schoon, ensure_ascii=False)
from app.models import (Kaart, KaartAfbeelding, KaartWijziging, KaartKoppeling,
                         ThemaKaartLink, ThemaQRLink, InstructieQRLink, QRCode,
                         Kerntaak, Subcategorie,
                         KAART_TYPES, KERNTAKEN, QR_CATEGORIEEN, kenmerken_kerntaak_label,
                         THEMA_MAX_KAARTEN_TOTAAL, THEMA_MAX_QR_TOP, THEMA_MAX_QR_BOTTOM,
                         THEMA_QR_LABEL_MAX,
                         INSTRUCTIE_MAX_KAART_KOPPELINGEN, INSTRUCTIE_MAX_QR_KOPPELINGEN)


def _hernummer_als_subcategorie_wijzigt(kaart):
    """Update kaart.nummer als de kerntaak+subcategorie-prefix niet meer klopt.

    Rebecca's keuze (v0.7.8): het nummer volgt ALTIJD de gekozen kerntaak +
    subcategorie — ook bij gepubliceerde kaarten. Geprinte materialen kunnen
    dan verouderd raken, dat is een bewust risico. Retourneert (oud, nieuw)
    tuple wanneer er hernummerd is, anders (None, None).
    """
    if not kaart.subcategorie_id:
        return None, None
    sub = kaart.subcategorie
    if sub is None or sub.kerntaak is None:
        return None, None
    letters = KAART_TYPES[kaart.type]['prefix']
    verwachte_start = f'{letters}-{sub.kerntaak.cijfer}{sub.cijfer}'
    if kaart.nummer and kaart.nummer.startswith(verwachte_start):
        # Prefix klopt al — nummer blijft ongewijzigd
        return None, None
    oud_nummer = kaart.nummer
    kaart.nummer = Kaart.volgende_nummer(kaart.type, sub.kerntaak.cijfer, sub.cijfer)
    return oud_nummer, kaart.nummer


def _subcategorieen_per_kerntaak_ctx():
    """Voor JS: dict van kerntaak-sleutel → lijst met subcategorieën.

    Wordt naar formulier.html gestuurd zodat de subcategorie-dropdown
    dynamisch aangepast kan worden als de gebruiker een andere kerntaak kiest.
    """
    ctx = {}
    for kt in Kerntaak.query.order_by(Kerntaak.cijfer).all():
        subs = [{'id': s.id, 'cijfer': s.cijfer, 'naam': s.naam}
                for s in kt.subcategorieen.order_by(Subcategorie.cijfer).all()]
        ctx[kt.sleutel] = {
            'cijfer': kt.cijfer,
            'afkorting': kt.afkorting,
            'kleur': kt.kleur,
            'subs': subs,
        }
    return ctx
from app.kaarten import bp
from app.kaarten.forms import (FORMULIEREN, INHOUD_VELDEN, INHOUD_LIJST_VELDEN,
                                WERKWIJZE_MAX_STAPPEN, WERKWIJZE_TITEL_MAX,
                                WERKWIJZE_TEKST_MAX,
                                VEILIGHEID_MAX_ZINNEN, VEILIGHEID_ZIN_MAX,
                                KENNIS_MAX_HOOFDSTUKKEN, KENNIS_MAX_FOTOS_PER_HOOFDSTUK,
                                KENNIS_HOOFDSTUK_TITEL_MAX)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
HEADER_FOTO_MAX_BYTES = 20 * 1024 * 1024
HEADER_FOTO_MIN_W = 900
HEADER_FOTO_MIN_H = 300
HEADER_FOTO_RATIO = 3.0


# --- Autorisatie -------------------------------------------------------------
# Rollen (oplopend): medewerker < coordinator < specialist < admin.
# - admin / specialist: mogen alles op alle kaarten
# - coordinator: mag kaarten aanmaken en enkel eigen kaarten bewerken
# - medewerker: alleen-lezen
BEWERK_ROLLEN = {'admin', 'specialist'}
AANMAAK_ROLLEN = {'admin', 'specialist', 'coordinator'}
PUBLICATIE_ROLLEN = {'admin', 'specialist'}


def kan_kaart_bewerken(user, kaart):
    if not (user and user.is_authenticated):
        return False
    if user.rol in BEWERK_ROLLEN:
        return True
    if user.rol == 'coordinator' and kaart.auteur_id == user.id:
        return True
    return False


def kan_kaart_aanmaken(user):
    return bool(user and user.is_authenticated and user.rol in AANMAAK_ROLLEN)


def kan_publiceren(user):
    return bool(user and user.is_authenticated and user.rol in PUBLICATIE_ROLLEN)


def _vereis_bewerken(kaart):
    """Roep bovenaan elke write-route aan; stopt de request als de gebruiker niet mag."""
    if not kan_kaart_bewerken(current_user, kaart):
        abort(403)


def _vereis_aanmaken():
    if not kan_kaart_aanmaken(current_user):
        abort(403)


def _vereis_publiceren():
    if not kan_publiceren(current_user):
        abort(403)


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
        return None, 'Bestand is te groot (max 20 MB).'
    img = Image.open(file)
    # Pas EXIF-orientatie expliciet toe — anders bewaart PIL de fysieke pixels
    # zonder de rotatie-metadata, waardoor de foto op zijn kant in de PDF kan komen.
    img = ImageOps.exif_transpose(img)
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


FOTO_MAX_BYTES = 20 * 1024 * 1024
FOTO_MIN_PX = 200  # minimaal 200px breed of hoog


def save_foto(file, prefix='foto', ratio=None):
    """Flexibele foto-opslag. Optionele ratio-crop (bijv. 4/3)."""
    if not file or not file.filename or not allowed_file(file.filename):
        return None, 'Ongeldig bestandstype. Gebruik JPG, PNG of WebP.'
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > FOTO_MAX_BYTES:
        return None, 'Bestand is te groot (max 20 MB).'
    img = Image.open(file)
    # Pas EXIF-orientatie expliciet toe — anders bewaart PIL de fysieke pixels
    # zonder de rotatie-metadata, waardoor de foto op zijn kant in de PDF kan komen.
    img = ImageOps.exif_transpose(img)
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


def _controleer_verplichte_velden(kaart_type, form, request_form, kaart=None):
    """Extra 'verplichte veld'-checks per kaart-type bovenop WTForms-validators.

    Uitgangspunt: alleen bij bewerken (expliciet 'Opslaan') — niet bij aanmaken
    en niet bij auto-save. Voegt foutmeldingen toe aan form.<veld>.errors zodat
    ze in de banner en per tab getoond worden. Geeft een lijst met de ontbrekende
    labels terug (leeg = alles OK).
    """
    def _add_error(veld_naam, boodschap):
        veld = getattr(form, veld_naam, None)
        if veld is None:
            return
        veld.errors = list(veld.errors) + [boodschap]

    def _tekst_leeg(veld_naam):
        return not (request_form.get(veld_naam) or '').strip()

    def _lijst_min(veld_naam, min_aantal=1):
        val = request_form.get(veld_naam) or ''
        regels = [r for r in val.split('\n') if r.strip()]
        return len(regels) < min_aantal

    def _json_lijst_min(veld_naam, min_aantal=1, key_check=None):
        try:
            data = json.loads(request_form.get(veld_naam) or '[]')
        except (ValueError, TypeError):
            data = []
        if not isinstance(data, list):
            return True
        if key_check:
            gevuld = [x for x in data if isinstance(x, dict) and key_check(x)]
        else:
            gevuld = [x for x in data if x]
        return len(gevuld) < min_aantal

    ontbrekend = []

    # Verplicht voor élk kaart-type: subcategorie (bepaalt 2e cijfer van nummer).
    subcat_raw = (request_form.get('subcategorie_id') or '').strip()
    if not subcat_raw.isdigit():
        _add_error('subcategorie_id',
                    'Kies een subcategorie — die bepaalt de laatste 2 cijfers van het kaartnummer.')
        ontbrekend.append('Subcategorie')

    if kaart_type == 'thema':
        if _tekst_leeg('ondertitel'):
            _add_error('ondertitel', 'Vul een ondertitel in.')
            ontbrekend.append('Ondertitel')
        tussentitels = [(request_form.get(f'tussentitel_{i}') or '').strip() for i in (1, 2, 3, 4)]
        if not any(tussentitels):
            _add_error('tussentitel_1', 'Vul minstens 1 tussentitel in.')
            ontbrekend.append('Tussentitel')
        elif kaart is not None:
            aantal_links = ThemaKaartLink.query.filter_by(kaart_id=kaart.id).count()
            if aantal_links < 1:
                _add_error('tussentitel_1', 'Koppel minstens 1 kaart onder een tussentitel.')
                ontbrekend.append('Gekoppelde kaart')

    elif kaart_type == 'instructie':
        if _tekst_leeg('omschrijving'):
            _add_error('omschrijving', 'Vul een omschrijving in.')
            ontbrekend.append('Omschrijving')
        try:
            werkwijze = json.loads(request_form.get('werkwijze_stappen_json') or '[]')
        except (ValueError, TypeError):
            werkwijze = []
        gevuld = [
            s for s in werkwijze
            if isinstance(s, dict) and (
                (s.get('titel') or '').strip()
                or (s.get('tekst') or '').strip()
                or (s.get('fotos') or [])
            )
        ]
        if len(gevuld) < 1:
            _add_error('werkwijze_stappen_json', 'Voeg minstens 1 werkwijze-stap toe.')
            ontbrekend.append('Werkwijze-stap')

    elif kaart_type == 'scenario':
        if _tekst_leeg('doelgroep'):
            _add_error('doelgroep', 'Kies een doelgroep.')
            ontbrekend.append('Doelgroep')
        try:
            oefenleider_n = int(request_form.get('oefenleider_aantal') or 0)
        except (ValueError, TypeError):
            oefenleider_n = 0
        if oefenleider_n < 1:
            _add_error('oefenleider_aantal', 'Minstens 1 oefenleider vereist.')
            ontbrekend.append('Aantal oefenleiders')
        if _tekst_leeg('tijdsduur'):
            _add_error('tijdsduur', 'Kies een tijdsduur.')
            ontbrekend.append('Tijdsduur')
        if _tekst_leeg('aanleiding_doelen'):
            _add_error('aanleiding_doelen', 'Vul een oefendoel in.')
            ontbrekend.append('Oefendoel')
        if _json_lijst_min('oefenmiddelen_basis', 1):
            _add_error('oefenmiddelen_basis', 'Voeg minstens 1 basis-oefenmiddel toe.')
            ontbrekend.append('Basis-oefenmiddelen')
        if _tekst_leeg('pager_prio'):
            _add_error('pager_prio', 'Kies een prioriteit.')
            ontbrekend.append('Pager-prioriteit')
        if _tekst_leeg('pager_soort'):
            _add_error('pager_soort', 'Kies een soort melding.')
            ontbrekend.append('Pager-soort')
        if _tekst_leeg('scenariobeschrijving'):
            _add_error('scenariobeschrijving', 'Vul een scenariobeschrijving in.')
            ontbrekend.append('Scenariobeschrijving')
        for veld, label in [
            ('kenmerken_kerntaak', 'Kerntaak-kenmerken'),
            ('gebouwkenmerken', 'Gebouwkenmerken'),
            ('menskenmerken', 'Menskenmerken'),
            ('omgevingskenmerken', 'Omgevingskenmerken'),
            ('interventiekenmerken', 'Interventiekenmerken'),
        ]:
            if _tekst_leeg(veld):
                _add_error(veld, f'Vul {label.lower()} in.')
                ontbrekend.append(label)
        if _lijst_min('evaluatie', 1):
            _add_error('evaluatie', 'Voeg minstens 1 evaluatie-punt toe.')
            ontbrekend.append('Evaluatie')

    elif kaart_type == 'opdracht':
        if _tekst_leeg('doelgroep'):
            _add_error('doelgroep', 'Kies een doelgroep.')
            ontbrekend.append('Doelgroep')
        if _json_lijst_min('oefenmiddelen_basis', 1):
            _add_error('oefenmiddelen_basis', 'Voeg minstens 1 basis-oefenmiddel toe.')
            ontbrekend.append('Basis-oefenmiddelen')
        if _tekst_leeg('oefendoel'):
            _add_error('oefendoel', 'Vul een oefendoel in.')
            ontbrekend.append('Oefendoel')
        if _json_lijst_min(
                'opdrachten_json', 1,
                key_check=lambda o: bool((o.get('titel') or '').strip()
                                          or (o.get('tekst') or '').strip())):
            _add_error('opdrachten_json', 'Voeg minstens 1 opdracht toe.')
            ontbrekend.append('Opdracht')
        if _lijst_min('evaluatie', 1):
            _add_error('evaluatie', 'Voeg minstens 1 evaluatie-punt toe.')
            ontbrekend.append('Evaluatie')

    elif kaart_type == 'kennis':
        if _tekst_leeg('doelgroep'):
            _add_error('doelgroep', 'Kies een doelgroep.')
            ontbrekend.append('Doelgroep')
        if _tekst_leeg('doel'):
            _add_error('doel', 'Vul een doel in.')
            ontbrekend.append('Doel')
        # Kernboodschap: minstens 1 hoofdstuk met titel of tekst of foto.
        try:
            hoofdstukken = json.loads(request_form.get('kernboodschap_hoofdstukken_json') or '[]')
        except (ValueError, TypeError):
            hoofdstukken = []
        gevuld = []
        for h in hoofdstukken:
            if not isinstance(h, dict):
                continue
            titel = (h.get('titel') or '').strip()
            tekst_plat = re.sub(r'<[^>]+>', ' ', h.get('tekst_html') or '')
            tekst_plat = re.sub(r'\s+', ' ', tekst_plat).strip()
            fotos = [f for f in (h.get('fotos') or [])
                     if isinstance(f, dict) and (f.get('bestand') or '').strip()]
            if titel or tekst_plat or fotos:
                gevuld.append(h)
        if not gevuld:
            _add_error('kernboodschap_hoofdstukken_json',
                        'Voeg minstens 1 hoofdstuk toe met een titel, tekst of foto.')
            ontbrekend.append('Kernboodschap-hoofdstuk')
        if _lijst_min('evaluatie', 1):
            _add_error('evaluatie', 'Voeg minstens 1 evaluatie-punt toe.')
            ontbrekend.append('Evaluatie')

    return ontbrekend


def verwerk_tips_fotos(json_string):
    """Parse tips-JSON (max 3 tips, elk met max 2 foto's), upload nieuwe foto's per
    slot-id, return bijgewerkte JSON.

    Per tip.fotos[i] = {'slot': '<uuid>', 'bestand': '<filename>',
                        'bestand_origineel': '<filename>'}.
    File-inputs: 'tips_foto_<slot>' (gecropt) en 'tips_orig_<slot>' (origineel).
    """
    try:
        tips = json.loads(json_string or '[]')
    except (ValueError, TypeError):
        return json_string or '[]'
    if not isinstance(tips, list):
        return json_string or '[]'

    for tip in tips[:3]:  # veiligheidsklem: max 3 tips
        if not isinstance(tip, dict):
            continue
        fotos = tip.get('fotos') or []
        nieuwe_fotos = []
        for foto in fotos[:2]:  # veiligheidsklem: max 2 foto's per tip
            if not isinstance(foto, dict):
                continue
            slot = (foto.get('slot') or '').strip()
            bestand = (foto.get('bestand') or '').strip()
            bestand_origineel = (foto.get('bestand_origineel') or '').strip()
            if slot:
                orig_upload = request.files.get('tips_orig_' + slot)
                if orig_upload and orig_upload.filename:
                    orig_naam, orig_fout = save_foto(orig_upload, prefix='tips_orig', ratio=None)
                    if orig_naam:
                        if bestand_origineel:
                            verwijder_bestand(bestand_origineel)
                        bestand_origineel = orig_naam
                    elif orig_fout:
                        flash(f'Tips-foto (origineel): {orig_fout}', 'warning')
                crop_upload = request.files.get('tips_foto_' + slot)
                if crop_upload and crop_upload.filename:
                    foto_naam, foto_fout = save_foto(crop_upload, prefix='tips', ratio=None)
                    if foto_naam:
                        if bestand:
                            verwijder_bestand(bestand)
                        bestand = foto_naam
                    elif foto_fout:
                        flash(f'Tips-foto: {foto_fout}', 'warning')
                if bestand and not bestand_origineel:
                    bestand_origineel = bestand
            nieuwe_fotos.append({
                'slot': slot,
                'bestand': bestand,
                'bestand_origineel': bestand_origineel,
            })
        tip['fotos'] = nieuwe_fotos
        # v0.7.31 rich-text: sanitize tekst_html per tip.
        if tip.get('tekst_html'):
            tip['tekst_html'] = sanitize_kennis_html(tip.get('tekst_html'))
    return json.dumps(tips[:3], ensure_ascii=False)


def verwerk_opdracht_fotos(json_string):
    """Parse opdracht-JSON (platte lijst van opdrachten met max 2 foto's per opdracht),
    upload nieuwe foto's per slot-id, return bijgewerkte JSON.

    Per opdracht.fotos[i] = {'slot': '<uuid>', 'bestand': '<filename>',
                             'bestand_origineel': '<filename>'}.
    Bij een geüploade file in request.files['opdracht_foto_<slot>'] (gecropt) of
    'opdracht_orig_<slot>' (origineel) wordt deze opgeslagen en de bestandsnaam in
    de JSON vervangen. Oude bestanden bij vervangen worden opgeruimd.
    """
    try:
        opdrachten = json.loads(json_string or '[]')
    except (ValueError, TypeError):
        return json_string or '[]'
    if not isinstance(opdrachten, list):
        return json_string or '[]'

    for opdr in opdrachten:
        if not isinstance(opdr, dict):
            continue
        fotos = opdr.get('fotos') or []
        nieuwe_fotos = []
        for foto in fotos[:3]:  # veiligheidsklem: max 3 foto's per opdracht
            if not isinstance(foto, dict):
                continue
            slot = (foto.get('slot') or '').strip()
            bestand = (foto.get('bestand') or '').strip()
            bestand_origineel = (foto.get('bestand_origineel') or '').strip()
            if slot:
                orig_upload = request.files.get('opdracht_orig_' + slot)
                if orig_upload and orig_upload.filename:
                    orig_naam, orig_fout = save_foto(orig_upload, prefix='opdracht_orig', ratio=None)
                    if orig_naam:
                        if bestand_origineel:
                            verwijder_bestand(bestand_origineel)
                        bestand_origineel = orig_naam
                    elif orig_fout:
                        flash(f'Opdracht-foto (origineel): {orig_fout}', 'warning')
                crop_upload = request.files.get('opdracht_foto_' + slot)
                if crop_upload and crop_upload.filename:
                    foto_naam, foto_fout = save_foto(crop_upload, prefix='opdracht', ratio=None)
                    if foto_naam:
                        if bestand:
                            verwijder_bestand(bestand)
                        bestand = foto_naam
                    elif foto_fout:
                        flash(f'Opdracht-foto: {foto_fout}', 'warning')
                if bestand and not bestand_origineel:
                    bestand_origineel = bestand
            nieuwe_fotos.append({
                'slot': slot,
                'bestand': bestand,
                'bestand_origineel': bestand_origineel,
            })
        opdr['fotos'] = nieuwe_fotos
    return json.dumps(opdrachten, ensure_ascii=False)


def verwerk_werkwijze_fotos(json_string):
    """Parse werkwijze-JSON (platte lijst van stappen), upload nieuwe foto's per slot-id,
    return bijgewerkte JSON.

    Per stap.fotos[i] = {'slot': '<uuid>', 'bestand': '<filename>'}.
    Bij een geüploade file in request.files['werkwijze_foto_<slot>'] wordt deze opgeslagen
    en de bestandsnaam vervangen in de JSON. Oude bestanden bij vervangen worden opgeruimd.
    """
    try:
        stappen = json.loads(json_string or '[]')
    except (ValueError, TypeError):
        return json_string or '[]'
    if not isinstance(stappen, list):
        return json_string or '[]'

    for stap in stappen:
        if not isinstance(stap, dict):
            continue
        fotos = stap.get('fotos') or []
        nieuwe_fotos = []
        for foto in fotos:
            if not isinstance(foto, dict):
                continue
            slot = (foto.get('slot') or '').strip()
            bestand = (foto.get('bestand') or '').strip()
            bestand_origineel = (foto.get('bestand_origineel') or '').strip()
            if slot:
                # Nieuwe ORIGINELE upload (via de "+ Foto toevoegen" knop).
                # De client stuurt hetzelfde bestand naar beide inputs bij een
                # nieuwe upload, en alleen naar 'werkwijze_foto_<slot>' bij re-crop.
                orig_upload = request.files.get('werkwijze_orig_' + slot)
                if orig_upload and orig_upload.filename:
                    orig_naam, orig_fout = save_foto(orig_upload, prefix='werkwijze_orig', ratio=None)
                    if orig_naam:
                        if bestand_origineel:
                            verwijder_bestand(bestand_origineel)
                        bestand_origineel = orig_naam
                    elif orig_fout:
                        flash(f'Werkwijze-foto (origineel): {orig_fout}', 'warning')
                # Nieuwe GECROPTE versie (nieuwe upload = zelfde als origineel,
                # of een blob uit de cropper bij re-crop).
                crop_upload = request.files.get('werkwijze_foto_' + slot)
                if crop_upload and crop_upload.filename:
                    foto_naam, foto_fout = save_foto(crop_upload, prefix='werkwijze', ratio=None)
                    if foto_naam:
                        if bestand:
                            verwijder_bestand(bestand)
                        bestand = foto_naam
                    elif foto_fout:
                        flash(f'Werkwijze-foto: {foto_fout}', 'warning')
                # Backfill voor oude data: als er wel een 'bestand' is maar geen
                # 'bestand_origineel' → beschouw 'bestand' als origineel (zodat
                # re-crop niet leidt tot een 404).
                if bestand and not bestand_origineel:
                    bestand_origineel = bestand
            nieuwe_fotos.append({
                'slot': slot,
                'bestand': bestand,
                'bestand_origineel': bestand_origineel,
            })
        stap['fotos'] = nieuwe_fotos
        # v0.7.31 rich-text: sanitize tekst_html van de vrije-tekst-modus.
        # Bullets-modus (tekst_opmaak='lijst') blijft plain-text.
        if stap.get('tekst_opmaak') != 'lijst' and stap.get('tekst_html'):
            stap['tekst_html'] = sanitize_kennis_html(stap.get('tekst_html'))
    return json.dumps(stappen, ensure_ascii=False)


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
    _vereis_aanmaken()
    if kaart_type not in KAART_TYPES:
        flash('Ongeldig kaarttype.', 'danger')
        return redirect(url_for('kaarten.kies_type'))

    form_class = FORMULIEREN[kaart_type]
    form = form_class()

    if form.validate_on_submit():
        # Header-foto is verplicht voor élke kaart.
        if not (form.header_foto.data and form.header_foto.data.filename):
            form.header_foto.errors = list(form.header_foto.errors) + ['Een header-foto is verplicht.']
            type_info = KAART_TYPES[kaart_type]
            return render_template('kaarten/formulier.html', form=form, kaart_type=kaart_type,
                                   type_info=type_info, bewerken=False, kaart=None,
                                   WERKWIJZE_MAX_STAPPEN=WERKWIJZE_MAX_STAPPEN,
                                   WERKWIJZE_TITEL_MAX=WERKWIJZE_TITEL_MAX,
                                   WERKWIJZE_TEKST_MAX=WERKWIJZE_TEKST_MAX,
                                   subcategorieen_per_kerntaak=_subcategorieen_per_kerntaak_ctx())

        inhoud = {}
        for veld in INHOUD_VELDEN[kaart_type]:
            inhoud[veld] = getattr(form, veld).data or ''
        for veld in INHOUD_LIJST_VELDEN.get(kaart_type, []):
            inhoud[veld] = getattr(form, veld).data or []

        # Subcategorie is verplicht om het XX-#### nummer te kunnen genereren.
        subcat_raw = (form.subcategorie_id.data or '').strip() if form.subcategorie_id.data else ''
        subcat = Subcategorie.query.get(int(subcat_raw)) if subcat_raw.isdigit() else None
        if subcat is None or subcat.kerntaak is None:
            form.subcategorie_id.errors = list(form.subcategorie_id.errors) + [
                'Kies een subcategorie — die bepaalt de laatste 2 cijfers van het kaartnummer.'
            ]
            type_info = KAART_TYPES[kaart_type]
            return render_template('kaarten/formulier.html', form=form, kaart_type=kaart_type,
                                   type_info=type_info, bewerken=False, kaart=None,
                                   WERKWIJZE_MAX_STAPPEN=WERKWIJZE_MAX_STAPPEN,
                                   WERKWIJZE_TITEL_MAX=WERKWIJZE_TITEL_MAX,
                                   WERKWIJZE_TEKST_MAX=WERKWIJZE_TEKST_MAX,
                                   subcategorieen_per_kerntaak=_subcategorieen_per_kerntaak_ctx())

        kaart = Kaart(
            type=kaart_type,
            nummer=Kaart.volgende_nummer(kaart_type, subcat.kerntaak.cijfer, subcat.cijfer),
            naam=_kaart_naam_uit_form(form, kaart_type),
            kerntaak=form.kerntaak.data or None,
            subcategorie_id=subcat.id,
            status='concept',
            auteur_id=current_user.id,
            bijgewerkt_door_id=current_user.id,
        )
        kaart.set_inhoud(inhoud)

        foto_naam, foto_fout = save_kaart_header_foto(form.header_foto.data, kaart_type)
        if foto_fout:
            form.header_foto.errors = list(form.header_foto.errors) + [foto_fout]
            type_info = KAART_TYPES[kaart_type]
            return render_template('kaarten/formulier.html', form=form, kaart_type=kaart_type,
                                   type_info=type_info, bewerken=False, kaart=None,
                                   WERKWIJZE_MAX_STAPPEN=WERKWIJZE_MAX_STAPPEN,
                                   WERKWIJZE_TITEL_MAX=WERKWIJZE_TITEL_MAX,
                                   WERKWIJZE_TEKST_MAX=WERKWIJZE_TEKST_MAX,
                                   subcategorieen_per_kerntaak=_subcategorieen_per_kerntaak_ctx())
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
                           type_info=type_info, bewerken=False, kaart=None,
                           WERKWIJZE_MAX_STAPPEN=WERKWIJZE_MAX_STAPPEN,
                           WERKWIJZE_TITEL_MAX=WERKWIJZE_TITEL_MAX,
                           WERKWIJZE_TEKST_MAX=WERKWIJZE_TEKST_MAX,
                           subcategorieen_per_kerntaak=_subcategorieen_per_kerntaak_ctx())


@bp.route('/<int:kaart_id>/bewerken', methods=['GET', 'POST'])
@login_required
def bewerken(kaart_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    _vereis_bewerken(kaart)
    if kaart.type not in FORMULIEREN:
        abort(404)
    form_class = FORMULIEREN[kaart.type]

    if request.method == 'POST':
        form = form_class()
    else:
        # Prefill met huidige inhoud
        huidige = kaart.get_inhoud()
        data = {'naam': kaart.naam, 'kerntaak': kaart.kerntaak or '',
                'subcategorie_id': str(kaart.subcategorie_id) if kaart.subcategorie_id else ''}
        for veld in INHOUD_VELDEN[kaart.type]:
            data[veld] = huidige.get(veld, '')
        for veld in INHOUD_LIJST_VELDEN.get(kaart.type, []):
            data[veld] = huidige.get(veld, []) or []
        form = form_class(data=data)

    wijziging_toelichting = (request.form.get('wijziging_toelichting') or '').strip()
    auto_save_tab = (request.form.get('auto_save_tab') or '').strip()
    is_auto_save = bool(auto_save_tab)
    toelichting_fout = None

    # Werkwijze-foto's ALTIJD verwerken op POST — ook als de submit later door een
    # validatiefout wordt afgewezen. Anders gaan geüploade foto's stilletjes verloren
    # bij re-render van het formulier (browser stuurt file-inputs niet opnieuw mee).
    processed_werkwijze_json = None
    if request.method == 'POST' and kaart.type == 'instructie':
        _incoming_ww = request.form.get('werkwijze_stappen_json') or '[]'
        processed_werkwijze_json = verwerk_werkwijze_fotos(_incoming_ww)
        if processed_werkwijze_json != _incoming_ww:
            _inh = kaart.get_inhoud()
            _inh['werkwijze_stappen_json'] = processed_werkwijze_json
            kaart.set_inhoud(_inh)
            db.session.commit()
            # Update WTForms data zodat een eventuele re-render de nieuwe JSON toont.
            if hasattr(form, 'werkwijze_stappen_json'):
                form.werkwijze_stappen_json.data = processed_werkwijze_json

    # Kenniskaart-hoofdstukken (v0.7.13): één JSON met max 5 hoofdstukken. Per
    # hoofdstuk: titel (plain), tekst_html (rich, gesanitized met bleach) én
    # foto's (dezelfde file-input-conventie werkwijze_foto_<slot>).
    processed_kennis_hoofdstukken_json = None
    if request.method == 'POST' and kaart.type == 'kennis':
        _incoming_h = request.form.get('kernboodschap_hoofdstukken_json') or '[]'
        processed_kennis_hoofdstukken_json = verwerk_kennis_hoofdstukken(_incoming_h)
        if processed_kennis_hoofdstukken_json != _incoming_h:
            _inh = kaart.get_inhoud()
            _inh['kernboodschap_hoofdstukken_json'] = processed_kennis_hoofdstukken_json
            kaart.set_inhoud(_inh)
            db.session.commit()
            if hasattr(form, 'kernboodschap_hoofdstukken_json'):
                form.kernboodschap_hoofdstukken_json.data = processed_kennis_hoofdstukken_json

    # Opdracht-foto's: idem — bij POST altijd verwerken zodat uploads niet verloren gaan.
    processed_opdracht_json = None
    if request.method == 'POST' and kaart.type == 'opdracht':
        _incoming_opdr = request.form.get('opdrachten_json') or '[]'
        processed_opdracht_json = verwerk_opdracht_fotos(_incoming_opdr)
        if processed_opdracht_json != _incoming_opdr:
            _inh = kaart.get_inhoud()
            _inh['opdrachten_json'] = processed_opdracht_json
            kaart.set_inhoud(_inh)
            db.session.commit()
            if hasattr(form, 'opdrachten_json'):
                form.opdrachten_json.data = processed_opdracht_json

    # Ensceneringstips-foto's (scenariokaart): idem — verwerk uploads per tip-slot.
    processed_tips_json = None
    if request.method == 'POST' and kaart.type == 'scenario':
        _incoming_tips = request.form.get('ensceneringstips') or '[]'
        processed_tips_json = verwerk_tips_fotos(_incoming_tips)
        if processed_tips_json != _incoming_tips:
            _inh = kaart.get_inhoud()
            _inh['ensceneringstips'] = processed_tips_json
            kaart.set_inhoud(_inh)
            db.session.commit()
            if hasattr(form, 'ensceneringstips'):
                form.ensceneringstips.data = processed_tips_json
    # Toelichting-wijziging is alleen vereist zodra de kaart ooit gepubliceerd is.
    # Concept-fase = bouwen; publicatie = officieel; verandering ná publicatie =
    # echte "wijziging" die een reden verdient. Zolang je nog in concept-fase zit
    # (ook al klopt de kaart al 3× opgeslagen) hoef je geen reden op te geven.
    kaart_heeft_wijzigingen = (
        kaart.status == 'gepubliceerd'
        or KaartWijziging.query.filter_by(kaart_id=kaart.id, actie='Gepubliceerd').count() > 0
    )

    # === AUTO-SAVE BIJ TAB-WISSEL ===
    # Bypass de strikte WTForms-validatie (Length/DataRequired): concept mag incompleet
    # of over-lang zijn. Zo gaat er nooit data verloren tijdens tab-wissel.
    if request.method == 'POST' and is_auto_save:
        inhoud = {}
        for veld in INHOUD_VELDEN[kaart.type]:
            inhoud[veld] = (request.form.get(veld) or '').strip()
        for veld in INHOUD_LIJST_VELDEN.get(kaart.type, []):
            inhoud[veld] = request.form.getlist(veld)
        # Werkwijze-foto's zijn al bovenaan verwerkt — gebruik het resultaat.
        if processed_werkwijze_json is not None and 'werkwijze_stappen_json' in inhoud:
            inhoud['werkwijze_stappen_json'] = processed_werkwijze_json
        if processed_opdracht_json is not None and 'opdrachten_json' in inhoud:
            inhoud['opdrachten_json'] = processed_opdracht_json
        if processed_tips_json is not None and 'ensceneringstips' in inhoud:
            inhoud['ensceneringstips'] = processed_tips_json
        if processed_kennis_hoofdstukken_json is not None and 'kernboodschap_hoofdstukken_json' in inhoud:
            inhoud['kernboodschap_hoofdstukken_json'] = processed_kennis_hoofdstukken_json

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
            # ratio=None: crop-aspect is client-side gekozen; server valideert alleen minimum-formaat.
            foto_naam, foto_fout = save_foto(tips_foto, prefix='tips', ratio=None)
            if foto_fout:
                flash(f'Tips-foto: {foto_fout}', 'warning')
            elif foto_naam:
                if kaart.ensceneringstips_foto:
                    verwijder_bestand(kaart.ensceneringstips_foto)
                kaart.ensceneringstips_foto = foto_naam

        # Tweede ensceneringstips-foto (optioneel — max 2 per kaart).
        if request.form.get('verwijder_ensceneringstips_foto_2') and kaart.ensceneringstips_foto_2:
            verwijder_bestand(kaart.ensceneringstips_foto_2)
            kaart.ensceneringstips_foto_2 = None

        tips_foto_2 = request.files.get('ensceneringstips_foto_2')
        if tips_foto_2 and tips_foto_2.filename:
            foto_naam, foto_fout = save_foto(tips_foto_2, prefix='tips', ratio=None)
            if foto_fout:
                flash(f'Tweede tips-foto: {foto_fout}', 'warning')
            elif foto_naam:
                if kaart.ensceneringstips_foto_2:
                    verwijder_bestand(kaart.ensceneringstips_foto_2)
                kaart.ensceneringstips_foto_2 = foto_naam

        # Productfoto (instructiekaart-materiaal): geen vaste ratio, vrij formaat.
        if request.form.get('verwijder_productfoto') and kaart.productfoto:
            verwijder_bestand(kaart.productfoto)
            kaart.productfoto = None

        prod_foto = request.files.get('productfoto')
        if prod_foto and prod_foto.filename:
            foto_naam, foto_fout = save_foto(prod_foto, prefix='product', ratio=None)
            if foto_fout:
                flash(f'Productfoto: {foto_fout}', 'warning')
            elif foto_naam:
                if kaart.productfoto:
                    verwijder_bestand(kaart.productfoto)
                kaart.productfoto = foto_naam

        log_wijziging(kaart, 'Auto-save (tab-wissel)', '')
        db.session.commit()
        flash(f'Concept opgeslagen ({kaart.nummer}).', 'info')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + f'#tab-{auto_save_tab}')

    # === NORMALE OPSLAAN (submit-knop) ===
    if (request.method == 'POST' and not is_auto_save
            and kaart_heeft_wijzigingen and not wijziging_toelichting):
        toelichting_fout = 'Vul een korte toelichting in waarom je deze kaart wijzigt.'

    # WTForms valideren — moet vóór de foto-check omdat validate_on_submit() de errors-dict reset.
    is_valid = form.validate_on_submit()

    # Header-foto verplicht: er moet ofwel een bestaande foto blijven, ofwel een nieuwe upload zijn.
    foto_fout_label = None
    if request.method == 'POST' and not is_auto_save:
        wil_verwijderen = bool(request.form.get('verwijder_header_foto'))
        heeft_nieuwe = form.header_foto.data and form.header_foto.data.filename
        blijft_bestaande = bool(kaart.header_foto) and not wil_verwijderen
        if not heeft_nieuwe and not blijft_bestaande:
            foto_fout_label = 'Een header-foto is verplicht.'
            form.header_foto.errors = list(form.header_foto.errors) + [foto_fout_label]

    # Instructiekaart: werkwijze mag niet meer dan WERKWIJZE_MAX_STAPPEN stappen bevatten.
    werkwijze_fout = None
    if request.method == 'POST' and not is_auto_save and kaart.type == 'instructie':
        try:
            werkwijze_data = json.loads(request.form.get('werkwijze_stappen_json') or '[]')
        except (ValueError, TypeError):
            werkwijze_data = []
        if isinstance(werkwijze_data, list):
            totaal_stappen = len([s for s in werkwijze_data if isinstance(s, dict)])
            if totaal_stappen > WERKWIJZE_MAX_STAPPEN:
                werkwijze_fout = f'Maximaal {WERKWIJZE_MAX_STAPPEN} stappen toegestaan ({totaal_stappen} geteld).'
                form.werkwijze_stappen_json.errors = list(form.werkwijze_stappen_json.errors) + [werkwijze_fout]


    # Instructiekaart-materiaal: elke marker op de productfoto moet een beschrijving hebben.
    markers_fout = None
    if (request.method == 'POST' and not is_auto_save and kaart.type == 'instructie'
            and (request.form.get('instructie_type') or '') == 'materiaal'):
        try:
            markers_data = json.loads(request.form.get('productfoto_markers_json') or '[]')
        except (ValueError, TypeError):
            markers_data = []
        if isinstance(markers_data, list):
            lege_nrs = [str(i + 1) for i, m in enumerate(markers_data)
                        if not (isinstance(m, dict) and (m.get('label') or '').strip())]
            if lege_nrs:
                markers_fout = 'Marker ' + ', '.join(lege_nrs) + ' heeft nog geen beschrijving.'
                form.productfoto_markers_json.errors = list(form.productfoto_markers_json.errors) + [markers_fout]

    # Verplichte inhoud per kaart-type (bovenop WTForms) — alleen bij expliciete opslag.
    ontbrekende_velden = []
    if request.method == 'POST' and not is_auto_save:
        ontbrekende_velden = _controleer_verplichte_velden(kaart.type, form, request.form, kaart=kaart)

    if is_valid and not toelichting_fout and not foto_fout_label and not markers_fout and not werkwijze_fout and not ontbrekende_velden:
        inhoud = {}
        for veld in INHOUD_VELDEN[kaart.type]:
            inhoud[veld] = getattr(form, veld).data or ''
        for veld in INHOUD_LIJST_VELDEN.get(kaart.type, []):
            inhoud[veld] = getattr(form, veld).data or []
        # Werkwijze-foto's zijn al bovenaan verwerkt — gebruik het resultaat.
        if processed_werkwijze_json is not None and 'werkwijze_stappen_json' in inhoud:
            inhoud['werkwijze_stappen_json'] = processed_werkwijze_json
        if processed_opdracht_json is not None and 'opdrachten_json' in inhoud:
            inhoud['opdrachten_json'] = processed_opdracht_json
        if processed_tips_json is not None and 'ensceneringstips' in inhoud:
            inhoud['ensceneringstips'] = processed_tips_json
        if processed_kennis_hoofdstukken_json is not None and 'kernboodschap_hoofdstukken_json' in inhoud:
            inhoud['kernboodschap_hoofdstukken_json'] = processed_kennis_hoofdstukken_json

        kaart.naam = _kaart_naam_uit_form(form, kaart.type)
        _subcat_raw = (form.subcategorie_id.data or '').strip() if form.subcategorie_id.data else ''
        kaart.subcategorie_id = int(_subcat_raw) if _subcat_raw.isdigit() else None
        # Subcategorie is autoritair voor de kerntaak-sleutel — zo raken de twee
        # nooit uit sync. Alleen als er geen sub is (oude kaart, edge case) valt
        # kerntaak terug op wat het formulier meestuurt.
        if kaart.subcategorie and kaart.subcategorie.kerntaak:
            kaart.kerntaak = kaart.subcategorie.kerntaak.sleutel
        else:
            kaart.kerntaak = form.kerntaak.data or None
        kaart.set_inhoud(inhoud)
        kaart.bijgewerkt_door_id = current_user.id
        # Als kerntaak/subcategorie zijn gewijzigd → nieuw nummer volgens VGGM-schema.
        oud_nummer, nieuw_nummer = _hernummer_als_subcategorie_wijzigt(kaart)
        if oud_nummer and nieuw_nummer:
            log_wijziging(kaart, 'Hernummerd', f'{oud_nummer} → {nieuw_nummer}')
            flash(f'Kaartnummer aangepast aan de nieuwe kerntaak/subcategorie: {oud_nummer} → {nieuw_nummer}.', 'info')

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

        # Ensceneringstips foto (1)
        if request.form.get('verwijder_ensceneringstips_foto'):
            if kaart.ensceneringstips_foto:
                verwijder_bestand(kaart.ensceneringstips_foto)
                kaart.ensceneringstips_foto = None

        tips_foto = request.files.get('ensceneringstips_foto')
        if tips_foto and tips_foto.filename:
            foto_naam, foto_fout = save_foto(tips_foto, prefix='tips', ratio=None)
            if foto_fout:
                flash(f'Tips-foto: {foto_fout}', 'warning')
            else:
                if kaart.ensceneringstips_foto:
                    verwijder_bestand(kaart.ensceneringstips_foto)
                kaart.ensceneringstips_foto = foto_naam

        # Ensceneringstips foto (2)
        if request.form.get('verwijder_ensceneringstips_foto_2'):
            if kaart.ensceneringstips_foto_2:
                verwijder_bestand(kaart.ensceneringstips_foto_2)
                kaart.ensceneringstips_foto_2 = None

        tips_foto_2 = request.files.get('ensceneringstips_foto_2')
        if tips_foto_2 and tips_foto_2.filename:
            foto_naam, foto_fout = save_foto(tips_foto_2, prefix='tips', ratio=None)
            if foto_fout:
                flash(f'Tweede tips-foto: {foto_fout}', 'warning')
            else:
                if kaart.ensceneringstips_foto_2:
                    verwijder_bestand(kaart.ensceneringstips_foto_2)
                kaart.ensceneringstips_foto_2 = foto_naam

        # Productfoto (instructiekaart-materiaal): geen vaste ratio, vrij formaat.
        if request.form.get('verwijder_productfoto'):
            if kaart.productfoto:
                verwijder_bestand(kaart.productfoto)
                kaart.productfoto = None

        prod_foto = request.files.get('productfoto')
        if prod_foto and prod_foto.filename:
            foto_naam, foto_fout = save_foto(prod_foto, prefix='product', ratio=None)
            if foto_fout:
                flash(f'Productfoto: {foto_fout}', 'warning')
            else:
                if kaart.productfoto:
                    verwijder_bestand(kaart.productfoto)
                kaart.productfoto = foto_naam

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

    # Gekoppelde kaarten + beschikbare kaarten voor koppel-dropdown (scenario- én opdrachtkaart)
    gekoppelde_kaarten = []
    beschikbaar_per_type = {}
    if kaart.type in ('scenario', 'opdracht'):
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

    # Instructie-, opdracht- én kenniskaart-achtergrond: QR-koppelingen + beschikbare QR-codes.
    # De InstructieQRLink-tabel is generiek gestructureerd (kaart_id FK), dus dekt alle 3 types.
    instructie_qr_links = []
    instructie_beschikbare_qrs_per_categorie = {}
    if kaart.type in ('instructie', 'opdracht', 'kennis'):
        instructie_qr_links = kaart.get_instructie_qr_links()
        gekoppelde_qr_ids = {r.qr_code_id for r in instructie_qr_links}
        beschikbare_qrs = QRCode.query.filter(
            QRCode.actief == True,  # noqa: E712
            ~QRCode.id.in_(gekoppelde_qr_ids),
        ).order_by(QRCode.naam).all()
        for cat_key, cat_naam in QR_CATEGORIEEN.items():
            qrs = [q for q in beschikbare_qrs if q.categorie == cat_key]
            if qrs:
                instructie_beschikbare_qrs_per_categorie[cat_key] = {
                    'naam': cat_naam,
                    'qrs': qrs,
                }

    # Themakaart: koppelingen aan andere kaarten (per tussentitel) + QR-codes (één lijst)
    thema_kaart_links = {0: [], 1: [], 2: []}
    thema_qr_links_op_volgorde = []
    thema_beschikbare_kaarten_per_type = {}  # {'instructie': [...], 'scenario': [...], 'opdracht': [...], 'kennis': [...]}
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
        # Groepeer per type, in vaste volgorde (instructie / scenario / opdracht / kennis)
        for type_key in ('instructie', 'scenario', 'opdracht', 'kennis'):
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
                           kaart_heeft_wijzigingen=kaart_heeft_wijzigingen,
                           gekoppelde_kaarten=gekoppelde_kaarten,
                           beschikbaar_per_type=beschikbaar_per_type,
                           thema_kaart_links=thema_kaart_links,
                           thema_qr_links_op_volgorde=thema_qr_links_op_volgorde,
                           thema_beschikbare_kaarten_per_type=thema_beschikbare_kaarten_per_type,
                           thema_beschikbare_qrs_per_categorie=thema_beschikbare_qrs_per_categorie,
                           THEMA_MAX_KAARTEN_TOTAAL=THEMA_MAX_KAARTEN_TOTAAL,
                           THEMA_MAX_QR_TOP=THEMA_MAX_QR_TOP,
                           THEMA_MAX_QR_BOTTOM=THEMA_MAX_QR_BOTTOM,
                           THEMA_QR_LABEL_MAX=THEMA_QR_LABEL_MAX,
                           WERKWIJZE_MAX_STAPPEN=WERKWIJZE_MAX_STAPPEN,
                           WERKWIJZE_TITEL_MAX=WERKWIJZE_TITEL_MAX,
                           WERKWIJZE_TEKST_MAX=WERKWIJZE_TEKST_MAX,
                           instructie_qr_links=instructie_qr_links,
                           instructie_beschikbare_qrs_per_categorie=instructie_beschikbare_qrs_per_categorie,
                           INSTRUCTIE_MAX_KAART_KOPPELINGEN=INSTRUCTIE_MAX_KAART_KOPPELINGEN,
                           INSTRUCTIE_MAX_QR_KOPPELINGEN=INSTRUCTIE_MAX_QR_KOPPELINGEN,
                           subcategorieen_per_kerntaak=_subcategorieen_per_kerntaak_ctx())


@bp.route('/<int:kaart_id>/koppel-actie', methods=['POST'])
@login_required
def koppel_actie(kaart_id):
    """Klein endpoint om koppelingen te beheren vanuit de verwijzingen-tab."""
    kaart = Kaart.query.get_or_404(kaart_id)
    _vereis_bewerken(kaart)
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
    _vereis_bewerken(kaart)
    if kaart.type != 'thema':
        abort(404)
    doel_id = request.form.get('doel_id', type=int)
    groep_index = request.form.get('groep_index', type=int)
    if groep_index is None or groep_index not in (0, 1, 2, 3) or not doel_id:
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
    _vereis_bewerken(kaart)
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
    _vereis_bewerken(kaart)
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
    _vereis_bewerken(kaart)
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
    _vereis_bewerken(kaart)
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
    _vereis_bewerken(kaart)
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


# ====================== INSTRUCTIEKAART QR-KOPPELINGEN ======================

@bp.route('/<int:kaart_id>/instructie/qr-link/toevoegen', methods=['POST'])
@login_required
def instructie_qr_link_toevoegen(kaart_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    _vereis_bewerken(kaart)
    if kaart.type not in ('instructie', 'opdracht', 'kennis'):
        abort(404)
    qr_id = request.form.get('qr_id', type=int)
    if not qr_id:
        flash('Geen QR-code geselecteerd.', 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + '#tab-achtergrond')
    qr = QRCode.query.get(qr_id)
    if not qr:
        flash('QR-code niet gevonden.', 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + '#tab-achtergrond')
    if InstructieQRLink.query.filter_by(kaart_id=kaart.id).count() >= INSTRUCTIE_MAX_QR_KOPPELINGEN:
        flash(f'Maximaal {INSTRUCTIE_MAX_QR_KOPPELINGEN} QR-koppelingen per kaart.', 'warning')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + '#tab-achtergrond')
    if InstructieQRLink.query.filter_by(kaart_id=kaart.id, qr_code_id=qr_id).first():
        flash('Deze QR-code is al gekoppeld.', 'info')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + '#tab-achtergrond')
    laatste = InstructieQRLink.query.filter_by(kaart_id=kaart.id).order_by(InstructieQRLink.volgorde.desc()).first()
    volgorde = (laatste.volgorde + 1) if laatste else 0
    db.session.add(InstructieQRLink(kaart_id=kaart.id, qr_code_id=qr_id, volgorde=volgorde))
    log_wijziging(kaart, 'QR-koppeling toegevoegd', qr.naam)
    db.session.commit()
    flash(f'QR-code "{qr.naam}" toegevoegd.', 'success')
    return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + '#tab-achtergrond')


@bp.route('/<int:kaart_id>/instructie/qr-link/<int:link_id>/verwijderen', methods=['POST'])
@login_required
def instructie_qr_link_verwijderen(kaart_id, link_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    _vereis_bewerken(kaart)
    if kaart.type not in ('instructie', 'opdracht', 'kennis'):
        abort(404)
    link = InstructieQRLink.query.filter_by(id=link_id, kaart_id=kaart.id).first()
    if not link:
        abort(404)
    qr_naam = link.qr_code.naam if link.qr_code else 'onbekend'
    db.session.delete(link)
    log_wijziging(kaart, 'QR-koppeling verwijderd', qr_naam)
    db.session.commit()
    flash('QR-koppeling verwijderd.', 'info')
    return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + '#tab-achtergrond')


@bp.route('/<int:kaart_id>/thema/qr-link/<int:link_id>/label', methods=['POST'])
@login_required
def thema_qr_link_label(kaart_id, link_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    _vereis_bewerken(kaart)
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
    _vereis_publiceren()
    _vereis_bewerken(kaart)
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
    _vereis_publiceren()
    _vereis_bewerken(kaart)
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
    _vereis_bewerken(kaart)
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
    _vereis_bewerken(kaart)
    kaart.status = 'concept'
    kaart.bijgewerkt_door_id = current_user.id
    log_wijziging(kaart, 'Uit archief gehaald')
    db.session.commit()
    flash(f'Kaart "{kaart.naam}" is uit het archief gehaald en staat nu als concept.', 'success')
    return redirect(url_for('kaarten.overzicht'))


@bp.route('/<int:kaart_id>/kopieren', methods=['POST'])
@login_required
def kopieren(kaart_id):
    _vereis_aanmaken()
    origineel = Kaart.query.get_or_404(kaart_id)
    # Voor het XX-#### nummer hebben we de subcategorie van het origineel nodig.
    # Als die er niet is (kaarten van vóór de subcategorie-refactor) valt de kopie
    # terug op 0/0 — een tijdelijk startnummer dat de gebruiker later kan verzetten.
    if origineel.subcategorie and origineel.subcategorie.kerntaak:
        kt_cijfer = origineel.subcategorie.kerntaak.cijfer
        sub_cijfer = origineel.subcategorie.cijfer
    else:
        kt_cijfer, sub_cijfer = 0, 0
    nieuwe = Kaart(
        type=origineel.type,
        nummer=Kaart.volgende_nummer(origineel.type, kt_cijfer, sub_cijfer),
        naam=f'{origineel.naam} (kopie)',
        kerntaak=origineel.kerntaak,
        subcategorie_id=origineel.subcategorie_id,
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
