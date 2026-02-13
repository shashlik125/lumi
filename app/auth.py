from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_user, logout_user, login_required, current_user
from app.models import User
from app import get_db, close_db, bcrypt
from mysql.connector import Error

auth = Blueprint('auth', __name__)

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.get_by_username(username)
        if user and user.check_password(password):
            login_user(user)
            flash('Вы успешно вошли в систему!', 'success')
            return redirect(url_for('main.dashboard'))
        else:
            flash('Неверный логин или пароль', 'error')
    
    return render_template('login.html')

@auth.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # Получаем данные из формы регистрации
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        gender = request.form.get('gender')  # Пол пользователя

        # ======== ВАЛИДАЦИЯ ДАННЫХ =========
        if password != confirm_password:
            flash('Пароли не совпадают', 'error')
            return render_template('register.html')

        if len(password) < 8:
            flash('Пароль должен содержать не менее 8 символов', 'error')
            return render_template('register.html')

        if len(username) < 3:
            flash('Логин должен содержать не менее 3 символов', 'error')
            return render_template('register.html')
        
        if gender not in ['male', 'female']:
            flash('Пожалуйста, выберите ваш пол', 'error')
            return render_template('register.html')

        # ======== ПРОВЕРКА, СУЩЕСТВУЕТ ЛИ ПОЛЬЗОВАТЕЛЬ =========
        existing_user = User.get_by_username(username)
        if existing_user:
            flash('Пользователь с таким логином уже существует', 'error')
            return render_template('register.html')

        # ======== СОЗДАНИЕ ПОЛЬЗОВАТЕЛЯ =========
        user = User.create(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            gender=gender
        )
        
        if user:
            # ======== СОЗДАНИЕ DEFOLT CYCLE_SETTINGS ДЛЯ ЖЕНЩИН =========
            if user.gender == 'female':
                conn = get_db()  # Получаем соединение с базой
                try:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO cycle_settings
                        (user_id, cycle_length, period_length, notify_before_period, notify_ovulation)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (user.id, 28, 5, True, True))
                    conn.commit()
                    cursor.close()
                except Error as e:
                    print(f"Database error while creating cycle_settings: {e}")
                finally:
                    close_db(conn)  # Закрываем соединение

            # ======== ВХОД ПОЛЬЗОВАТЕЛЯ =========
            login_user(user)
            flash('Регистрация прошла успешно!', 'success')
            return redirect(url_for('main.dashboard'))
        else:
            flash('Ошибка при создании пользователя', 'error')

    return render_template('register.html')

@auth.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('main.index'))