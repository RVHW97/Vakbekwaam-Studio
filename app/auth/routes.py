from functools import wraps
from urllib.parse import urlparse
from flask import render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
from app import db
from app.auth import bp
from app.auth.forms import LoginForm, NieuweGebruikerForm, GebruikerBewerkenForm
from app.models import User

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
            flash('Je hebt geen toegang tot deze pagina.', 'danger')
            return redirect(url_for('main.dashboard'))
        return f(*args, **kwargs)
    return decorated_function


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user and user.actief and user.check_wachtwoord(form.wachtwoord.data):
            login_user(user, remember=form.onthoud_mij.data)
            next_page = _veilige_next_url(request.args.get('next'))
            return redirect(next_page or url_for('main.dashboard'))
        # Betaal altijd de hash-tijd, ook bij onbekende gebruiker of gedeactiveerd account.
        check_password_hash(_DUMMY_HASH, form.wachtwoord.data)
        flash('Ongeldige inloggegevens of account is gedeactiveerd.', 'danger')

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
        if User.query.filter_by(email=form.email.data.lower().strip()).first():
            flash('Dit e-mailadres is al in gebruik.', 'danger')
        else:
            user = User(
                email=form.email.data.lower().strip(),
                naam=form.naam.data.strip(),
                rol=form.rol.data
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

            user.naam = form.naam.data.strip()
            user.email = form.email.data.lower().strip()
            user.rol = nieuwe_rol
            user.actief = nieuw_actief
            if form.nieuw_wachtwoord.data:
                user.set_wachtwoord(form.nieuw_wachtwoord.data)
            db.session.commit()
            flash(f'Gebruiker {user.naam} is bijgewerkt.', 'success')
            return redirect(url_for('auth.gebruikers'))

    return render_template('auth/gebruiker_form.html', form=form, titel='Gebruiker bewerken')
