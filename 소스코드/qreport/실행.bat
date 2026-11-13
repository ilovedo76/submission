@echo off
cd /d "%~dp0"
.venv\Scripts\streamlit run src\qreport\app\main.py
if not defined NOINTERACTIVE pause
