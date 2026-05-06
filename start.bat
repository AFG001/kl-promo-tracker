@echo off
echo ============================================
echo  KL Electronics Promo Tracker - Startup
echo ============================================

:: Check .env exists
if not exist ".env" (
    echo [WARN] .env not found. Copying from .env.example...
    copy ".env.example" ".env"
    echo [ACTION] .env を開いて ANTHROPIC_API_KEY を設定してください。
    notepad .env
    pause
)

:: Install dependencies
echo [INFO] Installing Python dependencies...
cd backend
pip install -r requirements.txt --quiet

:: Start server
echo [INFO] Starting server at http://localhost:8000 ...
python main.py

pause
