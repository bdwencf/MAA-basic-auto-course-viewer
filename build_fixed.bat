@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

echo [1/4] Installing build dependencies...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [2/4] Building MaaAutomation.exe...
%PY% -m PyInstaller --noconfirm --clean MaaAutomation.spec
if errorlevel 1 goto :error

echo [3/4] Replacing dist resources atomically...
if exist "dist\resource" rmdir /s /q "dist\resource"
if not exist "dist\config" mkdir "dist\config"
xcopy "resource" "dist\resource" /E /I /Y /Q >nul
if errorlevel 1 goto :error
copy /Y "config\settings.json" "dist\config\settings.json" >nul
if errorlevel 1 goto :error

echo [4/4] Preparing writable directories...
if not exist "dist\runtime" mkdir "dist\runtime"
if not exist "dist\captures" mkdir "dist\captures"

echo.
echo Build complete:
echo   dist\MaaAutomation.exe
echo.
echo Run the EXE, then use:
echo   Connect -> Load Resource -> Run Task
exit /b 0

:error
echo.
echo Build failed. Check the error above.
pause
exit /b 1
