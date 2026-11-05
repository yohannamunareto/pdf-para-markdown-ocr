@echo off
chcp 65001 >nul
title Reconhecer texto de um PDF
cd /d "%~dp0"
rem usa o pacote de portugues que fica junto do conversor, se existir
if exist "%~dp0tessdata\por.traineddata" set "TESSDATA_PREFIX=%~dp0tessdata"

rem arquivo arrastado para cima do icone, ou escolhido na janela
set "PDF=%~1"
if not "%PDF%"=="" goto temarquivo
echo.
echo  Escolha o PDF na janela que vai abrir...
for /f "usebackq delims=" %%F in (`powershell -NoProfile -STA -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Add-Type -AssemblyName System.Windows.Forms; $d=New-Object System.Windows.Forms.OpenFileDialog; $d.Filter='PDF (*.pdf)|*.pdf'; $d.Title='Escolha o PDF para ler'; if($d.ShowDialog() -eq 'OK'){$d.FileName}"`) do set "PDF=%%F"
if "%PDF%"=="" goto semarquivo

:temarquivo
for %%A in ("%PDF%") do set "SAIDA=%%~dpnA - OCR.md"
for %%A in ("%PDF%") do set "NOME=%%~nxA"
echo.
echo  Lendo: %NOME%
echo  Isso pode demorar alguns minutos. Nao feche esta janela.
echo  Se fechar sem querer, rode de novo: o que ja foi lido fica guardado.
echo.
python "%~dp0ocr-lote.py" "%PDF%" -o "%SAIDA%" --retomar
if errorlevel 1 goto deuerro
echo.
echo  PRONTO. O resultado foi salvo na mesma pasta do PDF:
echo  %SAIDA%
start "" "%SAIDA%"
goto fim

:deuerro
echo.
echo  Algo deu errado. Tire um print desta janela.
echo  Para conferir a instalacao, de dois cliques em "conferir.bat".
goto fim

:semarquivo
echo.
echo  Nenhum PDF foi escolhido.
echo.

:fim
echo.
pause
