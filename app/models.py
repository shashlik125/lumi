from flask_login import UserMixin
from app import get_db, close_db, bcrypt
from mysql.connector import Error

class User(UserMixin):
    def __init__(self, id, username, email, password, first_name=None, last_name=None, avatar_path=None):
        self.id = id
        self.username = username
        self.email = email
        self.password = password
        self.first_name = first_name
        self.last_name = last_name
        self.avatar_path = avatar_path
    
    @staticmethod
    def get_by_id(user_id):
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
                    avatar_path=user_data.get('avatar_path')
                )
            return None
        except Error as e:
            print(f"Database error in get_by_id: {e}")
            return None
        finally:
            close_db(conn)
    
    @staticmethod
    def get_by_username(username):
        conn = get_db()
        if conn is None:
            return None
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
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
                    avatar_path=user_data.get('avatar_path')
                )
            return None  # Этот return должен быть внутри блока if, но вне try-except
        except Error as e:
            print(f"Database error in get_by_username: {e}")
            return None
        finally:
            close_db(conn)
    
    @staticmethod
    def create(username, password, first_name=None, last_name=None, email=None):
        conn = get_db()
        if conn is None:
            return None
            
        try:
            hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
            cursor = conn.cursor()
            
            cursor.execute(
                "INSERT INTO users (username, password, first_name, last_name, email) VALUES (%s, %s, %s, %s, %s)",
                (username, hashed_password, first_name, last_name, email)
            )
            
            user_id = cursor.lastrowid
            cursor.close()
            
            return User(user_id, username, email, hashed_password, first_name, last_name)
        except Error as e:
            print(f"Database error in create: {e}")
            return None
        finally:
            close_db(conn)
    
    def check_password(self, password):
        return bcrypt.check_password_hash(self.password, password)