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
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        username = request.form.get('username')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        gender = request.form.get('gender')  # Получаем пол из формы

        if password != confirm_password:
            flash('Пароли не совпадают', 'error')
            return render_template('register.html')

        if len(password) < 8:
            flash('Пароль должен содержать не менее 8 символов', 'error')
            return render_template('register.html')

        if len(username) < 3:
            flash('Логин должен содержать не менее 3 символов', 'error')
            return render_template('register.html')
        
        # Проверяем, что пол был выбран
        if gender not in ['male', 'female']:
            flash('Пожалуйста, выберите ваш пол', 'error')
            return render_template('register.html')

        existing_user = User.get_by_username(username)
        if existing_user:
            flash('Пользователь с таким логином уже существует', 'error')
            return render_template('register.html')

        # Создаем пользователя с передачей пола
        # Используем именованные аргументы для надежности
        user = User.create(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            gender=gender
        )
        
        if user:
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