@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\classmate-test.ps1" -Action start %*
exit /b %ERRORLEVEL%
