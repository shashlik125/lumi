from flask import Flask
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
import mysql.connector
from mysql.connector import Error
import os
from dotenv import load_dotenv

load_dotenv()

# Инициализация расширений
bcrypt = Bcrypt()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'

def get_db():
    """Получение нового соединения с базой данных"""
    try:
        mysql_url = os.getenv('MYSQL_URL')
        print(f"🔄 DEBUG: MYSQL_URL = {mysql_url}")
        
        if mysql_url and mysql_url.strip() and mysql_url != 'mysql://':
            print("✅ DEBUG: MYSQL_URL найден, парсим...")
            
            from urllib.parse import urlparse
            parsed = urlparse(mysql_url)
            
            print(f"🔄 DEBUG parsed: scheme={parsed.scheme}, hostname={parsed.hostname}, username={parsed.username}, path={parsed.path}, port={parsed.port}")
            
            hostname = parsed.hostname
            username = parsed.username or 'root'
            password = parsed.password or ''
            
            database = parsed.path
            if database.startswith('/'):
                database = database[1:]
            if not database:
                database = 'railway'
                
            port = parsed.port or 3306
            
            print(f"🔄 DEBUG подключение: host={hostname}, user={username}, db={database}, port={port}")
            
            # ✅ ГЛАВНОЕ ИЗМЕНЕНИЕ - ДОБАВЛЕН ТАЙМАУТ!
            conn = mysql.connector.connect(
                host=hostname,
                user=username,
                password=password,
                database=database,
                port=port,
                autocommit=True,
                connection_timeout=5,  # 👈 5 секунд таймаут!
                pool_size=1
            )
            print(f"✅ Подключено к Railway MySQL: {hostname}")
            return conn
            
        else:
            print("⚠ DEBUG: MYSQL_URL не найден, проверяем отдельные переменные...")
            
            db_host = os.getenv('DB_HOST')
            db_user = os.getenv('DB_USER')
            db_password = os.getenv('DB_PASSWORD')
            db_name = os.getenv('DB_NAME')
            db_port = os.getenv('DB_PORT', 3306)
            
            print(f"🔄 DEBUG: DB_HOST={db_host}, DB_USER={db_user}, DB_NAME={db_name}, DB_PORT={db_port}")
            
            if db_host:
                conn = mysql.connector.connect(
                    host=db_host,
                    user=db_user or 'root',
                    password=db_password or '',
                    database=db_name or 'railway',
                    port=int(db_port),
                    autocommit=True,
                    connection_timeout=5  # 👈 И ЗДЕСЬ ТОЖЕ ТАЙМАУТ!
                )
                print(f"✅ Подключено к MySQL: {db_host}")
                return conn
            else:
                print("❌ DEBUG: Не найдены параметры подключения к БД")
                return None
                
    except Error as e:
        print(f"❌ Ошибка подключения к БД: {e}")
        print(f"❌ Подробности ошибки: {e.msg}")
        return None

def close_db(conn):
    """Закрытие соединения с базой данных"""
    if conn and conn.is_connected():
        try:
            conn.close()
        except Error as e:
            print(f"Ошибка закрытия соединения: {e}")

def create_app():
    """Фабрика приложения Flask"""
    app = Flask(__name__)
    print("🚀 CREATE_APP началась")
    
    # Проверяем все переменные окружения (для отладки)
    print("🔄 Проверяем переменные окружения...")
    env_vars = ['MYSQL_URL', 'DB_HOST', 'DB_USER', 'DB_NAME', 'DB_PORT']
    for var in env_vars:
        value = os.getenv(var)
        if value:
            print(f"   {var}: {value[:20]}..." if len(str(value)) > 20 else f"   {var}: {value}")
        else:
            print(f"   {var}: НЕ НАЙДЕНА")
    
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # Конфигурация базы данных (для совместимости)
    app.config['MYSQL_HOST'] = os.getenv('DB_HOST', 'localhost')
    app.config['MYSQL_USER'] = os.getenv('DB_USER', 'root')
    app.config['MYSQL_PASSWORD'] = os.getenv('DB_PASSWORD', '')
    app.config['MYSQL_DB'] = os.getenv('DB_NAME', 'lumi')
    
    # Инициализация расширений
    bcrypt.init_app(app)
    login_manager.init_app(app)
    
    from app.models import User
    
    @login_manager.user_loader
    def load_user(user_id):
        print(f"👤 LOAD_USER вызвана для user_id: {user_id}")
        conn = get_db()
        if conn is None:
            return None
            
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM users WHERE id = %s", (int(user_id),))
            user_data = cursor.fetchone()
            cursor.close()
            
            if user_data:
                return User(
                    id=user_data['id'],
                    username=user_data['username'],
                    email=user_data.get('email'),
                    password=user_data['password'],
                    first_name=user_data.get('first_name'),
                    last_name=user_data.get('last_name'),
                    avatar_path=user_data.get('avatar_path'),
                    gender=user_data.get('gender')
                )
            return None
        except Error as e:
            print(f"Ошибка загрузки пользователя: {e}")
            return None
        finally:
            close_db(conn)
    
    # Регистрация blueprint'ов
    from app.auth import auth as auth_blueprint
    from app.routes import main as main_blueprint
    
    app.register_blueprint(auth_blueprint, url_prefix='/auth')
    app.register_blueprint(main_blueprint)
    
    print("✅ Приложение Lumi инициализировано")
    return app