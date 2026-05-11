"""QR-code generator met huisstijl-opmaak.

Vier stijlen:
 - navy:        navy achtergrond, witte QR, gouden schild, witte tekst.
 - rood:        brandweer-rode achtergrond, witte QR, gouden schild, witte tekst.
 - goud:        gouden achtergrond, navy QR, navy schild, navy tekst.
 - transparant: geen achtergrond, navy QR, gouden schild, navy tekst (om te plakken).
"""
import io
import os
from flask import current_app, url_for

import cairosvg
import qrcode
from qrcode.constants import ERROR_CORRECT_H
from PIL import Image, ImageDraw, ImageFont


KLEUR_NAVY = '#1B2A4A'
KLEUR_GOUD = '#CC9933'
KLEUR_ROOD = '#C8102E'
KLEUR_WIT  = '#FFFFFF'

QR_MODULE_PX = 12
QR_BORDER_MODULES = 4
SCHILD_BREEDTE_FACTOR = 0.18   # schild ~18% van de QR-breedte

STIJLEN = {
    'navy':        {'bg': KLEUR_NAVY, 'qr': KLEUR_WIT,  'schild': KLEUR_GOUD, 'cirkel': KLEUR_WIT,  'tekst': KLEUR_WIT},
    'rood':        {'bg': KLEUR_ROOD, 'qr': KLEUR_WIT,  'schild': KLEUR_GOUD, 'cirkel': KLEUR_WIT,  'tekst': KLEUR_WIT},
    'goud':        {'bg': KLEUR_GOUD, 'qr': KLEUR_WIT,  'schild': KLEUR_GOUD, 'cirkel': KLEUR_WIT,  'tekst': KLEUR_WIT},
    'transparant': {'bg': None,       'qr': KLEUR_NAVY, 'schild': KLEUR_GOUD, 'cirkel': KLEUR_WIT,  'tekst': KLEUR_NAVY},
    'wit':         {'bg': KLEUR_WIT,  'qr': KLEUR_NAVY, 'schild': KLEUR_GOUD, 'cirkel': KLEUR_WIT,  'tekst': KLEUR_NAVY},
}


def _stijl(naam):
    return STIJLEN.get(naam) or STIJLEN['navy']


def _hex_naar_rgb(hex_kleur):
    h = hex_kleur.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _public_url_voor_qr(qr):
    return url_for('main.qr_redirect', slug=qr.slug, _external=True)


def _laad_font(kandidaten, size):
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    paden = [os.path.join(project_root, 'huisstijl', 'fonts', f) for f in kandidaten]
    paden += ['/System/Library/Fonts/Supplemental/Arial Bold.ttf',
              '/System/Library/Fonts/Helvetica.ttc']
    for p in paden:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except (OSError, IOError):
                continue
    return ImageFont.load_default()


def _laad_schild_pil(grootte_px):
    """Laad het brandweer-schild uit huisstijl/brandweer-1.svg."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    svg_pad = os.path.join(project_root, 'huisstijl', 'brandweer-1.svg')
    if not os.path.exists(svg_pad):
        return None
    try:
        png = cairosvg.svg2png(url=svg_pad, output_width=grootte_px)
        return Image.open(io.BytesIO(png)).convert('RGBA')
    except Exception:
        return None


def _kleur_schild(schild_img, hex_kleur):
    """Vervang de kleur van het schild door hex_kleur, behoud alpha-mask."""
    if schild_img is None:
        return None
    r, g, b = _hex_naar_rgb(hex_kleur)
    nieuwe = Image.new('RGBA', schild_img.size, (r, g, b, 0))
    alpha = schild_img.split()[-1]
    nieuwe.putalpha(alpha)
    return nieuwe


def genereer_qr_pil(qr_code, stijl='navy'):
    """QR als PIL RGBA-image, zonder tekst/schild, in de juiste kleuren voor de stijl."""
    s = _stijl(stijl)
    doel_url = _public_url_voor_qr(qr_code)
    fill_kleur = s['qr']
    bg_kleur = s['bg']

    # Voor wit-stijl een krappere quiet-zone (2 modules): tegel op themakaart bevat
    # alleen QR + zeer smal wit randje, geen brede band eromheen.
    border = 2 if stijl == 'wit' else QR_BORDER_MODULES

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,
        box_size=QR_MODULE_PX,
        border=border,
    )
    qr.add_data(doel_url)
    qr.make(fit=True)

    if bg_kleur is None:
        # Transparante variant: render op witte back, converteer wit naar transparant
        img = qr.make_image(fill_color=fill_kleur, back_color='white').convert('RGBA')
        datas = img.getdata()
        nieuwe = [(r, g, b, 0) if (r, g, b) == (255, 255, 255) else (r, g, b, 255)
                  for (r, g, b, _a) in datas]
        img.putdata(nieuwe)
    else:
        img = qr.make_image(fill_color=fill_kleur, back_color=bg_kleur).convert('RGBA')
    return img


def _voeg_schild_toe(qr_img, schild_kleur, cirkel_kleur=KLEUR_WIT):
    """Plak het schild (in opgegeven kleur) in het midden van de QR.

    Cirkel achter het schild zorgt dat hij leesbaar blijft op elk patroon.
    """
    qr_b, qr_h = qr_img.size
    schild_grootte = int(qr_b * SCHILD_BREEDTE_FACTOR)
    schild = _laad_schild_pil(schild_grootte)
    if not schild:
        return qr_img
    schild = _kleur_schild(schild, schild_kleur)

    cirkel_grootte = int(max(schild.size) * 1.45)
    cirkel = Image.new('RGBA', (cirkel_grootte, cirkel_grootte), (0, 0, 0, 0))
    ImageDraw.Draw(cirkel).ellipse((0, 0, cirkel_grootte, cirkel_grootte), fill=cirkel_kleur)

    cx = (qr_b - cirkel_grootte) // 2
    cy = (qr_h - cirkel_grootte) // 2
    qr_img.alpha_composite(cirkel, (cx, cy))

    sx = (qr_b - schild.size[0]) // 2
    sy = (qr_h - schild.size[1]) // 2
    qr_img.alpha_composite(schild, (sx, sy))
    return qr_img


def render_png(qr_code, stijl='navy', met_tekst=True, met_schild=True):
    """Complete PIL-image: afgerond gekleurd vlak + QR + schild + optionele tekst."""
    s = _stijl(stijl)
    qr_img = genereer_qr_pil(qr_code, stijl=stijl)
    if met_schild:
        qr_img = _voeg_schild_toe(qr_img, s['schild'], s.get('cirkel', KLEUR_WIT))

    qr_b, qr_h = qr_img.size
    tekst_onder = (qr_code.tekst_onder or '').strip() if met_tekst else ''
    tekst_kleur = s['tekst']

    # Wit-stijl zonder tekst: direct qr_img returnen — geen extra canvas met witte
    # marge. De qr_img heeft zijn eigen quiet-zone (border=2 modules) wat al genoeg is.
    if stijl == 'wit' and not tekst_onder:
        return qr_img

    # Kleinere marge bij wit-stijl met tekst: alleen ruimte voor de tekst eronder.
    marge = 8 if stijl == 'wit' else 60
    font_onder = _laad_font(
        ['Frutiger Ultra Black Regular.ttf', 'FTUBL.ttf', 'FTB.ttf'],
        size=max(34, qr_b // 20),
    )
    tekst_h = 0
    if tekst_onder:
        bbox = font_onder.getbbox(tekst_onder)
        tekst_h = (bbox[3] - bbox[1]) + 36

    totaal_b = qr_b + 2 * marge
    totaal_h = qr_h + 2 * marge + tekst_h

    canvas = Image.new('RGBA', (totaal_b, totaal_h), (0, 0, 0, 0))
    if s['bg'] is not None:
        bg = Image.new('RGBA', (totaal_b, totaal_h), (0, 0, 0, 0))
        bg_draw = ImageDraw.Draw(bg)
        try:
            bg_draw.rounded_rectangle(
                (0, 0, totaal_b - 1, totaal_h - 1),
                radius=36,
                fill=s['bg'],
            )
        except AttributeError:
            bg_draw.rectangle((0, 0, totaal_b - 1, totaal_h - 1), fill=s['bg'])
        canvas.alpha_composite(bg, (0, 0))

    qr_x = (totaal_b - qr_b) // 2
    qr_y = marge
    canvas.alpha_composite(qr_img, (qr_x, qr_y))

    if tekst_onder:
        draw = ImageDraw.Draw(canvas)
        bbox = font_onder.getbbox(tekst_onder)
        t_b = bbox[2] - bbox[0]
        t_y = qr_y + qr_h + 18
        draw.text(((totaal_b - t_b) / 2, t_y), tekst_onder,
                  fill=tekst_kleur, font=font_onder)

    return canvas


def render_png_bytes(qr_code, stijl='navy', met_tekst=True):
    img = render_png(qr_code, stijl=stijl, met_tekst=met_tekst)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf


def render_svg_bytes(qr_code, stijl='navy'):
    """SVG-variant met dezelfde huisstijl. Oneindig scherp voor drukwerk."""
    from qrcode.image.svg import SvgPathImage
    import xml.etree.ElementTree as ET

    s = _stijl(stijl)
    doel_url = _public_url_voor_qr(qr_code)
    fill_kleur = s['qr']

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_H, border=QR_BORDER_MODULES, box_size=10)
    qr.add_data(doel_url)
    qr.make(fit=True)
    svg_img = qr.make_image(image_factory=SvgPathImage,
                            fill_color=fill_kleur, back_color='white')

    buf = io.BytesIO()
    svg_img.save(buf)
    qr_svg_tekst = buf.getvalue().decode('utf-8')

    try:
        inner_tree = ET.fromstring(qr_svg_tekst)
    except ET.ParseError:
        return io.BytesIO(qr_svg_tekst.encode('utf-8'))

    inner_xml = ''.join(ET.tostring(child, encoding='unicode') for child in inner_tree)
    inner_xml = inner_xml.replace('xmlns:ns0="http://www.w3.org/2000/svg"', '')
    inner_xml = inner_xml.replace('ns0:', '')

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    schild_svg_pad = os.path.join(project_root, 'huisstijl', 'brandweer-1.svg')
    schild_inner = ''
    if os.path.exists(schild_svg_pad):
        with open(schild_svg_pad) as f:
            schild_svg = f.read()
        import re
        m = re.search(r'<path[^/]*/>', schild_svg)
        if m:
            schild_inner = m.group(0)

    qr_grootte = 300
    marge = 40
    tekst_h = 40 if qr_code.tekst_onder else 0
    breedte = qr_grootte + 2 * marge
    hoogte = qr_grootte + 2 * marge + tekst_h

    achtergrond_svg = (
        '' if s['bg'] is None else
        f'<rect x="0" y="0" width="{breedte}" height="{hoogte}" rx="22" ry="22" fill="{s["bg"]}"/>'
    )

    schild_grootte = int(qr_grootte * SCHILD_BREEDTE_FACTOR)
    cirkel_grootte = int(schild_grootte * 1.45)
    cx = breedte / 2
    cy = marge + qr_grootte / 2
    schild_svg_inline = ''
    if schild_inner:
        cirkel_kleur = s.get('cirkel', KLEUR_WIT)
        schild_svg_inline = (
            f'<circle cx="{cx}" cy="{cy}" r="{cirkel_grootte/2}" fill="{cirkel_kleur}"/>'
            f'<g transform="translate({cx - schild_grootte/2}, {cy - schild_grootte/2}) '
            f'scale({schild_grootte / 30.31})" fill="{s["schild"]}">{schild_inner}</g>'
        )

    tekst_svg = ''
    if qr_code.tekst_onder:
        tekst_svg = (
            f'<text x="{breedte//2}" y="{qr_grootte + marge + 30}" '
            f'text-anchor="middle" font-family="Frutiger, Arial, sans-serif" font-weight="900" '
            f'font-size="20" fill="{s["tekst"]}">{qr_code.tekst_onder}</text>'
        )

    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {breedte} {hoogte}" width="{breedte}" height="{hoogte}">
  {achtergrond_svg}
  <g transform="translate({marge}, {marge}) scale({qr_grootte / 100})">
    {inner_xml}
  </g>
  {schild_svg_inline}
  {tekst_svg}
</svg>'''

    return io.BytesIO(svg.encode('utf-8'))


def render_pdf_bytes(qr_code, stijl='navy'):
    """Render QR als A4-PDF (gecentreerd)."""
    from flask import render_template
    from weasyprint import HTML

    png_buf = render_png_bytes(qr_code, stijl=stijl)
    import base64
    png_b64 = base64.b64encode(png_buf.getvalue()).decode('ascii')

    html = render_template('qr/pdf_qr.html',
                           qr=qr_code,
                           png_data_uri=f'data:image/png;base64,{png_b64}')
    pdf_bytes = HTML(string=html, base_url=current_app.root_path).write_pdf()
    return io.BytesIO(pdf_bytes)
