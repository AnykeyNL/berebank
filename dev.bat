@echo off
setlocal
set "ROOT=%~dp0"

echo Starting de BereBank dev servers...
echo.

if not exist "%ROOT%backend\.venv\Scripts\python.exe" (
    echo ERROR: Backend venv not found.
    echo Run setup first:
    echo   cd backend
    echo   python -m venv .venv
    echo   .\.venv\Scripts\python -m pip install -r requirements.txt
    exit /b 1
)

start "BereBank Backend" cmd /k "cd /d "%ROOT%backend" && .\.venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload"
start "BereBank Frontend" cmd /k "cd /d "%ROOT%frontend" && npm run dev"

echo Backend:  http://127.0.0.1:8000
echo Frontend: http://localhost:5173
echo.
echo Two terminal windows were opened. Close them to stop the servers.
