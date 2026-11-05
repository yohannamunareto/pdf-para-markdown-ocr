@echo off
chcp 65001 >nul
title Gerador do Leitor de Documentos
cd /d "%~dp0"
echo.
echo  ==================================================================
echo    GERADOR DO EXECUTAVEL "Leitor de Documentos.exe"
echo  ==================================================================
echo.
echo  Junta o programa, o leitor de texto, o separador de paginas e o
echo  pacote de portugues num unico arquivo .exe para os colegas.
echo  Leva alguns minutos. Nao feche esta janela.
echo.

echo  [1 de 6] Conferindo o Python...
where python >nul 2>&1 || (echo  FALTA o Python. & goto erro)

echo  [2 de 6] Localizando o leitor de texto (Tesseract)...
set "TESSDIR="
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "TESSDIR=%ProgramFiles%\Tesseract-OCR"
if not defined TESSDIR if exist "%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe" set "TESSDIR=%ProgramFiles(x86)%\Tesseract-OCR"
if not defined TESSDIR if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" set "TESSDIR=%LOCALAPPDATA%\Programs\Tesseract-OCR"
if not defined TESSDIR for /f "delims=" %%i in ('where tesseract 2^>nul') do if not defined TESSDIR set "TESSDIR=%%~dpi"
if not defined TESSDIR (echo  Nao achei o Tesseract. & goto erro)
if "%TESSDIR:~-1%"=="\" set "TESSDIR=%TESSDIR:~0,-1%"
echo          %TESSDIR%

echo  [3 de 6] Localizando o separador de paginas (Poppler)...
set "POPPDIR="
for /f "delims=" %%i in ('where pdftoppm 2^>nul') do if not defined POPPDIR set "POPPDIR=%%~dpi"
if not defined POPPDIR if exist "C:\poppler\Library\bin\pdftoppm.exe" set "POPPDIR=C:\poppler\Library\bin"
if not defined POPPDIR for /d %%d in ("%ProgramFiles%\poppler*") do if exist "%%d\Library\bin\pdftoppm.exe" set "POPPDIR=%%d\Library\bin"
if not defined POPPDIR (echo  Nao achei o Poppler. & goto erro)
if "%POPPDIR:~-1%"=="\" set "POPPDIR=%POPPDIR:~0,-1%"
echo          %POPPDIR%

echo  [4 de 6] Conferindo o pacote de portugues...
if not exist "%~dp0tessdata\por.traineddata" (echo  Falta tessdata\por.traineddata nesta pasta. & goto erro)
echo          %~dp0tessdata

echo  [5 de 6] Instalando o empacotador (PyInstaller)...
python -m pip install --quiet --disable-pip-version-check --upgrade pyinstaller pillow
if errorlevel 1 (echo  Falhou. Verifique a conexao com a internet. & goto erro)

copy /y "%~dp0ocr-lote.py" "%~dp0ocr_lote.py" >nul

echo  [6 de 6] Montando o executavel...
python -m PyInstaller --noconfirm --onefile --console ^
  --name "Leitor de Documentos" ^
  --add-data "%TESSDIR%;tesseract" ^
  --add-data "%POPPDIR%;poppler" ^
  --add-data "%~dp0tessdata;tessdata" ^
  --hidden-import ocr_lote ^
  --distpath "%~dp0dist" ^
  --workpath "%~dp0build\trabalho" ^
  --specpath "%~dp0build" ^
  "%~dp0app_exe.py"
if errorlevel 1 (echo  A montagem falhou. & goto erro)

echo.
echo  ==================================================================
echo    PRONTO
echo  ==================================================================
echo.
echo  O executavel esta na pasta "dist":
echo    %~dp0dist\Leitor de Documentos.exe
echo.
echo  Esse unico arquivo e tudo que os colegas precisam: dois cliques,
echo  escolher o PDF e esperar. Nao instalam nada.
echo  O arquivo e grande. Mande por link (Drive, OneDrive), nao por e-mail.
echo.
pause
exit /b 0

:erro
echo.
echo  Algo deu errado. Tire um print desta janela.
echo.
pause
exit /b 1
