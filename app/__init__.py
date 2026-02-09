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
        # Сначала проверяем Railway MySQL URL
        mysql_url = os.getenv('MYSQL_URL')
        
        if mysql_url:
            # Для Railway MySQL
            from urllib.parse import urlparse
            parsed = urlparse(mysql_url)
            conn = mysql.connector.connect(
                host=parsed.hostname,
                user=parsed.username,
                password=parsed.password,
                database=parsed.path[1:],
                port=parsed.port or 3306,
                autocommit=True
            )
            print(f"✓ Подключено к Railway MySQL: {parsed.hostname}")
        else:
            # Для локальной разработки
            conn = mysql.connector.connect(
                host=os.getenv('DB_HOST', 'localhost'),
                user=os.getenv('DB_USER', 'root'),
                password=os.getenv('DB_PASSWORD', ''),
                database=os.getenv('DB_NAME', 'lumi'),
                autocommit=True
            )
            print("✓ Подключено к локальной MySQL")
        return conn
    except Error as e:
        print(f"✗ Ошибка подключения к БД: {e}")
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
    
    # Быстрая проверка БД
    conn = get_db()
    if conn:
        print("✅ БД подключена")
        close_db(conn)
    else:
        print("❌ БД не подключена")
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # Конфигурация базы данных
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
    
    # Проверка подключения к БД при запуске
    with app.app_context():
        conn = get_db()
        if conn:
            print("✓ База данных подключена успешно")
            
            try:
                cursor = conn.cursor()
                cursor.execute("SHOW TABLES LIKE 'users'")
                users_table_exists = cursor.fetchone() is not None
                cursor.close()
                
                if users_table_exists:
                    print("✓ Таблицы БД существуют")
                else:
                    print("⚠ Таблицы БД не найдены")
                    
            except Error as e:
                print(f"⚠ Ошибка проверки таблиц: {e}")
            finally:
                close_db(conn)
        else:
            print("✗ Ошибка подключения к базе данных")
    
    return app


