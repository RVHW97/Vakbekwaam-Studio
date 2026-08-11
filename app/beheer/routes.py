"""Beheer-blueprint — admin-only pagina's voor systeeminstellingen.

Nummering: kerntaken (0-9) en hun subcategorieen (0-9) beheren, met naam en kleur.
Voedt fase 2 (formulier) en fase 3 (nummer-generator) van de nummering-refactor.
"""
import re
from flask import render_template, redirect, url_for, flash, request, abort
from flask_login import current_user
from app import db
from app.beheer import bp
from app.models import Kerntaak, Subcategorie
from app.auth.routes import admin_required


HEX_KLEUR_PAT = re.compile(r'^#[0-9A-Fa-f]{6}$')


def _valideer_cijfer(waarde, veldnaam='cijfer'):
    """Cast naar int en check op 0-9. Retourneert (int_of_None, foutmelding_of_None)."""
    if waarde is None or waarde == '':
        return None, f'{veldnaam.capitalize()} is verplicht.'
    try:
        n = int(waarde)
    except (ValueError, TypeError):
        return None, f'{veldnaam.capitalize()} moet een cijfer 0-9 zijn.'
    if n < 0 or n > 9:
        return None, f'{veldnaam.capitalize()} moet tussen 0 en 9 liggen.'
    return n, None


@bp.route('/nummering', methods=['GET'])
@admin_required
def nummering():
    kerntaken = Kerntaak.query.order_by(Kerntaak.cijfer).all()
    return render_template('beheer/nummering.html', kerntaken=kerntaken)


# ------------------------- Kerntaak-acties -------------------------

@bp.route('/nummering/kerntaak/nieuw', methods=['POST'])
@admin_required
def kerntaak_nieuw():
    cijfer, fout = _valideer_cijfer(request.form.get('cijfer'), 'cijfer')
    if fout:
        flash(fout, 'danger')
        return redirect(url_for('beheer.nummering'))
    if Kerntaak.query.filter_by(cijfer=cijfer).first():
        flash(f'Er bestaat al een kerntaak met cijfer {cijfer}.', 'danger')
        return redirect(url_for('beheer.nummering'))
    naam = (request.form.get('naam') or '').strip()
    if not naam:
        flash('Naam is verplicht.', 'danger')
        return redirect(url_for('beheer.nummering'))
    afkorting = (request.form.get('afkorting') or '').strip().upper()[:10]
    kleur = (request.form.get('kleur') or '').strip() or '#B8B2A4'
    if not HEX_KLEUR_PAT.match(kleur):
        kleur = '#B8B2A4'
    # Slug op basis van naam; unieke suffix bij botsing.
    basis_sleutel = re.sub(r'[^a-z0-9]+', '_', naam.lower()).strip('_') or f'kerntaak_{cijfer}'
    sleutel = basis_sleutel
    n = 2
    while Kerntaak.query.filter_by(sleutel=sleutel).first():
        sleutel = f'{basis_sleutel}_{n}'
        n += 1
    kt = Kerntaak(cijfer=cijfer, sleutel=sleutel, naam=naam,
                  afkorting=afkorting or naam[:3].upper(), kleur=kleur)
    db.session.add(kt)
    db.session.commit()
    flash(f'Kerntaak "{kt.naam}" toegevoegd.', 'success')
    return redirect(url_for('beheer.nummering'))


@bp.route('/nummering/kerntaak/<int:kerntaak_id>/opslaan', methods=['POST'])
@admin_required
def kerntaak_opslaan(kerntaak_id):
    kt = Kerntaak.query.get_or_404(kerntaak_id)
    naam = (request.form.get('naam') or '').strip()
    if not naam:
        flash('Naam is verplicht.', 'danger')
        return redirect(url_for('beheer.nummering'))
    afkorting = (request.form.get('afkorting') or '').strip().upper()[:10]
    kleur = (request.form.get('kleur') or '').strip() or '#B8B2A4'
    if not HEX_KLEUR_PAT.match(kleur):
        flash(f'Ongeldige kleurcode "{kleur}" — moet #RRGGBB zijn.', 'danger')
        return redirect(url_for('beheer.nummering'))
    kt.naam = naam
    kt.afkorting = afkorting or kt.afkorting
    kt.kleur = kleur
    db.session.commit()
    flash(f'Kerntaak {kt.cijfer} — {kt.naam} bijgewerkt.', 'success')
    return redirect(url_for('beheer.nummering'))


@bp.route('/nummering/kerntaak/<int:kerntaak_id>/verwijderen', methods=['POST'])
@admin_required
def kerntaak_verwijderen(kerntaak_id):
    kt = Kerntaak.query.get_or_404(kerntaak_id)
    naam = kt.naam
    db.session.delete(kt)  # cascade wist ook subcategorieen
    db.session.commit()
    flash(f'Kerntaak "{naam}" (en zijn subcategorieen) verwijderd.', 'info')
    return redirect(url_for('beheer.nummering'))


# ------------------------- Subcategorie-acties -------------------------

@bp.route('/nummering/kerntaak/<int:kerntaak_id>/subcategorie/nieuw', methods=['POST'])
@admin_required
def subcategorie_nieuw(kerntaak_id):
    kt = Kerntaak.query.get_or_404(kerntaak_id)
    cijfer, fout = _valideer_cijfer(request.form.get('cijfer'), 'cijfer')
    if fout:
        flash(fout, 'danger')
        return redirect(url_for('beheer.nummering'))
    if Subcategorie.query.filter_by(kerntaak_id=kt.id, cijfer=cijfer).first():
        flash(f'Er bestaat al een subcategorie met cijfer {cijfer} onder {kt.naam}.', 'danger')
        return redirect(url_for('beheer.nummering'))
    naam = (request.form.get('naam') or '').strip()
    if not naam:
        flash('Naam is verplicht.', 'danger')
        return redirect(url_for('beheer.nummering'))
    sub = Subcategorie(kerntaak_id=kt.id, cijfer=cijfer, naam=naam)
    db.session.add(sub)
    db.session.commit()
    flash(f'Subcategorie {kt.cijfer}{cijfer} toegevoegd.', 'success')
    return redirect(url_for('beheer.nummering'))


@bp.route('/nummering/subcategorie/<int:sub_id>/opslaan', methods=['POST'])
@admin_required
def subcategorie_opslaan(sub_id):
    sub = Subcategorie.query.get_or_404(sub_id)
    naam = (request.form.get('naam') or '').strip()
    if not naam:
        flash('Naam is verplicht.', 'danger')
        return redirect(url_for('beheer.nummering'))
    sub.naam = naam
    db.session.commit()
    flash(f'Subcategorie {sub.kerntaak.cijfer}{sub.cijfer} bijgewerkt.', 'success')
    return redirect(url_for('beheer.nummering'))


@bp.route('/nummering/subcategorie/<int:sub_id>/verwijderen', methods=['POST'])
@admin_required
def subcategorie_verwijderen(sub_id):
    sub = Subcategorie.query.get_or_404(sub_id)
    label = f'{sub.kerntaak.cijfer}{sub.cijfer}'
    db.session.delete(sub)
    db.session.commit()
    flash(f'Subcategorie {label} verwijderd.', 'info')
    return redirect(url_for('beheer.nummering'))
