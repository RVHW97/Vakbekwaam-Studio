import base64
import json
import os
from flask import current_app, render_template
from weasyprint import HTML
from app.models import KAART_TYPES, KERNTAKEN, kenmerken_kerntaak_label
from app.kaarten.forms import FORMULIEREN, INHOUD_VELDEN, PBM_KEUZES


def _qr_data_uri(qr, met_tekst=False):
    """Render een QR-code (witte stijl, geen tekst) als base64 data-URI voor inbedden in PDF."""
    from app.qr import generator as gen
    buf = gen.render_png_bytes(qr, stijl='wit', met_tekst=met_tekst)
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
    font_paden = {
        'regular': 'file://' + os.path.join(fonts_dir, 'Frutiger Normal.ttf'),
        'italic': 'file://' + os.path.join(fonts_dir, 'Frutiger Roman Italic.ttf'),
        'bold': 'file://' + os.path.join(fonts_dir, 'FTB.ttf'),
        'black': 'file://' + os.path.join(fonts_dir, 'FTUBL.ttf'),
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

    # Ensceneringstips foto pad
    tips_foto_pad = None
    if kaart.ensceneringstips_foto:
        pad = os.path.join(upload_folder, kaart.ensceneringstips_foto)
        if os.path.exists(pad):
            tips_foto_pad = 'file://' + pad

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

        # PBM-labels uit de keuzes-constante
        pbm_dict = dict(PBM_KEUZES)
        pbm_gekozen = inhoud.get('pbm_items') or []
        pbm_lijst = [{'key': k, 'naam': pbm_dict.get(k, k)} for k in pbm_gekozen if k in pbm_dict]

        # LMRA-URL (centrale config) en optioneel QR daarvoor (later in 6b)
        lmra_url = current_app.config.get('LMRA_QR_URL') or ''

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
                                      pbm_lijst=pbm_lijst,
                                      lmra_url=lmra_url,
                                      instructie_qrs=instructie_qrs,
                                      gekoppelde=gekoppelde,
                                      header_foto_pad=header_foto_pad,
                                      logo_pad=logo_pad,
                                      logo_wit_pad=logo_wit_pad,
                                      kerntaak_kleur=kerntaak_kleur,
                                      kerntaak_afk=kerntaak_afk,
                                      versie_tekst=versie_tekst,
                                      versie_datum_tekst=versie_datum_tekst,
                                      font_paden=font_paden)
        return HTML(string=html_string).write_pdf()

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
                                  tips_foto_pad=tips_foto_pad)

    pdf = HTML(string=html_string).write_pdf()
    return pdf
