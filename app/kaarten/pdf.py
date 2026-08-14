import base64
import json
import os
from flask import current_app, render_template
from weasyprint import HTML
from app.models import KAART_TYPES, KERNTAKEN, kenmerken_kerntaak_label
from app.kaarten.forms import FORMULIEREN, INHOUD_VELDEN, VEILIGHEID_MAX_ZINNEN, DOELGROEP_KEUZES


def _qr_data_uri(qr, met_tekst=False):
    """Render een QR-code (witte stijl, geen tekst) als base64 data-URI voor inbedden in PDF."""
    from app.qr import generator as gen
    buf = gen.render_png_bytes(qr, stijl='wit', met_tekst=met_tekst)
    b64 = base64.b64encode(buf.getvalue()).decode('ascii')
    return f'data:image/png;base64,{b64}'


def _url_qr_data_uri(url):
    """Render een QR voor een losse URL-string (geen QRCode-model nodig).
    Gebruikt voor de LMRA-QR die als app-brede config-waarde staat."""
    import io
    import qrcode
    from qrcode.constants import ERROR_CORRECT_H
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_H, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='#1B2A4A', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode('ascii')
    return f'data:image/png;base64,{b64}'


def genereer_pdf(kaart):
    """Genereer een professionele PDF voor een kaart."""
    inhoud = kaart.get_inhoud()
    velden = INHOUD_VELDEN.get(kaart.type, [])

    # Labels ophalen uit het formulier
    form_class = FORMULIEREN[kaart.type]
    temp_form = form_class()
    labels = {}
    for veld in velden:
        field = getattr(temp_form, veld, None)
        labels[veld] = field.label.text if field else veld
    if 'kenmerken_kerntaak' in labels:
        labels['kenmerken_kerntaak'] = kenmerken_kerntaak_label(kaart.kerntaak)

    # Afbeeldingen met absolute paden
    afbeeldingen = []
    upload_folder = current_app.config['UPLOAD_FOLDER']
    for afb in kaart.afbeeldingen:
        pad = os.path.join(upload_folder, afb.bestandsnaam)
        if os.path.exists(pad):
            afbeeldingen.append({
                'pad': 'file://' + pad,
                'naam': afb.beschrijving or afb.originele_naam,
            })

    # Logo pad
    logo_pad = 'file://' + os.path.join(current_app.root_path, 'static', 'img', 'logo.png')
    favicon_pad = 'file://' + os.path.join(current_app.root_path, 'static', 'img', 'favicon.svg')
    header_svg_pad = 'file://' + os.path.join(current_app.root_path, 'static', 'img', 'header-decoratie.svg')
    logo_wit_pad = 'file://' + os.path.join(current_app.root_path, 'static', 'img', 'logo-wit.svg')
    pager_c35_pad = 'file://' + os.path.join(current_app.root_path, 'static', 'img', 'pager-c35.png')

    # Frutiger fonts (file:// URLs voor WeasyPrint).
    # Frutiger Normal.ttf = 55 Roman (regular), FTB.ttf = 65 Bold, FTUBL.ttf = 95 Ultra Black.
    fonts_dir = os.path.join(current_app.root_path, 'static', 'fonts')
    open_sans_dir = os.path.join(fonts_dir, 'open-sans')
    font_paden = {
        'regular': 'file://' + os.path.join(fonts_dir, 'Frutiger Normal.ttf'),
        'italic': 'file://' + os.path.join(fonts_dir, 'Frutiger Roman Italic.ttf'),
        'bold': 'file://' + os.path.join(fonts_dir, 'FTB.ttf'),
        'black': 'file://' + os.path.join(fonts_dir, 'FTUBL.ttf'),
        # Open Sans lokaal (geen Google-Fonts-fetch bij render — offline-veilig, GDPR-veilig).
        'os_300': 'file://' + os.path.join(open_sans_dir, 'open-sans-300.woff2'),
        'os_400': 'file://' + os.path.join(open_sans_dir, 'open-sans-400.woff2'),
        'os_400_italic': 'file://' + os.path.join(open_sans_dir, 'open-sans-400-italic.woff2'),
        'os_600': 'file://' + os.path.join(open_sans_dir, 'open-sans-600.woff2'),
        'os_700': 'file://' + os.path.join(open_sans_dir, 'open-sans-700.woff2'),
        'os_800': 'file://' + os.path.join(open_sans_dir, 'open-sans-800.woff2'),
    }

    # Versie-info voor footer
    if kaart.versie and kaart.versie > 0 and kaart.versie_datum:
        versie_tekst = f'v{kaart.versie}'
        versie_datum_tekst = kaart.versie_datum.strftime('%d-%m-%Y')
    else:
        versie_tekst = 'concept'
        versie_datum_tekst = kaart.bijgewerkt_op.strftime('%d-%m-%Y')

    # Thematische kleur per kaarttype (alleen nog voor gekoppelde-kaarten badges)
    type_kleuren = {
        'thema': '#4B70A6',      # Water blauw
        'instructie': '#4C7F52', # THV groen
        'scenario': '#B6463D',   # Brand rood
        'opdracht': '#DAB94F',   # IBGS geel
    }
    type_kleur = type_kleuren.get(kaart.type, '#CC9933')

    # Kerntaak-info voor zijbalk. Bij geen kerntaak: grijze bar, "—".
    if kaart.kerntaak and kaart.kerntaak in KERNTAKEN:
        kerntaak_info = KERNTAKEN[kaart.kerntaak]
        kerntaak_kleur = kerntaak_info['kleur']
        kerntaak_afk = kerntaak_info['afkorting']
    else:
        kerntaak_kleur = '#B8B2A4'
        kerntaak_afk = ''

    # Headerfoto pad
    header_foto_pad = None
    if kaart.header_foto:
        pad = os.path.join(upload_folder, kaart.header_foto)
        if os.path.exists(pad):
            header_foto_pad = 'file://' + pad

    # Ensceneringstips: JSON-lijst met max 3 tips × max 2 foto's per tip.
    # Fallback: als de opgeslagen ensceneringstips-waarde nog oude tekst-vorm is
    # (van vóór de tips-editor), converteer naar lijst van tekst-only tips.
    tips_stappen = []
    raw_tips = (inhoud.get('ensceneringstips') or '').strip()
    if raw_tips:
        try:
            parsed = json.loads(raw_tips)
            if isinstance(parsed, list):
                tips_stappen = parsed
        except (ValueError, TypeError):
            # Oude text-vorm: elke niet-lege regel wordt 1 tip zonder foto.
            for regel in raw_tips.split('\n'):
                r = regel.strip()
                if r:
                    tips_stappen.append({'tekst': r, 'fotos': []})
    # Foto-paden absoluut maken.
    for tip in tips_stappen:
        if not isinstance(tip, dict):
            continue
        for foto in (tip.get('fotos') or []):
            bestand = (foto.get('bestand') or '').strip() if isinstance(foto, dict) else ''
            if bestand:
                pad = os.path.join(upload_folder, bestand)
                foto['pad'] = 'file://' + pad if os.path.exists(pad) else None
            else:
                foto['pad'] = None

    # Legacy: standalone ensceneringstips_foto(_2) blijven bruikbaar voor kaarten
    # die nog geen tips-JSON hebben.
    tips_foto_pad = None
    if kaart.ensceneringstips_foto:
        pad = os.path.join(upload_folder, kaart.ensceneringstips_foto)
        if os.path.exists(pad):
            tips_foto_pad = 'file://' + pad
    tips_foto_2_pad = None
    if getattr(kaart, 'ensceneringstips_foto_2', None):
        pad = os.path.join(upload_folder, kaart.ensceneringstips_foto_2)
        if os.path.exists(pad):
            tips_foto_2_pad = 'file://' + pad

    # Gekoppelde kaarten (handmatig gekoppeld via verwijzingen-tab)
    gekoppelde = []
    for k in kaart.get_gekoppelde_kaarten():
        gekoppelde.append({
            'nummer': k.nummer,
            'naam': k.naam,
            'type_naam': k.type_naam,
            'kleur': type_kleuren.get(k.type, '#CC9933'),
        })

    # === THEMAKAART: aparte 1-pagina A4-landscape layout ===
    if kaart.type == 'thema':
        # Tussentitels + gekoppelde kaarten per groep (0/1/2)
        koppelingen = kaart.get_thema_kaart_links()
        thema_groepen = []
        for idx in (0, 1, 2):
            titel = inhoud.get(f'tussentitel_{idx + 1}') or ''
            kaarten = [link.gekoppelde_kaart for link in koppelingen.get(idx, [])
                       if link.gekoppelde_kaart is not None]
            thema_groepen.append({'titel': titel, 'kaarten': kaarten})

        # QR-codes auto-verdeeld over top/bottom
        qr_verdeling = kaart.get_thema_qr_verdeling()
        qr_top = [{'data_uri': _qr_data_uri(link.qr_code),
                   'label': link.label or link.qr_code.naam}
                  for link in qr_verdeling['top'] if link.qr_code is not None]
        qr_bottom = [{'data_uri': _qr_data_uri(link.qr_code),
                      'label': link.label or link.qr_code.naam}
                     for link in qr_verdeling['bottom'] if link.qr_code is not None]

        html_string = render_template('kaarten/pdf_thema.html',
                                      kaart=kaart,
                                      inhoud=inhoud,
                                      thema_groepen=thema_groepen,
                                      qr_top=qr_top,
                                      qr_bottom=qr_bottom,
                                      header_foto_pad=header_foto_pad,
                                      logo_wit_pad=logo_wit_pad,
                                      kerntaak_kleur=kerntaak_kleur,
                                      versie_tekst=versie_tekst,
                                      versie_datum_tekst=versie_datum_tekst,
                                      font_paden=font_paden)
        return HTML(string=html_string).write_pdf()

    # === KENNISKAART: A4-staand multi-page layout ===
    if kaart.type == 'kennis':
        # v0.7.13: max 5 hoofdstukken, elk met titel + rich HTML + 1-3 foto's.
        # De HTML in elk hoofdstuk is server-side gesanitized bij opslaan.
        try:
            kennis_hoofdstukken = json.loads(inhoud.get('kernboodschap_hoofdstukken_json') or '[]')
            if not isinstance(kennis_hoofdstukken, list):
                kennis_hoofdstukken = []
        except (ValueError, TypeError):
            kennis_hoofdstukken = []

        for hoofdstuk in kennis_hoofdstukken:
            if not isinstance(hoofdstuk, dict):
                continue
            for foto in (hoofdstuk.get('fotos') or []):
                if not isinstance(foto, dict):
                    continue
                bestand = (foto.get('bestand') or '').strip()
                if bestand:
                    pad = os.path.join(upload_folder, bestand)
                    foto['pad'] = 'file://' + pad if os.path.exists(pad) else None
                else:
                    foto['pad'] = None

        # Doelgroep-tekst: dropdown-keuze of vrije tekst bij 'anders'.
        _dg_keuzes = dict(DOELGROEP_KEUZES)
        _dg_val = (inhoud.get('doelgroep') or '').strip()
        if _dg_val == 'anders':
            doelgroep_tekst = (inhoud.get('doelgroep_anders') or '').strip()
        else:
            doelgroep_tekst = _dg_keuzes.get(_dg_val, '') if _dg_val else ''

        # Evaluatie: regel-per-punt in het textarea (verdiepende vragen zijn
        # sinds v0.7.12 uit de kenniskaart verwijderd).
        def _lijst_uit_tekst(veld):
            ruw = (inhoud.get(veld) or '').strip()
            return [r.strip() for r in ruw.split('\n') if r.strip()]
        evaluatie_punten = _lijst_uit_tekst('evaluatie')

        # Gekoppelde QR-codes uit de bank (dezelfde tabel als instructie/opdracht).
        kennis_qrs = []
        for link in kaart.get_instructie_qr_links():
            if link.qr_code is None:
                continue
            kennis_qrs.append({
                'data_uri': _qr_data_uri(link.qr_code),
                'naam': link.qr_code.naam,
                'categorie': link.qr_code.categorie_naam,
            })

        html_string = render_template('kaarten/pdf_kennis.html',
                                      kaart=kaart,
                                      inhoud=inhoud,
                                      doelgroep_tekst=doelgroep_tekst,
                                      kennis_hoofdstukken=kennis_hoofdstukken,
                                      evaluatie_punten=evaluatie_punten,
                                      kennis_qrs=kennis_qrs,
                                      header_foto_pad=header_foto_pad,
                                      logo_pad=logo_pad,
                                      logo_wit_pad=logo_wit_pad,
                                      kerntaak_kleur=kerntaak_kleur,
                                      kerntaak_afk=kerntaak_afk,
                                      versie_tekst=versie_tekst,
                                      versie_datum_tekst=versie_datum_tekst,
                                      font_paden=font_paden)
        return HTML(string=html_string).write_pdf()

    # === INSTRUCTIEKAART: A4-staand multi-page layout ===
    if kaart.type == 'instructie':
        # Parse JSON-velden (zijn opgeslagen als JSON-strings binnen inhoud-dict)
        try:
            werkwijze_stappen = json.loads(inhoud.get('werkwijze_stappen_json') or '[]')
            if not isinstance(werkwijze_stappen, list):
                werkwijze_stappen = []
        except (ValueError, TypeError):
            werkwijze_stappen = []
        try:
            productfoto_markers = json.loads(inhoud.get('productfoto_markers_json') or '[]')
            if not isinstance(productfoto_markers, list):
                productfoto_markers = []
        except (ValueError, TypeError):
            productfoto_markers = []

        # Productfoto pad
        productfoto_pad = None
        if kaart.productfoto:
            pad = os.path.join(upload_folder, kaart.productfoto)
            if os.path.exists(pad):
                productfoto_pad = 'file://' + pad

        # Foto-paden in werkwijze-stappen vervangen door file://
        for stap in werkwijze_stappen:
            if not isinstance(stap, dict):
                continue
            fotos = stap.get('fotos') or []
            for foto in fotos:
                if not isinstance(foto, dict):
                    continue
                bestand = (foto.get('bestand') or '').strip()
                if bestand:
                    pad = os.path.join(upload_folder, bestand)
                    foto['pad'] = 'file://' + pad if os.path.exists(pad) else None
                else:
                    foto['pad'] = None

        # Veiligheids-zinnen: maximaal VEILIGHEID_MAX_ZINNEN ingevulde regels.
        veiligheid_zinnen = []
        for i in range(1, VEILIGHEID_MAX_ZINNEN + 1):
            zin = (inhoud.get(f'veiligheid_zin_{i}') or '').strip()
            if zin:
                veiligheid_zinnen.append(zin)

        # LMRA-URL (centrale config) → echte QR genereren als URL gezet is.
        lmra_url = current_app.config.get('LMRA_QR_URL') or ''
        lmra_qr_data_uri = _url_qr_data_uri(lmra_url) if lmra_url else None

        # Gekoppelde QR-codes uit de bank
        instructie_qrs = []
        for link in kaart.get_instructie_qr_links():
            if link.qr_code is None:
                continue
            instructie_qrs.append({
                'data_uri': _qr_data_uri(link.qr_code),
                'naam': link.qr_code.naam,
                'categorie': link.qr_code.categorie_naam,
            })

        html_string = render_template('kaarten/pdf_instructie.html',
                                      kaart=kaart,
                                      inhoud=inhoud,
                                      werkwijze_stappen=werkwijze_stappen,
                                      productfoto_markers=productfoto_markers,
                                      productfoto_pad=productfoto_pad,
                                      veiligheid_zinnen=veiligheid_zinnen,
                                      lmra_url=lmra_url,
                                      lmra_qr_data_uri=lmra_qr_data_uri,
                                      instructie_qrs=instructie_qrs,
                                      header_foto_pad=header_foto_pad,
                                      logo_pad=logo_pad,
                                      logo_wit_pad=logo_wit_pad,
                                      kerntaak_kleur=kerntaak_kleur,
                                      kerntaak_afk=kerntaak_afk,
                                      versie_tekst=versie_tekst,
                                      versie_datum_tekst=versie_datum_tekst,
                                      font_paden=font_paden)
        return HTML(string=html_string).write_pdf()

    # Voor opdrachtkaart: veiligheidszinnen + LMRA + opdrachten (met foto-paden)
    # + oefenmiddelen-lijsten (JSON) omgezet naar leesbare labels.
    veiligheid_zinnen = []
    lmra_qr_data_uri = None
    opdracht_stappen = []
    oefenmiddelen_basis_labels = []
    oefenmiddelen_extra_labels = []
    opdracht_qrs = []

    # Oefenmiddelen JSON → labels — gedeeld tussen scenario- en opdrachtkaart.
    if kaart.type in ('scenario', 'opdracht'):
        BASIS_LABELS = {'ts': 'TS', 'basisoefenset': 'Basisoefenset'}
        EXTRA_LABELS = {'ademlucht': 'Ademlucht'}
        try:
            basis_items = json.loads(inhoud.get('oefenmiddelen_basis') or '[]')
        except (ValueError, TypeError):
            basis_items = []
        if isinstance(basis_items, list):
            for it in basis_items:
                if not isinstance(it, dict):
                    continue
                t = (it.get('type') or '').strip()
                aantal = (it.get('aantal') or '').strip()
                if t == 'anders':
                    tekst = (it.get('tekst') or '').strip()
                    if tekst:
                        oefenmiddelen_basis_labels.append(f'{aantal} × {tekst}' if aantal else tekst)
                elif t in BASIS_LABELS:
                    naam = BASIS_LABELS[t]
                    oefenmiddelen_basis_labels.append(f'{aantal} × {naam}' if aantal else naam)
        try:
            extra_items = json.loads(inhoud.get('oefenmiddelen_extra') or '[]')
        except (ValueError, TypeError):
            extra_items = []
        if isinstance(extra_items, list):
            for it in extra_items:
                if not isinstance(it, dict):
                    continue
                t = (it.get('type') or '').strip()
                aantal = (it.get('aantal') or '').strip()
                if t == 'anders':
                    tekst = (it.get('tekst') or '').strip()
                    if tekst:
                        oefenmiddelen_extra_labels.append(f'{aantal} × {tekst}' if aantal else tekst)
                elif t in EXTRA_LABELS:
                    naam = EXTRA_LABELS[t]
                    oefenmiddelen_extra_labels.append(f'{aantal} × {naam}' if aantal else naam)

    if kaart.type == 'opdracht':
        for i in range(1, VEILIGHEID_MAX_ZINNEN + 1):
            zin = (inhoud.get(f'veiligheid_zin_{i}') or '').strip()
            if zin:
                veiligheid_zinnen.append(zin)
        lmra_url = current_app.config.get('LMRA_QR_URL') or ''
        lmra_qr_data_uri = _url_qr_data_uri(lmra_url) if lmra_url else None

        try:
            opdracht_stappen = json.loads(inhoud.get('opdrachten_json') or '[]')
            if not isinstance(opdracht_stappen, list):
                opdracht_stappen = []
        except (ValueError, TypeError):
            opdracht_stappen = []

        # Gekoppelde QR-codes uit de bank (achtergrond-tab). Reuse InstructieQRLink.
        for link in kaart.get_instructie_qr_links():
            if link.qr_code is None:
                continue
            opdracht_qrs.append({
                'data_uri': _qr_data_uri(link.qr_code),
                'naam': link.qr_code.naam,
                'categorie': link.qr_code.categorie_naam,
            })
        # Foto-paden absoluut maken zodat WeasyPrint ze kan laden.
        for opdr in opdracht_stappen:
            if not isinstance(opdr, dict):
                continue
            for foto in (opdr.get('fotos') or []):
                bestand = (foto.get('bestand') or '').strip() if isinstance(foto, dict) else ''
                if bestand:
                    pad = os.path.join(upload_folder, bestand)
                    foto['pad'] = 'file://' + pad if os.path.exists(pad) else None
                else:
                    foto['pad'] = None

    html_string = render_template('kaarten/pdf_template.html',
                                  kaart=kaart,
                                  inhoud=inhoud,
                                  velden=velden,
                                  labels=labels,
                                  afbeeldingen=afbeeldingen,
                                  logo_pad=logo_pad,
                                  favicon_pad=favicon_pad,
                                  type_kleur=type_kleur,
                                  gekoppelde=gekoppelde,
                                  font_paden=font_paden,
                                  versie_tekst=versie_tekst,
                                  versie_datum_tekst=versie_datum_tekst,
                                  header_svg_pad=header_svg_pad,
                                  logo_wit_pad=logo_wit_pad,
                                  pager_c35_pad=pager_c35_pad,
                                  kerntaak_kleur=kerntaak_kleur,
                                  kerntaak_afk=kerntaak_afk,
                                  header_foto_pad=header_foto_pad,
                                  tips_foto_pad=tips_foto_pad,
                                  tips_foto_2_pad=tips_foto_2_pad,
                                  tips_stappen=tips_stappen,
                                  veiligheid_zinnen=veiligheid_zinnen,
                                  lmra_qr_data_uri=lmra_qr_data_uri,
                                  opdracht_stappen=opdracht_stappen,
                                  oefenmiddelen_basis_labels=oefenmiddelen_basis_labels,
                                  oefenmiddelen_extra_labels=oefenmiddelen_extra_labels,
                                  opdracht_qrs=opdracht_qrs)

    pdf = HTML(string=html_string).write_pdf()
    return pdf
