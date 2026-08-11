"""Beheer-blueprint — admin-only pagina's voor systeeminstellingen.

Nummering: de 10 kerntaken (0-9) staan VAST in de code (KERNTAKEN_SEED in
models.py) en zijn niet via de UI te wijzigen. Alleen subcategorieën zijn
door de admin beheerbaar. Voedt fase 2 (formulier) en fase 3 (generator).
"""
from flask import render_template, redirect, url_for, flash, request
from app import db
from app.beheer import bp
from app.models import Kerntaak, Subcategorie
from app.auth.routes import admin_required


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
