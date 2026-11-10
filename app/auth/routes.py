from functools import wraps
from urllib.parse import urlparse
from flask import render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
from app import db
from app.auth import bp
from app.auth.forms import LoginForm, NieuweGebruikerForm, GebruikerBewerkenForm
from app.models import (User, ROLLEN_MET_KERNGROEP, SPECIALIST_KERNGROEPEN,
                        KERNGROEPEN, ROI_KERNTAKEN)

# Dummy-hash om de check_password_hash-tijd óók te betalen bij onbekende e-mailadressen.
# Zonder deze extra hash is een timing-side-channel te meten.
_DUMMY_HASH = ('pbkdf2:sha256:600000$dummydummydummydummy$'
               '0000000000000000000000000000000000000000000000000000000000000000')


def _veilige_next_url(next_url):
    """Sta alleen relatieve paden binnen dezelfde host toe — voorkomt open-redirect."""
    if not next_url:
        return None
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return None
    if not next_url.startswith('/'):
        return None
    return next_url


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_beheerder:
            # Flash-category 'toegang_geweigerd' triggert in base.html een modal
            # i.p.v. de gewone banner — zodat Rebecca & co meteen zien WAAROM de
            # link niets deed, en niet overrompeld worden door een harde redirect.
            flash('Deze pagina is alleen toegankelijk voor admins.',
                   'toegang_geweigerd')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


def _valideer_rol_kerngroep(rol, kerngroep, form, roi_kerntaak=None):
    """Zorgt dat rol en kerngroep (resp. ROI-kerntaak) bij elkaar passen voor
    opslag.
    - Oefencoordinator en specialist moeten een kerngroep hebben uit KERNGROEPEN
      (brand/natuurbrand/water/thv/ibgs). Geen ROI meer: kerngroep ROI bestaat
      niet; oefencoord-ROI is een aparte rol.
    - Oefencoordinator_roi moet een roi_kerntaak hebben (brand/ibgs/thv/water/
      natuurbrand/specialistisch) en GEEN kerngroep.
    - Admin en medewerker_oo mogen géén kerngroep en géén roi_kerntaak hebben.
    Retourneert een foutmelding (string) of None als alles OK is.
    """
    if rol == 'oefencoordinator_roi':
        if kerngroep:
            form.kerngroep.errors = list(form.kerngroep.errors) + [
                'Oefencoördinator ROI heeft geen kerngroep nodig — kies "— geen kerngroep —".']
            return 'Oefencoördinator ROI mag geen kerngroep hebben.'
        if not roi_kerntaak:
            if hasattr(form, 'roi_kerntaak'):
                form.roi_kerntaak.errors = list(form.roi_kerntaak.errors) + [
                    'Kies een ROI-kerntaak.']
            return 'Oefencoördinator ROI vereist een kerntaak-koppeling.'
        if roi_kerntaak not in ROI_KERNTAKEN:
            if hasattr(form, 'roi_kerntaak'):
                form.roi_kerntaak.errors = list(form.roi_kerntaak.errors) + [
                    'Ongeldige ROI-kerntaak.']
            return 'Ongeldige ROI-kerntaak.'
        return None

    if rol in ROLLEN_MET_KERNGROEP:
        if not kerngroep:
            form.kerngroep.errors = list(form.kerngroep.errors) + [
                'Kies een kerngroep voor deze rol.']
            return 'Deze rol vereist een kerngroep.'
        if kerngroep not in KERNGROEPEN:
            form.kerngroep.errors = list(form.kerngroep.errors) + [
                'Ongeldige kerngroep.']
            return 'Ongeldige kerngroep.'
        if roi_kerntaak:
            if hasattr(form, 'roi_kerntaak'):
                form.roi_kerntaak.errors = list(form.roi_kerntaak.errors) + [
                    'Deze rol heeft geen ROI-kerntaak nodig.']
            return 'Deze rol mag geen ROI-kerntaak hebben.'
        return None

    # Admin / medewerker_oo mogen géén kerngroep en géén roi_kerntaak hebben.
    if kerngroep:
        form.kerngroep.errors = list(form.kerngroep.errors) + [
            'Deze rol heeft geen kerngroep nodig — kies "— geen kerngroep —".']
        return 'Deze rol mag geen kerngroep hebben.'
    if roi_kerntaak:
        if hasattr(form, 'roi_kerntaak'):
            form.roi_kerntaak.errors = list(form.roi_kerntaak.errors) + [
                'Deze rol heeft geen ROI-kerntaak nodig.']
        return 'Deze rol mag geen ROI-kerntaak hebben.'
    return None


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    form = LoginForm()
    if request.method == 'POST':
        if form.validate_on_submit():
            user = User.query.filter_by(email=form.email.data.lower().strip()).first()
            if user and user.actief and user.check_wachtwoord(form.wachtwoord.data):
                login_user(user, remember=form.onthoud_mij.data)
                next_page = _veilige_next_url(request.args.get('next'))
                return redirect(next_page or url_for('main.dashboard'))
            # Betaal altijd de hash-tijd, ook bij onbekende gebruiker of gedeactiveerd account.
            check_password_hash(_DUMMY_HASH, form.wachtwoord.data)
            flash('Ongeldige inloggegevens of account is gedeactiveerd.', 'danger')
        else:
            # validate_on_submit faalt stilletjes (bv. ongeldig e-mailformaat of leeg
            # veld) — toon de ECHTE reden zodat de gebruiker weet waarom het niet lukt
            # i.p.v. een lege form zonder uitleg.
            foutmeldingen = []
            for veld_naam, errors in form.errors.items():
                veld = getattr(form, veld_naam, None)
                label = veld.label.text if veld and veld.label else veld_naam
                for e in errors:
                    foutmeldingen.append(f'{label}: {e}')
            if foutmeldingen:
                flash('Inloggen mislukt — ' + ' | '.join(foutmeldingen), 'danger')
            else:
                flash('Inloggen mislukt — probeer het opnieuw.', 'danger')

    return render_template('auth/login.html', form=form)


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Je bent uitgelogd.', 'info')
    return redirect(url_for('auth.login'))


@bp.route('/gebruikers')
@admin_required
def gebruikers():
    users = User.query.order_by(User.naam).all()
    return render_template('auth/gebruikers.html', users=users)


@bp.route('/gebruikers/nieuw', methods=['GET', 'POST'])
@admin_required
def gebruiker_nieuw():
    form = NieuweGebruikerForm()
    if form.validate_on_submit():
        rol_fout = _valideer_rol_kerngroep(form.rol.data, form.kerngroep.data, form,
                                            roi_kerntaak=(form.roi_kerntaak.data or None))
        if rol_fout:
            flash(rol_fout, 'danger')
        elif User.query.filter_by(email=form.email.data.lower().strip()).first():
            flash('Dit e-mailadres is al in gebruik.', 'danger')
        else:
            user = User(
                email=form.email.data.lower().strip(),
                naam=form.naam.data.strip(),
                rol=form.rol.data,
                kerngroep=(form.kerngroep.data or None),
                roi_kerntaak=(form.roi_kerntaak.data or None),
            )
            user.set_wachtwoord(form.wachtwoord.data)
            db.session.add(user)
            db.session.commit()
            flash(f'Gebruiker {user.naam} is aangemaakt.', 'success')
            return redirect(url_for('auth.gebruikers'))

    return render_template('auth/gebruiker_form.html', form=form, titel='Nieuwe gebruiker')


@bp.route('/gebruikers/<int:user_id>/bewerken', methods=['GET', 'POST'])
@admin_required
def gebruiker_bewerken(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Gebruiker niet gevonden.', 'danger')
        return redirect(url_for('auth.gebruikers'))

    form = GebruikerBewerkenForm(obj=user)
    if form.validate_on_submit():
        bestaande = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if bestaande and bestaande.id != user.id:
            flash('Dit e-mailadres is al in gebruik.', 'danger')
        else:
            nieuwe_rol = form.rol.data
            nieuwe_kerngroep = form.kerngroep.data or None
            nieuwe_roi_kerntaak = form.roi_kerntaak.data or None
            nieuw_actief = form.actief.data
            # Guard 1: admin mag zichzelf niet degraderen of deactiveren.
            if user.id == current_user.id:
                if nieuwe_rol != 'admin' or not nieuw_actief:
                    flash('Je kunt je eigen adminrol of actief-status niet aanpassen. '
                          'Vraag een andere admin.', 'danger')
                    return render_template('auth/gebruiker_form.html', form=form,
                                           titel='Gebruiker bewerken')
            # Guard 2: laatste actieve admin moet blijven bestaan.
            als_deze_wordt_gedegradeerd = (user.rol == 'admin'
                                            and (nieuwe_rol != 'admin' or not nieuw_actief))
            if als_deze_wordt_gedegradeerd:
                aantal_admins = User.query.filter_by(rol='admin', actief=True).count()
                if aantal_admins <= 1:
                    flash('Dit is de laatste actieve admin — maak eerst een andere '
                          'admin aan voordat je deze wijzigt.', 'danger')
                    return render_template('auth/gebruiker_form.html', form=form,
                                           titel='Gebruiker bewerken')
            # Guard 3: rol + kerngroep (of ROI-kerntaak) moeten bij elkaar passen.
            rol_fout = _valideer_rol_kerngroep(nieuwe_rol, nieuwe_kerngroep, form,
                                                roi_kerntaak=nieuwe_roi_kerntaak)
            if rol_fout:
                flash(rol_fout, 'danger')
                return render_template('auth/gebruiker_form.html', form=form,
                                       titel='Gebruiker bewerken')

            user.naam = form.naam.data.strip()
            user.email = form.email.data.lower().strip()
            user.rol = nieuwe_rol
            user.kerngroep = nieuwe_kerngroep
            user.roi_kerntaak = nieuwe_roi_kerntaak
            user.actief = nieuw_actief
            if form.nieuw_wachtwoord.data:
                user.set_wachtwoord(form.nieuw_wachtwoord.data)
            db.session.commit()
            flash(f'Gebruiker {user.naam} is bijgewerkt.', 'success')
            return redirect(url_for('auth.gebruikers'))

    return render_template('auth/gebruiker_form.html', form=form, titel='Gebruiker bewerken')
