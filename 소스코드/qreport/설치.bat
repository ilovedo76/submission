@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python이 설치되어 있지 않습니다. python.org에서 3.12 이상을 설치한 뒤 다시 실행하세요.
  if not defined NOINTERACTIVE pause
  exit /b 1
)
python -m venv .venv
if errorlevel 1 (
  echo 가상환경 생성에 실패했습니다.
  if not defined NOINTERACTIVE pause
  exit /b 1
)
.venv\Scripts\pip install -c requirements-lock.txt -e .[dev]
if errorlevel 1 (
  echo 설치에 실패했습니다.
  if not defined NOINTERACTIVE pause
  exit /b 1
)
echo 설치가 완료되었습니다. 실행.bat 으로 시작하세요.
if not defined NOINTERACTIVE pause
