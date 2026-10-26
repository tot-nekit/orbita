@echo off
cd /d "%~dp0"
py -3 -m pip install -r requirements.txt
if errorlevel 1 goto error
py -3 run.py all
if errorlevel 1 goto error
goto end
:error
echo Install Python 3.11+ and check the error above.
pause
:end
