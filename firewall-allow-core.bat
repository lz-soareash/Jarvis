@echo off
rem Roda uma vez, como administrador, para liberar a porta 8100
rem para conexoes da rede local (mobile via Wi-Fi, sem USB).
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Solicitando elevacao de administrador...
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
netsh advfirewall firewall add rule name="JARVIS Core (8100 LAN mobile)" dir=in action=allow protocol=TCP localport=8100 profile=private
echo.
echo Regra de firewall aplicada (porta 8100 liberada na rede local).
pause