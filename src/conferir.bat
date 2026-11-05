@echo off
chcp 65001 >nul
title Conferir se o computador esta pronto
cd /d "%~dp0"
rem usa o pacote de portugues que fica junto do conversor, se existir
if exist "%~dp0tessdata\por.traineddata" set "TESSDATA_PREFIX=%~dp0tessdata"
where python >nul 2>&1
if errorlevel 1 goto sempython
python "%~dp0verificar.py"
goto fim
:sempython
echo.
echo  ==============================================================
echo    O Windows nao encontrou o Python.
echo  ==============================================================
echo.
echo    Refaca o PASSO 1 do guia e instale o Python.
echo    Na PRIMEIRA tela do instalador, marque a caixinha
echo    "Add python.exe to PATH" antes de clicar em Install.
echo.
:fim
echo.
pause
