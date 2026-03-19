@echo off
REM Script batch para Windows - Inicia o sistema completo da Iris
echo ================================================================================
echo SISTEMA IRIS - INICIANDO
echo ================================================================================
echo.
echo Iniciando todos os servicos...
echo.

REM Ativa ambiente virtual se existir
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Executa o script principal Python
python main.py

pause
