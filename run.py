from app import create_app
import os

app = create_app()

if __name__ == '__main__':
    # Используем порт из переменной окружения или 5000 по умолчанию
    port = int(os.environ.get('PORT', 5000))
    print(f"🚀 Запуск приложения Lumi на порту {port}...")
    app.run(host='0.0.0.0', port=port)