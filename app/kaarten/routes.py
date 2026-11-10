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


# === Rich-text sanitizer ===
# Whitelist van tags/attributen die de editor mag produceren. Alles daarbuiten
# wordt door bleach.clean() gestript — beschermt tegen XSS bij weergave in de app
# en tegen ongewenste opmaak in de PDF (fonts, inline scripts, etc.). Wordt
# gebruikt voor werkwijze-stappen (instructiekaart) en ensceneringstips
# (scenariokaart).
RICH_TEXT_TOEGESTANE_TAGS = [
    'p', 'br', 'strong', 'b', 'em', 'i', 'u',
    'h3', 'h4',
    'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'span',
]
# Enige attribuut dat we toelaten is `style="color: <hex>"` op <span>. Kleuren
# worden verder gefilterd tot de 3 huisstijl-kleuren (navy, brandweer-rood, zwart).
RICH_TEXT_HUISSTIJL_KLEUREN = {'#1B2A4A', '#C8102E', '#000000', '#1b2a4a', '#c8102e'}
_KLEUR_STYLE_RE = re.compile(r'color\s*:\s*(#[0-9a-fA-F]{6})', re.IGNORECASE)


def _rich_text_style_filter(tag, name, value):
    """bleach attribute-filter: sta alleen 'style' toe op <span>, alleen met
    color-waarde uit de huisstijl. Al het andere wordt geweigerd."""
    if name != 'style' or tag != 'span':
        return False
    match = _KLEUR_STYLE_RE.search(value or '')
    if not match:
        return False
    kleur = match.group(1)
    return kleur in RICH_TEXT_HUISSTIJL_KLEUREN


def sanitize_rich_html(ruw_html):
    """Sanitize + normaliseer rich-text input. Gebruikt voor werkwijze-stappen
    (v0.7.31) en ensceneringstips (v0.7.32). Leverstijl: één HTML-string die
    veilig in Jinja én in WeasyPrint gerenderd kan worden."""
    if not ruw_html:
        return ''
    schoon = bleach.clean(
        ruw_html,
        tags=RICH_TEXT_TOEGESTANE_TAGS,
        attributes={'span': _rich_text_style_filter},
        strip=True,
    )
    # Reduceer whitespace-blokken en verwijder lege paragrafen die contenteditable
    # vaak achterlaat na een leegmaken-actie.
    schoon = re.sub(r'<p>\s*(?:<br\s*/?>)?\s*</p>', '', schoon)
    return schoon.strip()


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
    # Placeholder-concepten hebben nog geen echt nummer — subcategorie-wissel
    # heeft dan geen effect (nummer wordt pas bij indienen toegekend).
    if not kaart.heeft_definitief_nummer:
        return None, None
    letters = KAART_TYPES[kaart.type]['prefix']
    verwachte_start = f'{letters}-{sub.kerntaak.cijfer}{sub.cijfer}'
    if kaart.nummer and kaart.nummer.startswith(verwachte_start):
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
                                VEILIGHEID_MAX_ZINNEN, VEILIGHEID_ZIN_MAX)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
HEADER_FOTO_MAX_BYTES = 20 * 1024 * 1024
HEADER_FOTO_MIN_W = 900
HEADER_FOTO_MIN_H = 300
HEADER_FOTO_RATIO = 3.0


# --- Autorisatie -------------------------------------------------------------
# Rollen (oplopend): medewerker < coordinator < specialist < admin.
# Voorlopige rechten (fase 2) — volledige workflow-rechten komen in fase 3+:
# - admin / specialist mogen alles bewerken en publiceren
# - oefencoordinator / medewerker_oo mogen aanmaken + eigen kaarten bewerken
#   (medewerker_oo wordt in fase 3 beperkt tot instructiekaart materieel)
BEWERK_ROLLEN = {'admin', 'specialist'}
AANMAAK_ROLLEN = {'admin', 'specialist', 'oefencoordinator',
                  'oefencoordinator_roi', 'medewerker_oo'}
PUBLICATIE_ROLLEN = {'admin', 'specialist'}


def kan_kaart_bewerken(user, kaart):
    """Mag deze gebruiker deze kaart bewerken?

    Strikte regel (afspraak met Rebecca, nov 2026): **alleen de maker** mag
    bewerken. Er is dus geen 'collega uit kerngroep'-bypass meer. Bij ziekte/
    afwezigheid draagt een specialist of admin de kaart expliciet over naar
    een andere maker via `/eigenaar-wijzigen` — daarna is die nieuwe maker
    de enige die mag bewerken. Admin blijft als vangnet alles kunnen.
    """
    if not (user and user.is_authenticated):
        return False
    if user.is_admin:
        return True
    return kaart.auteur_id == user.id


def kaart_is_in_review(kaart):
    """True als de kaart momenteel bij een reviewer ligt (peer of specialist)."""
    return kaart.status in ('peer_review', 'specialist_review')


def kan_kaart_aanmaken(user):
    return bool(user and user.is_authenticated and user.rol in AANMAAK_ROLLEN)


def kan_publiceren(user):
    return bool(user and user.is_authenticated and user.rol in PUBLICATIE_ROLLEN)


def _vereis_bewerken(kaart):
    """Roep bovenaan elke write-route aan; stopt de request als de gebruiker niet mag.

    Extra regel (v0.7.40): zolang de kaart bij een reviewer ligt (peer_review of
    specialist_review) mag ook de maker zelf niet schrijven. Hij moet eerst de
    kaart terugtrekken (POST /<id>/terugtrekken), dan is-ie weer concept en start
    de workflow opnieuw bij indienen.

    Extra regel (v0.7.44): gearchiveerde kaarten zijn alleen-lezen voor iedereen
    behalve admin (die kan 'm uit archief halen via /dearchiveren).
    """
    if not kan_kaart_bewerken(current_user, kaart):
        abort(403)
    if kaart_is_in_review(kaart) and not current_user.is_admin:
        abort(403)
    if kaart.status == 'archief' and not current_user.is_admin:
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

    def _praktisch_missende_aantallen(veld_naam, vereist_types, label_map):
        """Return een lijst leesbare labels van items waar type een aantal vereist
        maar het aantal-veld leeg is. Lege lijst = alles OK."""
        try:
            data = json.loads(request_form.get(veld_naam) or '[]')
        except (ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        missers = []
        for it in data:
            if not isinstance(it, dict):
                continue
            t = (it.get('type') or '').strip()
            if t not in vereist_types:
                continue
            aantal = (it.get('aantal') or '').strip()
            if aantal:
                continue
            if t == 'anders':
                tekst = (it.get('tekst') or '').strip()
                missers.append(tekst or 'Andere')
            else:
                missers.append(label_map.get(t, t))
        return missers

    # Types die een aantal-veld tonen in de UI → aantal verplicht als dit type gekozen is.
    _BASIS_VEREIST_AANTAL = {'anders'}
    _EXTRA_VEREIST_AANTAL = {'extra_basisoefenset', 'extra_gespreksgroep',
                              'cirrus', 'zware_oefenpop', 'ademlucht', 'anders'}
    _BASIS_LABELS = {
        'ts': 'TS', 'basisoefenset': 'Basisoefenset',
        'lokaal_beamer': 'Lokaal met beamer en scherm',
        'oefenterrein': 'Oefenterrein',
        'externe_locatie': 'Externe locatie',
        'flipover': 'Flipover of whiteboard', 'laptop': 'Laptop',
    }
    _EXTRA_LABELS = {
        'extra_ts': 'Extra TS',
        'extra_basisoefenset': 'Extra basisoefenset',
        'extra_gespreksgroep': 'Extra gespreksgroep',
        'cirrus': 'Cirrus', 'moi': 'MOI-oefenset',
        'zware_oefenpop': 'Zware oefenpop',
        'autoambulance': 'Autoambulance', 'ademlucht': 'Ademlucht',
    }

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
        # v0.7.35: elke sectie die bestaat (tussentitel ingevuld of koppelingen heeft)
        # moet BEIDE hebben: tussentitel + ≥1 gekoppelde kaart. Strict per-sectie-regel.
        koppelingen_per_sectie = {0: 0, 1: 0, 2: 0, 3: 0}
        if kaart is not None:
            for link in ThemaKaartLink.query.filter_by(kaart_id=kaart.id).all():
                if link.groep_index in koppelingen_per_sectie:
                    koppelingen_per_sectie[link.groep_index] += 1

        secties_met_inhoud = []
        for i in (1, 2, 3, 4):
            titel = (request_form.get(f'tussentitel_{i}') or '').strip()
            koppel_n = koppelingen_per_sectie.get(i - 1, 0)
            if titel or koppel_n > 0:
                secties_met_inhoud.append((i, titel, koppel_n))

        if not secties_met_inhoud:
            _add_error('tussentitel_1',
                        'Vul minstens 1 sectie met tussentitel én minstens 1 gekoppelde kaart.')
            ontbrekend.append('Sectie')
        else:
            for i, titel, koppel_n in secties_met_inhoud:
                if not titel:
                    _add_error(f'tussentitel_{i}', f'Vul tussentitel {i} in.')
                    ontbrekend.append(f'Tussentitel sectie {i}')
                if koppel_n < 1:
                    _add_error(f'tussentitel_{i}',
                                f'Koppel minstens 1 kaart aan sectie {i} (of verwijder de tussentitel).')
                    ontbrekend.append(f'Koppeling sectie {i}')

    elif kaart_type == 'instructie':
        if _tekst_leeg('omschrijving'):
            _add_error('omschrijving', 'Vul een omschrijving in.')
            ontbrekend.append('Omschrijving')
        try:
            werkwijze = json.loads(request_form.get('werkwijze_stappen_json') or '[]')
        except (ValueError, TypeError):
            werkwijze = []
        # Werkwijze-editor houdt per stap pre-emptief 3 lege foto-slots aan — die tellen
        # NIET als 'inhoud'. Alleen echte content (titel, tekst, foto met bestand) telt.
        def _stap_heeft_inhoud(s):
            if not isinstance(s, dict):
                return False
            if (s.get('titel') or '').strip():
                return True
            if (s.get('tekst') or '').strip():
                return True
            for f in (s.get('fotos') or []):
                if isinstance(f, dict) and (f.get('bestand') or '').strip():
                    return True
            return False

        valide_stappen = [s for s in werkwijze if isinstance(s, dict)]
        gevuld = [s for s in valide_stappen if _stap_heeft_inhoud(s)]
        if len(gevuld) < 1:
            _add_error('werkwijze_stappen_json', 'Voeg minstens 1 werkwijze-stap toe.')
            ontbrekend.append('Werkwijze-stap')
        else:
            # v0.7.35/37: ELKE stap die in de lijst staat (ook als-ie "leeg lijkt" door
            # alleen voorgevulde slots) moet een titel hebben. Zo kan de gebruiker geen
            # lege stap-blokken laten slingeren.
            zonder_titel = [idx + 1 for idx, s in enumerate(valide_stappen)
                            if not (s.get('titel') or '').strip()]
            if zonder_titel:
                nrs = ', '.join(f'stap {n}' for n in zonder_titel)
                _add_error('werkwijze_stappen_json',
                            f'Elke werkwijze-stap heeft een titel nodig (ontbreekt bij: {nrs}). '
                            f'Verwijder lege stappen of vul een titel in.')
                ontbrekend.append('Werkwijze-stap-titel')

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
            _add_error('oefenmiddelen_basis', 'Voeg minstens 1 item toe bij "Wat regel jij".')
            ontbrekend.append('Praktisch — Wat regel jij')
        _basis_missers = _praktisch_missende_aantallen(
            'oefenmiddelen_basis', _BASIS_VEREIST_AANTAL, _BASIS_LABELS)
        if _basis_missers:
            _add_error('oefenmiddelen_basis',
                       'Vul een aantal in bij: ' + ', '.join(_basis_missers) + '.')
            ontbrekend.append('Praktisch — aantal "Wat regel jij"')
        _extra_missers = _praktisch_missende_aantallen(
            'oefenmiddelen_extra', _EXTRA_VEREIST_AANTAL, _EXTRA_LABELS)
        if _extra_missers:
            _add_error('oefenmiddelen_extra',
                       'Vul een aantal in bij: ' + ', '.join(_extra_missers) + '.')
            ontbrekend.append('Praktisch — aantal "Wat regelen wij"')
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
            _add_error('oefenmiddelen_basis', 'Voeg minstens 1 item toe bij "Wat regel jij".')
            ontbrekend.append('Praktisch — Wat regel jij')
        _basis_missers_op = _praktisch_missende_aantallen(
            'oefenmiddelen_basis', _BASIS_VEREIST_AANTAL, _BASIS_LABELS)
        if _basis_missers_op:
            _add_error('oefenmiddelen_basis',
                       'Vul een aantal in bij: ' + ', '.join(_basis_missers_op) + '.')
            ontbrekend.append('Praktisch — aantal "Wat regel jij"')
        _extra_missers_op = _praktisch_missende_aantallen(
            'oefenmiddelen_extra', _EXTRA_VEREIST_AANTAL, _EXTRA_LABELS)
        if _extra_missers_op:
            _add_error('oefenmiddelen_extra',
                       'Vul een aantal in bij: ' + ', '.join(_extra_missers_op) + '.')
            ontbrekend.append('Praktisch — aantal "Wat regelen wij"')
        if _tekst_leeg('oefendoel'):
            _add_error('oefendoel', 'Vul een oefendoel in.')
            ontbrekend.append('Oefendoel')
        if _json_lijst_min(
                'opdrachten_json', 1,
                key_check=lambda o: bool((o.get('titel') or '').strip()
                                          or (o.get('tekst') or '').strip())):
            _add_error('opdrachten_json', 'Voeg minstens 1 opdracht toe.')
            ontbrekend.append('Opdracht')
        else:
            # v0.7.35/37: ELKE opdracht in de lijst moet een titel hebben
            # (ook als-ie 'leeg lijkt' door alleen voorgevulde foto-slots).
            try:
                opdrachten = json.loads(request_form.get('opdrachten_json') or '[]')
            except (ValueError, TypeError):
                opdrachten = []
            valide_opdrachten = [o for o in opdrachten if isinstance(o, dict)]
            zonder_titel = [idx + 1 for idx, o in enumerate(valide_opdrachten)
                            if not (o.get('titel') or '').strip()]
            if zonder_titel:
                nrs = ', '.join(f'opdracht {n}' for n in zonder_titel)
                _add_error('opdrachten_json',
                            f'Elke opdracht heeft een titel nodig (ontbreekt bij: {nrs}). '
                            f'Verwijder lege opdrachten of vul een titel in.')
                ontbrekend.append('Opdracht-titel')
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
            tip['tekst_html'] = sanitize_rich_html(tip.get('tekst_html'))
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
            stap['tekst_html'] = sanitize_rich_html(stap.get('tekst_html'))
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
    totaal_archief = Kaart.query.filter_by(status='archief').count()
    for type_key, type_info in KAART_TYPES.items():
        actief = Kaart.query.filter(
            Kaart.type == type_key,
            Kaart.status != 'archief'
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
        query = query.filter_by(status='archief')
    else:
        weergave = 'actief'
        query = query.filter(Kaart.status != 'archief')
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
    kaarten = Kaart.query.filter_by(status='archief').order_by(Kaart.bijgewerkt_op.desc()).all()
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

        # v0.7.42 — Concept-kaarten krijgen een placeholder-nummer tot eerste
        # indienen. Zo blijven ze zichtbaar in lijsten zonder al een echt
        # XX-####-nummer te 'claimen' (en dus later te moeten reserveren bij
        # verwijdering).
        placeholder_nummer = f'CONCEPT-{uuid.uuid4().hex[:8]}'
        kaart = Kaart(
            type=kaart_type,
            nummer=placeholder_nummer,
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
            flash(f'Concept opgeslagen als {kaart.nummer_leesbaar}.', 'success')
            return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id) + f'#tab-{auto_save_tab}')
        flash(f'{kaart.type_naam} "{kaart.naam}" opgeslagen als concept ({kaart.nummer_leesbaar}).', 'success')
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
    # GET = openen in read-only als user geen bewerk-rechten heeft (iedereen die
    # ingelogd is mag kaarten lezen; dit zorgt dat specialist/oefencoord. uit
    # andere kerngroepen de inhoud kunnen zien zonder "forbidden"-modal).
    # POST = altijd strikte bewerk-check (geen bypass via directe form-submit).
    mag_bewerken = kan_kaart_bewerken(current_user, kaart)
    if request.method == 'POST':
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
            raw = huidige.get(veld, []) or []
            # Backward-compat: ouder pager_voertuigen was een vrije string
            # ("TS 2441"). Split op spaties zodat de SelectMultipleField
            # de losse tokens kan matchen op zijn choices.
            if isinstance(raw, str):
                raw = [t for t in raw.split() if t]
            data[veld] = raw
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
        flash(f'Concept opgeslagen ({kaart.nummer_leesbaar}).', 'info')
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

    # Gekoppelde kaarten + beschikbare kaarten voor koppel-dropdown
    # (v0.7.36: ook instructiekaart heeft nu een Gekoppelde-kaarten-blok op Achtergrond-tab)
    gekoppelde_kaarten = []
    beschikbaar_per_type = {}
    if kaart.type in ('scenario', 'opdracht', 'instructie'):
        gekoppelde_kaarten = kaart.get_gekoppelde_kaarten()
        huidige_ids = {k.id for k in gekoppelde_kaarten}
        huidige_ids.add(kaart.id)
        for type_key, t_info in KAART_TYPES.items():
            rijen = Kaart.query.filter(
                Kaart.type == type_key,
                Kaart.status != 'archief',
                ~Kaart.id.in_(huidige_ids),
            ).order_by(Kaart.nummer).all()
            if rijen:
                beschikbaar_per_type[type_key] = {'naam': t_info['naam'], 'kaarten': rijen}

    # Instructie-, opdracht- én scenariokaart: QR-koppelingen + beschikbare QR-codes.
    # (v0.7.36: scenario kreeg ook een QR-blok op de Verwijzingen-tab).
    # De InstructieQRLink-tabel is generiek gestructureerd (kaart_id FK).
    instructie_qr_links = []
    instructie_beschikbare_qrs_per_categorie = {}
    if kaart.type in ('instructie', 'opdracht', 'scenario'):
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
    thema_beschikbare_kaarten_per_type = {}  # {'instructie': [...], 'scenario': [...], 'opdracht': [...]}
    thema_beschikbare_qrs_per_categorie = {}  # {'oefening': [...], ...}
    if kaart.type == 'thema':
        thema_kaart_links = kaart.get_thema_kaart_links()
        thema_qr_links_op_volgorde = kaart.get_thema_qr_links_op_volgorde()

        gekoppelde_ids = {r.gekoppelde_kaart_id for groep in thema_kaart_links.values() for r in groep}
        gekoppelde_ids.add(kaart.id)
        beschikbare_kaarten = Kaart.query.filter(
            Kaart.type != 'thema',
            Kaart.status != 'archief',
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

    # Bij specialist/admin: lijst met kandidaat-makers meegeven zodat zij de
    # kaart via een UI-blok kunnen overdragen (ziekte/afwezigheid).
    eigenaar_kandidaten = []
    if current_user.is_admin or current_user.is_specialist:
        eigenaar_kandidaten = _mogelijke_nieuwe_makers(kaart)

    # Geschiedenis zichtbaar voor: admin, specialist (any), of een bijdrager
    # (iemand die ooit in de wijzigingen-log op deze kaart voorkomt). Zo zien
    # specialisten en betrokkenen altijd wat er met de kaart gebeurd is (incl.
    # overdrachten en review-besluiten).
    bijdrager_ids = {w.gebruiker_id for w in kaart.wijzigingen.all()
                      if w.gebruiker_id is not None}
    geschiedenis_zichtbaar = (
        current_user.is_admin
        or current_user.is_specialist
        or current_user.id in bijdrager_ids
        or current_user.id == kaart.auteur_id
    )
    # Voor de leesbare geschiedenis houden we alleen significante flow-overgangen
    # zichtbaar: aangemaakt / ingediend / review-besluiten / gepubliceerd /
    # teruggetrokken / nummer toegekend / eigenaar gewijzigd / overdracht.
    # Ruis (auto-saves, kleine 'Bewerkt'-regels, koppel-acties) blijft in de DB
    # voor audit-trail maar niet zichtbaar in deze lijst.
    ruis_acties = ['Auto-save%', 'Bewerkt', 'Koppeling %',
                    'Kaart-koppeling %', 'QR-koppeling %']
    wijziging_q = kaart.wijzigingen
    for patroon in ruis_acties:
        wijziging_q = wijziging_q.filter(~KaartWijziging.actie.like(patroon))
    kaart_wijzigingen = (wijziging_q.order_by(KaartWijziging.datum.desc())
                         .limit(50).all()
                         if geschiedenis_zichtbaar else [])

    return render_template('kaarten/formulier.html', form=form, kaart_type=kaart.type,
                           type_info=type_info, bewerken=True, kaart=kaart,
                           mag_bewerken=mag_bewerken,
                           eigenaar_kandidaten=eigenaar_kandidaten,
                           geschiedenis_zichtbaar=geschiedenis_zichtbaar,
                           kaart_wijzigingen=kaart_wijzigingen,
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
    if kaart.type not in ('instructie', 'opdracht', 'scenario'):
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
    if kaart.type not in ('instructie', 'opdracht', 'scenario'):
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
        q = q.filter(Kaart.status != 'archief')
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


@bp.route('/<int:kaart_id>/indienen', methods=['POST'])
@login_required
def indienen(kaart_id):
    """Zet een concept-kaart in peer-review. De route accepteert optioneel
    `peer_kerngroep` (voor Algemene kaarten waar de maker kerngroep met meeste
    affiniteit kiest). Bewerk-rechten op de kaart zijn vereist.

    Antwoord: JSON als client X-Requested-With: XMLHttpRequest meestuurt
    (voor de fetch-knop in formulier.html), anders flash + redirect.
    """
    from app.workflow import dien_in_voor_review
    from flask import jsonify
    kaart = Kaart.query.get_or_404(kaart_id)
    _vereis_bewerken(kaart)

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _antwoord(ok, boodschap, redirect_url=None, extra=None):
        if is_ajax:
            data = {'ok': ok, 'boodschap': boodschap}
            if redirect_url:
                data['redirect'] = redirect_url
            if extra:
                data.update(extra)
            return jsonify(data), (200 if ok else 400)
        flash(boodschap, 'success' if ok else 'danger')
        return redirect(redirect_url or url_for('kaarten.bewerken', kaart_id=kaart.id))

    # Specialisten doen alléén reviews — niet indienen. Hun eigen kaarten
    # publiceren ze direct via /publiceren. Admin mag wél indienen (noodgeval).
    if current_user.is_specialist and not current_user.is_admin:
        return _antwoord(False,
                          'Specialisten dienen zelf geen kaarten in voor review — '
                          'publiceer je eigen kaart direct met "Publiceren".')

    gekozen_kg = (request.form.get('peer_kerngroep') or '').strip()
    if gekozen_kg:
        from app.models import KERNGROEPEN
        if gekozen_kg not in KERNGROEPEN:
            return _antwoord(False, 'Ongeldige kerngroep gekozen.')
        kaart.peer_kerngroep = gekozen_kg

    # v0.7.40 — Een kaart mag alleen in review als alle verplichte velden gevuld
    # zijn. Reviewers moeten geen half-leeg concept op hun bord krijgen. We
    # hergebruiken dezelfde validator als bij "Opslaan" (_controleer_verplichte_velden):
    # bouw een MultiDict van de huidige kaart-inhoud + subcategorie, laat de
    # validator per veld checken, en weiger met een duidelijke lijst.
    ontbrekend = _valideer_kaart_volledig(kaart)
    if ontbrekend:
        kort = ', '.join(ontbrekend[:6]) + ('…' if len(ontbrekend) > 6 else '')
        return _antwoord(False,
                          'Kaart kan nog niet ingediend worden — vul eerst alle '
                          'verplichte velden in: ' + kort)

    # v0.7.42 — Bij EERSTE indienen krijgt de kaart zijn definitieve XX-####-
    # nummer (vervangt de CONCEPT-xxxxxxxx-placeholder). Daarna blijft het
    # nummer vast, ook als de kaart tussentijds terug naar concept gaat.
    if not kaart.heeft_definitief_nummer and kaart.subcategorie_id:
        sub = kaart.subcategorie
        if sub is not None and sub.kerntaak is not None:
            nieuw = Kaart.volgende_nummer(kaart.type, sub.kerntaak.cijfer, sub.cijfer)
            log_wijziging(kaart, 'Nummer toegekend',
                          f'{kaart.nummer} → {nieuw}')
            kaart.nummer = nieuw

    resultaat = dien_in_voor_review(kaart, current_user)
    if not resultaat['ok']:
        return _antwoord(False, resultaat['fout'])

    db.session.commit()
    # Verrijkte boodschap: bij wie ligt de kaart nu + wat gebeurt er daarna?
    # Zo ziet de maker direct dat er bv. ook een ensceneringstips-review loopt
    # (parallel aan de peer-review) en dat specialist-eindcheck het sluitstuk is.
    toegewezen = []
    for r in resultaat['reviews']:
        reviewer = r.reviewer
        label = (reviewer.kerngroep_naam
                 or reviewer.roi_kerntaak_naam
                 or reviewer.kerngroep or '').strip()
        toegewezen.append(f'{r.fase_naam} bij {reviewer.naam}'
                           + (f' ({label})' if label else ''))
    reviewers_tekst = '; '.join(toegewezen) if toegewezen else 'geen reviewers'
    boodschap = (f'Kaart "{kaart.naam}" is ingediend voor review. '
                 f'Ligt nu bij: {reviewers_tekst}. '
                 f'Na goedkeuring van alle 1e-ronde reviews gaat de kaart door '
                 f'naar de specialist-eindcheck.')
    return _antwoord(True, boodschap, redirect_url=url_for('kaarten.overzicht'))


@bp.route('/<int:kaart_id>/review/<int:review_id>', methods=['GET'])
@login_required
def review_pagina(kaart_id, review_id):
    """Review-taak openen. Alleen de toegewezen reviewer, een collega-reviewer
    (zelfde fase/kerngroep bij afwezigheid) of admin kan deze pagina openen."""
    from app.models import Review
    kaart = Kaart.query.get_or_404(kaart_id)
    review = Review.query.get_or_404(review_id)
    if review.kaart_id != kaart.id:
        abort(404)
    if not _mag_review_doen(current_user, review):
        abort(403)
    collegas = _collega_reviewers(review, current_user)
    return render_template('kaarten/review.html',
                           kaart=kaart, review=review,
                           kerntaak_info=kaart.kerntaak_info,
                           status_badge_class='badge-' + kaart.status,
                           overdraag_collegas=collegas)


@bp.route('/<int:kaart_id>/review/<int:review_id>/besluit', methods=['POST'])
@login_required
def review_besluit(kaart_id, review_id):
    """Verwerk GO of Afkeuren op een review. Antwoord via JSON (modal-friendly)
    als client X-Requested-With meestuurt, anders flash + redirect."""
    from app.models import Review
    from app.workflow import verwerk_review_besluit
    from flask import jsonify
    kaart = Kaart.query.get_or_404(kaart_id)
    review = Review.query.get_or_404(review_id)
    if review.kaart_id != kaart.id:
        abort(404)
    if not _mag_review_doen(current_user, review):
        abort(403)

    besluit = (request.form.get('besluit') or '').strip()
    opmerking = (request.form.get('opmerking') or '').strip()
    resultaat = verwerk_review_besluit(review, besluit, opmerking, current_user)

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if not resultaat['ok']:
        if is_ajax:
            return jsonify({'ok': False, 'boodschap': resultaat['fout']}), 400
        flash(resultaat['fout'], 'danger')
        return redirect(url_for('kaarten.review_pagina',
                                 kaart_id=kaart.id, review_id=review.id))

    db.session.commit()
    terug_url = url_for('main.dashboard')
    if is_ajax:
        return jsonify({'ok': True,
                         'boodschap': resultaat['boodschap'],
                         'redirect': terug_url})
    flash(resultaat['boodschap'], 'success')
    return redirect(terug_url)


def _valideer_kaart_volledig(kaart):
    """Check of alle verplichte velden gevuld zijn. Retourneert lijst ontbrekende
    labels (leeg = kaart is compleet).

    Werkt bovenop `_controleer_verplichte_velden` (dezelfde validator als bij
    'Opslaan') zodat we de regels maar één keer hoeven te onderhouden.
    """
    from werkzeug.datastructures import MultiDict
    inhoud = kaart.get_inhoud()
    rf = MultiDict()
    for veld, val in inhoud.items():
        if isinstance(val, list):
            for v in val:
                rf.add(veld, v if isinstance(v, str) else str(v))
        else:
            rf.add(veld, val if isinstance(val, str) else (str(val) if val is not None else ''))
    rf.add('subcategorie_id', str(kaart.subcategorie_id or ''))
    rf.add('kerntaak', kaart.kerntaak or '')
    rf.add('naam', kaart.naam or '')

    form_class = FORMULIEREN.get(kaart.type)
    if form_class is None:
        return []
    # meta={'csrf': False}: deze "nep"-form draait zonder CSRF-check — hij wordt
    # niet via een echte POST ingediend maar alleen gebruikt om errors op te hangen.
    nep = form_class(formdata=None, meta={'csrf': False})
    return _controleer_verplichte_velden(kaart.type, nep, rf, kaart=kaart)


def _mogelijke_nieuwe_makers(kaart):
    """Kandidaten om een kaart aan over te dragen bij ziekte/afwezigheid.

    - Oefencoördinatoren in de effectieve kerngroep van de kaart.
    - Medewerker O&O (alleen als het een materieel-instructiekaart betreft of
      de huidige maker ook O&O is — die zijn verder niet gebonden aan een
      kerngroep). Voor nu: altijd O&O meenemen; admin kiest wie het past.
    - Specialisten mogen géén kaart als maker nemen (zij reviewen alleen).
    """
    from app.models import User
    kand = []
    kg = kaart.effectieve_kerngroep
    for u in User.query.filter_by(actief=True).order_by(User.naam).all():
        if u.id == kaart.auteur_id:
            continue
        if u.is_oefencoordinator and kg and u.kerngroep == kg:
            kand.append(u)
        elif u.is_medewerker_oo:
            kand.append(u)
    return kand


@bp.route('/<int:kaart_id>/rechten-aanvragen', methods=['POST'])
@login_required
def rechten_aanvragen(kaart_id):
    """Een collega-oefencoord uit dezelfde kerngroep als de kaart vraagt bewerk-
    rechten aan. Dit creëert een OverdraagVerzoek waar de specialist uit zijn
    kerngroep op kan reageren via zijn dashboard.
    """
    from flask import jsonify
    from app.models import OverdraagVerzoek
    kaart = Kaart.query.get_or_404(kaart_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _fout(bood, status=400):
        if is_ajax:
            return jsonify({'ok': False, 'boodschap': bood}), status
        flash(bood, 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))

    # Regel: aanvrager moet oefencoördinator zijn uit DEZELFDE kerngroep als
    # de kaart. Buiten jouw kerngroep ben je geen logische vervanger. Admin
    # en specialist hebben eigen overdraag-knoppen.
    if kaart.auteur_id == current_user.id:
        return _fout('Jij bent de maker — je hebt al bewerk-rechten.')
    if not current_user.is_oefencoordinator:
        return _fout('Alleen een oefencoördinator uit de kerngroep van deze '
                      'kaart kan rechten aanvragen.', 403)
    if not current_user.kerngroep or current_user.kerngroep != kaart.effectieve_kerngroep:
        return _fout('Je kunt alleen rechten aanvragen op kaarten uit jouw '
                      'eigen kerngroep.', 403)

    bestaand = OverdraagVerzoek.query.filter_by(
        kaart_id=kaart.id, aanvrager_id=current_user.id, status='open').first()
    if bestaand:
        return _fout('Je hebt al een open verzoek op deze kaart lopen — '
                      'wacht op antwoord van de specialist.')

    bericht = (request.form.get('bericht') or '').strip()[:500]
    verzoek = OverdraagVerzoek(
        kaart_id=kaart.id, aanvrager_id=current_user.id,
        bericht=bericht, status='open'
    )
    db.session.add(verzoek)
    log_wijziging(
        kaart, 'rechten_aangevraagd',
        f'{current_user.naam} vraagt bewerk-rechten aan (specialist beslist).'
    )
    db.session.commit()

    bood = ('Verzoek verstuurd. De specialist uit jouw kerngroep ziet het op '
            'zijn dashboard en kan de kaart aan jou overdragen.')
    terug = url_for('kaarten.bewerken', kaart_id=kaart.id)
    if is_ajax:
        return jsonify({'ok': True, 'boodschap': bood, 'redirect': terug})
    flash(bood, 'success')
    return redirect(terug)


@bp.route('/overdraag-verzoek/<int:verzoek_id>/beslissen', methods=['POST'])
@login_required
def overdraag_verzoek_beslissen(verzoek_id):
    """Specialist beslist op een OverdraagVerzoek. Actie 'goedkeuren' draagt het
    makerschap over aan de aanvrager; 'afwijzen' sluit het verzoek.
    """
    from flask import jsonify
    from app.models import OverdraagVerzoek
    verzoek = OverdraagVerzoek.query.get_or_404(verzoek_id)
    kaart = verzoek.kaart
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _fout(bood, status=400):
        if is_ajax:
            return jsonify({'ok': False, 'boodschap': bood}), status
        flash(bood, 'danger')
        return redirect(url_for('main.dashboard'))

    # Toegestaan voor: admin, of specialist uit zelfde kerngroep als de kaart.
    if not current_user.is_admin:
        if not (current_user.is_specialist and current_user.kerngroep
                and current_user.kerngroep == kaart.effectieve_kerngroep):
            return _fout('Alleen de specialist uit de kerngroep van deze kaart '
                          '(of een admin) kan dit verzoek afhandelen.', 403)

    if verzoek.status != 'open':
        return _fout(f'Dit verzoek is al afgehandeld ({verzoek.status_naam}).')

    actie = (request.form.get('actie') or '').strip()
    from datetime import datetime as _dt
    if actie == 'goedkeuren':
        oude = kaart.auteur
        kaart.auteur_id = verzoek.aanvrager_id
        kaart.bijgewerkt_door_id = current_user.id
        verzoek.status = 'goedgekeurd'
        verzoek.afgehandeld_door_id = current_user.id
        verzoek.afgehandeld_op = _dt.utcnow()
        log_wijziging(
            kaart, 'eigenaar_gewijzigd',
            f'Rechten-verzoek goedgekeurd: makerschap overgedragen van '
            f'{oude.naam if oude else "?"} naar {verzoek.aanvrager.naam} '
            f'door specialist {current_user.naam}.'
        )
        db.session.commit()
        bood = (f'Verzoek goedgekeurd. Kaart "{kaart.naam}" is nu op naam van '
                f'{verzoek.aanvrager.naam}.')
    elif actie == 'afwijzen':
        verzoek.status = 'afgewezen'
        verzoek.afgehandeld_door_id = current_user.id
        verzoek.afgehandeld_op = _dt.utcnow()
        log_wijziging(
            kaart, 'rechten_afgewezen',
            f'Rechten-verzoek van {verzoek.aanvrager.naam} afgewezen door '
            f'{current_user.naam}.'
        )
        db.session.commit()
        bood = f'Verzoek van {verzoek.aanvrager.naam} is afgewezen.'
    else:
        return _fout('Onbekende actie.')

    terug = url_for('main.dashboard')
    if is_ajax:
        return jsonify({'ok': True, 'boodschap': bood, 'redirect': terug})
    flash(bood, 'success')
    return redirect(terug)


@bp.route('/<int:kaart_id>/eigenaar-wijzigen', methods=['POST'])
@login_required
def eigenaar_wijzigen(kaart_id):
    """Draag het makerschap van een kaart over aan een andere gebruiker.

    Reden: bij ziekte of langdurige afwezigheid van de maker moet iemand anders
    de kaart kunnen verderzetten (bewerken, intrekken, opnieuw indienen).
    Omdat alleen de maker bewerk-rechten heeft, is overdragen de enige weg.

    Toegestaan voor: admin + specialist (gezagsfunctie). Na de wissel is de
    oorspronkelijke maker geen maker meer — 'auteur_id' wordt overschreven.
    JSON-aware antwoord.
    """
    from flask import jsonify
    from app.models import User
    kaart = Kaart.query.get_or_404(kaart_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _fout(bood, status=400):
        if is_ajax:
            return jsonify({'ok': False, 'boodschap': bood}), status
        flash(bood, 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))

    if not (current_user.is_admin or current_user.is_specialist):
        return _fout('Alleen een specialist of admin kan het makerschap van een '
                      'kaart overdragen.', 403)

    nieuwe_id_raw = (request.form.get('nieuwe_maker_id') or '').strip()
    if not nieuwe_id_raw.isdigit():
        return _fout('Kies een nieuwe maker.')
    nieuwe = User.query.get(int(nieuwe_id_raw))
    if nieuwe is None or not nieuwe.actief:
        return _fout('Gekozen gebruiker bestaat niet of is inactief.')
    if nieuwe.id == kaart.auteur_id:
        return _fout('Deze gebruiker is al de huidige maker.')

    kand_ids = {u.id for u in _mogelijke_nieuwe_makers(kaart)}
    if nieuwe.id not in kand_ids:
        return _fout('Deze gebruiker past niet bij de kerngroep of rol van deze kaart.')

    oude = kaart.auteur
    kaart.auteur_id = nieuwe.id
    kaart.bijgewerkt_door_id = current_user.id
    log_wijziging(
        kaart, 'eigenaar_gewijzigd',
        f'Makerschap overgedragen van {oude.naam if oude else "?"} naar '
        f'{nieuwe.naam} door {current_user.naam}. '
        f'Alleen {nieuwe.naam} kan deze kaart nu nog bewerken.'
    )
    db.session.commit()

    bood = (f'Makerschap van "{kaart.naam}" is overgedragen aan {nieuwe.naam}. '
            f'{oude.naam if oude else "De vorige maker"} kan de kaart niet meer bewerken.')
    terug = url_for('kaarten.bewerken', kaart_id=kaart.id)
    if is_ajax:
        return jsonify({'ok': True, 'boodschap': bood, 'redirect': terug})
    flash(bood, 'success')
    return redirect(terug)


@bp.route('/<int:kaart_id>/terugtrekken', methods=['POST'])
@login_required
def terugtrekken(kaart_id):
    """Haal een kaart uit de review-flow terug naar concept.

    Lopende open reviews worden op 'ingetrokken' gezet (verdwijnen uit het
    reviewers-dashboard). De kaart gaat terug naar concept en moet opnieuw
    ingediend worden — de volledige flow (peer + eventueel ensceneringstips +
    specialist) start dan vers.

    Toegestaan voor: de maker en admin. Zolang de kaart in review ligt, slaat
    _vereis_bewerken strikter aan dan voor normale POSTs; die bypass gebruiken
    we hier NIET — we checken rechten expliciet.

    Antwoord: JSON bij X-Requested-With, anders flash + redirect.
    """
    from flask import jsonify
    from app.models import Review
    kaart = Kaart.query.get_or_404(kaart_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    # Terugtrekken mag door: de maker zelf of admin. Bij afwezigheid regelt
    # een specialist/admin eerst een eigenaar-wijziging (zie /eigenaar-wijzigen);
    # daarna is de nieuwe maker weer de enige die kan intrekken/bewerken.
    mag_terugtrekken = (current_user.is_admin
                        or kaart.auteur_id == current_user.id)
    if not mag_terugtrekken:
        if is_ajax:
            return jsonify({'ok': False,
                             'boodschap': 'Je mag deze kaart niet terugtrekken.'}), 403
        abort(403)

    if not kaart_is_in_review(kaart):
        boodschap = 'Deze kaart staat niet in review — niets om terug te trekken.'
        if is_ajax:
            return jsonify({'ok': False, 'boodschap': boodschap}), 400
        flash(boodschap, 'warning')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))

    # Als deze kaart ooit gepubliceerd is geweest, moet de intrekker een reden
    # opgeven. Zo blijft in de audit-trail traceerbaar waarom een eerder
    # vrijgegeven versie uit circulatie is gehaald.
    ooit_gepubliceerd = (
        KaartWijziging.query
        .filter_by(kaart_id=kaart.id, actie='Gepubliceerd')
        .count() > 0
    )
    reden = (request.form.get('reden') or '').strip()
    if ooit_gepubliceerd and len(reden) < 5:
        boodschap = ('Geef een korte reden op waarom deze eerder gepubliceerde kaart '
                     'wordt teruggetrokken (min. 5 tekens).')
        if is_ajax:
            return jsonify({'ok': False, 'boodschap': boodschap,
                             'reden_vereist': True}), 400
        flash(boodschap, 'warning')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))
    reden = reden[:500]  # harde cap tegen overvolle log-notities

    open_reviews = Review.query.filter_by(kaart_id=kaart.id, status='open').all()
    from datetime import datetime as _dt
    for r in open_reviews:
        r.status = 'ingetrokken'
        r.voltooid_op = _dt.utcnow()

    kaart.status = 'concept'
    kaart.bijgewerkt_door_id = current_user.id
    notitie = f'{len(open_reviews)} open review(s) geannuleerd; kaart terug naar concept.'
    if ooit_gepubliceerd:
        notitie += f' Eerder gepubliceerd — reden intrekken: "{reden}"'
    log_wijziging(kaart, 'Teruggetrokken', notitie)
    db.session.commit()

    boodschap = ('Kaart is teruggetrokken uit de review. '
                 'Pas aan en dien opnieuw in om de flow opnieuw te starten.')
    terug = url_for('kaarten.bewerken', kaart_id=kaart.id)
    if is_ajax:
        return jsonify({'ok': True, 'boodschap': boodschap, 'redirect': terug})
    flash(boodschap, 'success')
    return redirect(terug)


def _mag_archiveren(user, kaart):
    """Specialist van de kerntaak van de kaart (effectieve kerngroep) of admin
    mag een gepubliceerde kaart archiveren en terugzetten.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_admin:
        return True
    if user.is_specialist and user.kerngroep:
        return user.kerngroep == kaart.effectieve_kerngroep
    return False


def _kaarten_die_koppelen_naar(kaart):
    """Lijst van kaarten die naar DEZE kaart verwijzen via thema-kaart-links of
    anderszins. Gebruikt in de archiveer-bevestig-modal als waarschuwing.
    """
    from app.models import ThemaKaartLink
    rows = (ThemaKaartLink.query
            .filter_by(gekoppelde_kaart_id=kaart.id)
            .all())
    unieke_kaart_ids = {r.kaart_id for r in rows}
    if not unieke_kaart_ids:
        return []
    return (Kaart.query
            .filter(Kaart.id.in_(unieke_kaart_ids))
            .order_by(Kaart.nummer)
            .all())


@bp.route('/<int:kaart_id>/archiveren', methods=['POST'])
@login_required
def archiveren(kaart_id):
    """Archiveer een gepubliceerde kaart. Alleen specialist van de kerntaak
    of admin. Kaart blijft raadpleegbaar maar niet bewerkbaar en niet opnieuw
    in te dienen."""
    from flask import jsonify
    kaart = Kaart.query.get_or_404(kaart_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _fout(bood, status=400):
        if is_ajax:
            return jsonify({'ok': False, 'boodschap': bood}), status
        flash(bood, 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))

    if not _mag_archiveren(current_user, kaart):
        return _fout('Alleen de specialist van deze kerntaak of een admin mag deze '
                      'kaart archiveren.', 403)
    if kaart.status != 'gepubliceerd':
        return _fout('Alleen gepubliceerde kaarten kunnen gearchiveerd worden.', 400)

    kaart.status = 'archief'
    log_wijziging(
        kaart, 'Gearchiveerd',
        f'Kaart uit actief gebruik gehaald door {current_user.naam} ({current_user.rol_naam}).'
    )
    db.session.commit()

    boodschap = f'Kaart "{kaart.naam}" is gearchiveerd. QR-codes blijven werken.'
    terug = url_for('kaarten.bewerken', kaart_id=kaart.id)
    if is_ajax:
        return jsonify({'ok': True, 'boodschap': boodschap, 'redirect': terug})
    flash(boodschap, 'success')
    return redirect(terug)


@bp.route('/<int:kaart_id>/dearchiveren', methods=['POST'])
@login_required
def dearchiveren(kaart_id):
    """Zet een gearchiveerde kaart terug op 'gepubliceerd'."""
    from flask import jsonify
    kaart = Kaart.query.get_or_404(kaart_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _fout(bood, status=400):
        if is_ajax:
            return jsonify({'ok': False, 'boodschap': bood}), status
        flash(bood, 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))

    if not _mag_archiveren(current_user, kaart):
        return _fout('Alleen de specialist van deze kerntaak of een admin mag deze '
                      'kaart uit het archief halen.', 403)
    if kaart.status != 'archief':
        return _fout('Deze kaart staat niet in het archief.', 400)

    kaart.status = 'gepubliceerd'
    log_wijziging(
        kaart, 'Uit archief gehaald',
        f'Kaart terug in actief gebruik door {current_user.naam} ({current_user.rol_naam}).'
    )
    db.session.commit()

    boodschap = f'Kaart "{kaart.naam}" staat weer in actief gebruik.'
    terug = url_for('kaarten.bewerken', kaart_id=kaart.id)
    if is_ajax:
        return jsonify({'ok': True, 'boodschap': boodschap, 'redirect': terug})
    flash(boodschap, 'success')
    return redirect(terug)


@bp.route('/<int:kaart_id>/koppelend-naar', methods=['GET'])
@login_required
def koppelend_naar(kaart_id):
    """JSON-helper voor de archiveer-modal: geeft de lijst kaarten terug die
    naar deze kaart verwijzen (thema-koppelingen). Zo kan de UI een
    waarschuwing tonen voordat de gebruiker bevestigt."""
    from flask import jsonify
    kaart = Kaart.query.get_or_404(kaart_id)
    kaarten = _kaarten_die_koppelen_naar(kaart)
    return jsonify({
        'ok': True,
        'count': len(kaarten),
        'kaarten': [{'id': k.id, 'nummer': k.nummer_leesbaar,
                     'naam': k.naam, 'status': k.status,
                     'status_naam': k.status_naam} for k in kaarten]
    })


@bp.route('/<int:kaart_id>/verwijderen', methods=['POST'])
@login_required
def verwijderen(kaart_id):
    """Verwijder een concept-kaart definitief. Toegestaan alléén als:
    - status = 'concept' EN
    - de kaart heeft NOG NOOIT een definitief XX-####-nummer gekregen
      (= nog nooit ingediend geweest), EN
    - de huidige gebruiker is de maker of een admin.

    Zodra een kaart ooit in de workflow is geweest (definitief nummer gekregen)
    blijft die permanent bestaan — ook na afkeur of terugtrekken — voor de
    logging en traceerbaarheid.
    """
    from flask import jsonify
    kaart = Kaart.query.get_or_404(kaart_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _fout(bood, status=400):
        if is_ajax:
            return jsonify({'ok': False, 'boodschap': bood}), status
        flash(bood, 'danger')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))

    # Rechten-check: alleen maker of admin.
    if not (current_user.is_admin or kaart.auteur_id == current_user.id):
        return _fout('Alleen de maker of een admin mag deze kaart verwijderen.', 403)

    # Statuscheck: alleen concept.
    if kaart.status != 'concept':
        return _fout('Alleen concept-kaarten mogen verwijderd worden. '
                      'Trek de kaart eerst terug uit de review als-ie daar nog ligt.', 400)

    # Levensloop-check: zodra er een definitief nummer was toegekend blijft de
    # kaart bewaard (voor logging + traceerbaarheid in de workflow).
    if kaart.heeft_definitief_nummer:
        return _fout(f'Deze kaart heeft al nummer {kaart.nummer} gekregen en is in de '
                      'workflow geweest — nummer en kaart blijven bewaard voor de logging. '
                      'Verwijderen kan niet meer; wel kan een specialist of admin het '
                      'makerschap overdragen als dat nodig is.', 400)

    # Ruim fysieke bestanden op (header-foto + overige uploads die alleen aan
    # deze kaart hangen). Reviews, wijzigingen en link-tabellen vallen onder
    # cascade (FK).
    bestandsnamen = [kaart.header_foto, kaart.ensceneringstips_foto,
                      kaart.ensceneringstips_foto_2, kaart.productfoto]
    for bn in bestandsnamen:
        if bn:
            verwijder_bestand(bn)

    kaart_naam = kaart.naam
    db.session.delete(kaart)
    db.session.commit()

    boodschap = f'Kaart "{kaart_naam}" is verwijderd.'
    terug = url_for('kaarten.overzicht')
    if is_ajax:
        return jsonify({'ok': True, 'boodschap': boodschap, 'redirect': terug})
    flash(boodschap, 'success')
    return redirect(terug)


def _collega_reviewers(review, huidige_user):
    """Alle gebruikers die deze review MOGEN doen, minus de huidige user en de
    maker. Gebruikt dezelfde regels als _mag_review_doen. Retourneert User-lijst.
    """
    from app.models import User
    kaart = review.kaart
    kandidaten = []
    for u in User.query.filter_by(actief=True).order_by(User.naam).all():
        if u.id == huidige_user.id:
            continue
        if u.id == kaart.auteur_id:
            continue
        if review.fase == 'peer':
            if u.is_oefencoordinator and u.kerngroep == kaart.effectieve_kerngroep:
                kandidaten.append(u)
        elif review.fase == 'ensceneringstips':
            if u.is_medewerker_oo:
                kandidaten.append(u)
        elif review.fase == 'specialist':
            if u.is_specialist and u.kerngroep:
                if kaart.effectieve_kerngroep == 'roi' or u.kerngroep != kaart.effectieve_kerngroep:
                    kandidaten.append(u)
    return kandidaten


@bp.route('/<int:kaart_id>/review/<int:review_id>/overdragen', methods=['POST'])
@login_required
def review_overdragen(kaart_id, review_id):
    """Draag deze review-taak over aan een andere collega uit dezelfde pool
    (bv. bij ziekte of te drukke agenda).

    Toegestaan voor: de huidige reviewer, een collega uit zelfde pool, of admin.
    De nieuwe reviewer MOET uit de reviewer-pool komen (anders hebben we een
    rechten-lek). JSON-aware antwoord.
    """
    from flask import jsonify
    from app.models import Review, User
    kaart = Kaart.query.get_or_404(kaart_id)
    review = Review.query.get_or_404(review_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _fout(bood, status=400):
        if is_ajax:
            return jsonify({'ok': False, 'boodschap': bood}), status
        flash(bood, 'danger')
        return redirect(url_for('kaarten.review_pagina',
                                 kaart_id=kaart.id, review_id=review.id))

    if review.kaart_id != kaart.id:
        abort(404)
    if review.status != 'open':
        return _fout('Deze review-taak is al afgerond — overdragen kan niet meer.')
    # Alleen admin mag review-taken handmatig doorzetten; reviewers (medewerker
    # O&O, specialist, oefencoord) dragen hun eigen taken NIET zelf over.
    if not current_user.is_admin:
        return _fout('Alleen een admin kan een review-taak handmatig overdragen.',
                      403)

    nieuwe_id_raw = (request.form.get('nieuwe_reviewer_id') or '').strip()
    if not nieuwe_id_raw.isdigit():
        return _fout('Kies een collega om aan over te dragen.')
    nieuwe = User.query.get(int(nieuwe_id_raw))
    if nieuwe is None or not nieuwe.actief:
        return _fout('Gekozen gebruiker bestaat niet of is inactief.')
    if nieuwe.id == kaart.auteur_id:
        return _fout('De maker kan zijn eigen kaart niet reviewen.')
    # Valideer dat de nieuwe gebruiker daadwerkelijk in de pool zit (zelfde regels
    # als _mag_review_doen). Zo voorkomen we dat iemand zomaar aan een willekeurige
    # user kan doorgeven.
    pool_ids = {u.id for u in _collega_reviewers(review, current_user)}
    pool_ids.add(current_user.id)  # jezelf mag ook (impliciete overname)
    if nieuwe.id not in pool_ids:
        return _fout('Deze collega hoort niet bij de reviewer-pool voor deze fase.')

    oude_naam = review.reviewer.naam if review.reviewer else '?'
    review.reviewer_id = nieuwe.id
    log_wijziging(
        kaart, 'review_overgedragen',
        f'{review.fase_naam}: overgedragen van {oude_naam} naar {nieuwe.naam} '
        f'door {current_user.naam}.'
    )
    db.session.commit()

    bood = f'Review-taak overgedragen aan {nieuwe.naam}. Het verdwijnt van jouw lijst.'
    terug = url_for('main.dashboard')
    if is_ajax:
        return jsonify({'ok': True, 'boodschap': bood, 'redirect': terug})
    flash(bood, 'success')
    return redirect(terug)


def _mag_review_doen(user, review):
    """Toegewezen reviewer, collega uit zelfde pool bij afwezigheid, of admin."""
    if not user or not user.is_authenticated:
        return False
    if user.is_admin:
        return True
    if review.reviewer_id == user.id:
        return True
    # Collega-reviewer uit zelfde "pool" mag overnemen (bv. afwezigheid):
    kaart = review.kaart
    if review.fase == 'peer':
        return user.is_oefencoordinator and user.kerngroep == kaart.effectieve_kerngroep \
               and user.id != kaart.auteur_id
    if review.fase == 'ensceneringstips':
        return user.is_medewerker_oo and user.id != kaart.auteur_id
    if review.fase == 'specialist':
        if not user.is_specialist or not user.kerngroep:
            return False
        if kaart.effectieve_kerngroep == 'roi':
            return True
        return user.kerngroep != kaart.effectieve_kerngroep
    return False


@bp.route('/<int:kaart_id>/publiceren', methods=['POST'])
@login_required
def publiceren(kaart_id):
    kaart = Kaart.query.get_or_404(kaart_id)
    _vereis_publiceren()
    _vereis_bewerken(kaart)
    if kaart.status == 'archief':
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
    # Een depublicatie komt per definitie vanaf 'gepubliceerd' → altijd een
    # reden vereist voor de audit-trail.
    reden = (request.form.get('reden') or '').strip()
    if len(reden) < 5:
        flash('Geef een korte reden op waarom deze kaart gedepubliceerd wordt (min. 5 tekens).',
              'warning')
        return redirect(url_for('kaarten.bewerken', kaart_id=kaart.id))
    reden = reden[:500]
    kaart.status = 'concept'
    kaart.bijgewerkt_door_id = current_user.id
    log_wijziging(kaart, 'Teruggezet naar concept',
                   f'Reden depublicatie: "{reden}"')
    db.session.commit()
    flash(f'Kaart "{kaart.naam}" staat weer als concept.', 'info')
    return redirect(url_for('kaarten.overzicht'))


# NB: /archiveren + /dearchiveren routes staan boven bij _mag_archiveren.
# De oude routes (die via _vereis_bewerken naar concept zetten) zijn vervangen
# door de strikte versie: specialist van de kerntaak of admin, overgang
# gepubliceerd ↔ archief.


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
    bestandsnaam = f'{kaart.nummer_leesbaar}_{kaart.naam}.pdf'.replace(' ', '_')
    response.headers['Content-Disposition'] = f'inline; filename="{bestandsnaam}"'
    return response
