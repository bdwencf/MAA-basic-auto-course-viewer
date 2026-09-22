@echo off
setlocal
cd /d "%~dp0"

echo Syncing config/resource into dist...
if exist "dist\resource" rmdir /s /q "dist\resource"
if not exist "dist\config" mkdir "dist\config"
xcopy "resource" "dist\resource" /E /I /Y /Q >nul
if errorlevel 1 goto :error
copy /Y "config\settings.json" "dist\config\settings.json" >nul
if errorlevel 1 goto :error

echo Sync complete.
exit /b 0

:error
echo Sync failed.
pause
exit /b 1
