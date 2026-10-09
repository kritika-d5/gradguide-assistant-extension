@echo off
rem GradGuide Counsellor Assist: one click start for Windows.
rem Sets up the backend the first time, checks the course data, starts the API in its own
rem window, then opens Chrome on the extensions page so the side panel can be loaded.

setlocal
set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "PY=%BACKEND%\.venv\Scripts\python.exe"
set "PORT=8000"

echo.
echo  GradGuide Counsellor Assist
echo  ===========================
echo.

rem An API already running on the port (for example from an earlier run) is reused.
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo  The API is already running on http://localhost:%PORT%
  goto :extension
)

where python >nul 2>nul
if errorlevel 1 (
  echo  Python was not found. Install Python 3.11 or newer from python.org and tick
  echo  "Add python.exe to PATH", then run this file again.
  goto :fail
)

cd /d "%BACKEND%"

if not exist "%PY%" (
  echo  [1/3] Creating the Python environment, first run only...
  python -m venv .venv || goto :fail
) else (
  echo  [1/3] Python environment found
)

echo  [2/3] Installing backend requirements...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail

echo  [3/3] Checking course data...
"%PY%" -m app.catalog || goto :fail

rem Optional AI reading of noisy captions needs one API key in backend\.env (git ignored).
if not exist ".env" copy /y ".env.example" ".env" >nul
findstr /r /c:"^GROQ_API_KEY=..*" /c:"^ANTHROPIC_API_KEY=..*" ".env" >nul
if errorlevel 1 (
  echo.
  echo  AI reading is off: no API key yet. Rules still work. To turn it on, put a free Groq
  echo  key from https://console.groq.com/keys into %BACKEND%\.env
) else (
  echo  AI reading key found in backend\.env
)

start "GradGuide API" cmd /k ""%PY%" -m uvicorn app.api:app --reload --port %PORT%"
echo.
echo  API starting in the "GradGuide API" window: http://localhost:%PORT%/docs
echo  Close that window to stop it.

:extension
echo.
echo  Load the side panel in Chrome (first time only):
echo    1. On the page that opens, turn on Developer mode (top right)
echo    2. Click "Load unpacked" and choose this folder:
echo         %ROOT%extension
echo    3. Pin the GradGuide icon, open a Google Meet call and click it
echo.
echo  After changing extension files, click the reload arrow on its card.
echo.
start "" chrome "chrome://extensions" 2>nul || echo  Open chrome://extensions in Chrome yourself.
echo.
pause
exit /b 0

:fail
echo.
echo  Setup stopped. Read the message above, fix it, then run start-all.bat again.
echo.
pause
exit /b 1
