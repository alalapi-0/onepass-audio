@echo off
setlocal
set "ROOT=%~dp0.."
python "%ROOT%\scripts\onepass_cli.py" serve-web --out "%ROOT%\out" --audio-root "%ROOT%\materials" --open-browser
