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

# Глобальная переменная для пула
mysql_pool = None

def init_db():
    """Инициализация пула соединений"""
    global mysql_pool
    try:
        mysql_url = os.getenv('MYSQL_URL')
        print(f"🔄 Инициализация пула соединений...")
        
        if mysql_url and mysql_url.strip() and mysql_url != 'mysql://':
            from urllib.parse import urlparse
            parsed = urlparse(mysql_url)
            
            hostname = parsed.hostname
            username = parsed.username or 'root'
            password = parsed.password or ''
            database = parsed.path
            if database.startswith('/'):
                database = database[1:]
            if not database:
                database = 'railway'
            port = parsed.port or 3306
            
            # СОЗДАЕМ ПУЛ, А НЕ ОДНО СОЕДИНЕНИЕ
            mysql_pool = mysql.connector.pooling.MySQLConnectionPool(
                pool_name="lumi_pool",
                pool_size=5,
                pool_reset_session=True,
                host=hostname,
                user=username,
                password=password,
                database=database,
                port=port,
                autocommit=True,
                connection_timeout=10
            )
            print(f"✅ Пул соединений создан для {hostname}")
            return True
    except Error as e:
        print(f"❌ Ошибка создания пула: {e}")
        return False

def get_db():
    """Получение соединения из пула"""
    global mysql_pool
    try:
        if mysql_pool is None:
            print("🔄 Пул не инициализирован, создаем...")
            init_db()
            
        if mysql_pool:
            conn = mysql_pool.get_connection()
            print(f"✅ Соединение получено из пула")
            return conn
        else:
            print("❌ Не удалось создать пул соединений")
            return None
    except Error as e:
        print(f"❌ Ошибка получения соединения из пула: {e}")
        return None

def close_db(conn):
    """Возврат соединения в пул"""
    if conn and conn.is_connected():
        try:
            conn.close()
            print("✅ Соединение возвращено в пул")
        except Error as e:
            print(f"⚠️ Ошибка при возврате соединения: {e}")

def create_app():
    """Фабрика приложения Flask"""
    app = Flask(__name__)
    print("🚀 CREATE_APP началась")
    
    # Проверяем переменные окружения
    print("🔄 Проверяем переменные окружения...")
    env_vars = ['MYSQL_URL', 'DB_HOST', 'DB_USER', 'DB_NAME', 'DB_PORT', 'SECRET_KEY']
    for var in env_vars:
        value = os.getenv(var)
        if value:
            print(f"   {var}: {value[:20]}..." if len(str(value)) > 20 else f"   {var}: {value}")
        else:
            print(f"   {var}: НЕ НАЙДЕНА")
    
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # Конфигурация базы данных
    app.config['MYSQL_HOST'] = os.getenv('DB_HOST', 'localhost')
    app.config['MYSQL_USER'] = os.getenv('DB_USER', 'root')
    app.config['MYSQL_PASSWORD'] = os.getenv('DB_PASSWORD', '')
    app.config['MYSQL_DB'] = os.getenv('DB_NAME', 'lumi')
    
    # Инициализация расширений
    bcrypt.init_app(app)
    login_manager.init_app(app)
    
    # Инициализация пула соединений
    with app.app_context():
        init_db()
        print("✅ Пул соединений инициализирован")
    
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