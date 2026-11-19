@echo off
title Mon organisateur
cd /d "%~dp0"
echo.
echo   Mon organisateur
echo   ----------------
where python >nul 2>nul
if errorlevel 1 (
  echo   Python n'est pas installe.
  echo   Installe-le depuis https://www.python.org/downloads/
  echo   en cochant "Add Python to PATH", puis relance ce fichier.
  start "" https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo   Premiere utilisation : installation en cours, 1 a 2 minutes...
  python -m venv .venv
  if errorlevel 1 (
    echo   Echec de la creation de l'environnement Python.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 (
    echo   Echec de l'installation. Verifie ta connexion Internet et relance ce fichier.
    rmdir /s /q .venv
    pause
    exit /b 1
  )
)
echo.
echo   L'application s'ouvre dans ton navigateur : http://localhost:8001
echo   Pour l'arreter, ferme cette fenetre.
echo.
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8001"
".venv\Scripts\python.exe" -m uvicorn app.main:app --port 8001
pause
