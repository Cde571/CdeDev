$ErrorActionPreference = 'Stop'

$appDir = $PSScriptRoot
$logsDir = Join-Path $appDir 'logs'
$toolsDir = Join-Path $appDir 'tools'
$nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
$nodeExe = if ($nodeCommand) { $nodeCommand.Source } else { Join-Path $env:ProgramFiles 'nodejs\node.exe' }
$tunnelExe = Join-Path $appDir 'tools\cloudflared.exe'
$desktop = [Environment]::GetFolderPath('Desktop')
$accessCodeFile = Join-Path $appDir '.access-code'

if (-not (Test-Path -LiteralPath $nodeExe)) {
    throw 'Node.js no está instalado. Instala Node.js 20 o superior y vuelve a ejecutar este archivo.'
}

New-Item -ItemType Directory -Force -Path $logsDir, $toolsDir | Out-Null

if (-not (Test-Path -LiteralPath $accessCodeFile)) {
    [guid]::NewGuid().ToString('N').Substring(0, 16) | Set-Content -LiteralPath $accessCodeFile -Encoding ascii
}
$accessCode = (Get-Content -Raw $accessCodeFile).Trim()

if (-not (Test-Path -LiteralPath $tunnelExe)) {
    Write-Host 'Descargando el cliente oficial de Cloudflare Tunnel...'
    $downloadPath = Join-Path $toolsDir 'cloudflared.download'
    Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile $downloadPath
    if ((Get-Item -LiteralPath $downloadPath).Length -lt 1MB) {
        throw 'La descarga de Cloudflare Tunnel no es válida.'
    }
    Move-Item -LiteralPath $downloadPath -Destination $tunnelExe -Force
}

function Test-JsonEndpoint([string]$Uri) {
    try {
        Invoke-RestMethod -Uri $Uri -TimeoutSec 4 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-Endpoint([string]$Uri, [int]$Attempts) {
    for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
        if (Test-JsonEndpoint $Uri) { return $true }
        Start-Sleep -Milliseconds 750
    }
    return $false
}

Write-Host ''
Write-Host 'AURORA AI - REACTIVACION AUTOMATICA' -ForegroundColor Green
Write-Host '-----------------------------------'

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

Write-Host '[1/4] Comprobando Ollama...'
if (-not (Test-JsonEndpoint 'http://127.0.0.1:11434/api/version')) {
    $ollamaCommand = Get-Command ollama.exe -ErrorAction SilentlyContinue
    $ollamaExe = if ($ollamaCommand) { $ollamaCommand.Source } else { Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
    if (-not (Test-Path -LiteralPath $ollamaExe)) {
        throw 'Ollama no está instalado. Instálalo y vuelve a ejecutar este archivo.'
    }
    Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WorkingDirectory $appDir -WindowStyle Hidden
    if (-not (Wait-Endpoint 'http://127.0.0.1:11434/api/version' 30)) {
        throw 'Ollama no pudo iniciarse. Abre Ollama manualmente e inténtalo otra vez.'
    }
}

Write-Host '[2/4] Comprobando la app...'
if (-not (Test-JsonEndpoint 'http://127.0.0.1:4173/api/status')) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Start-Process -FilePath $nodeExe -ArgumentList 'server.js' -WorkingDirectory $appDir -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logsDir "app-$stamp.out.log") `
        -RedirectStandardError (Join-Path $logsDir "app-$stamp.err.log") | Out-Null
    if (-not (Wait-Endpoint 'http://127.0.0.1:4173/api/status' 30)) {
        throw 'La app no pudo iniciarse. Revisa los archivos de la carpeta logs.'
    }
}

Write-Host '[3/4] Creando un enlace público nuevo...'
Get-Process cloudflared -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $tunnelExe } |
    Stop-Process -Force
Start-Sleep -Seconds 1

$tunnelStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$tunnelOut = Join-Path $logsDir "tunnel-$tunnelStamp.out.log"
$tunnelErr = Join-Path $logsDir "tunnel-$tunnelStamp.err.log"
Start-Process -FilePath $tunnelExe `
    -ArgumentList @('tunnel', '--url', 'http://127.0.0.1:4173', '--no-autoupdate') `
    -WorkingDirectory $appDir -WindowStyle Hidden `
    -RedirectStandardOutput $tunnelOut -RedirectStandardError $tunnelErr | Out-Null

$publicUrl = $null
for ($attempt = 0; $attempt -lt 45; $attempt++) {
    Start-Sleep -Seconds 1
    if (Test-Path -LiteralPath $tunnelErr) {
        $logText = Get-Content -Raw $tunnelErr
        $match = [regex]::Match($logText, 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($match.Success) {
            $publicUrl = $match.Value
            break
        }
    }
}

if (-not $publicUrl) {
    throw 'Cloudflare no entregó un enlace nuevo. Comprueba la conexión a Internet.'
}

if (-not (Wait-Endpoint "$publicUrl/api/status" 30)) {
    throw 'El enlace fue creado, pero todavía no responde. Ejecuta este botón nuevamente.'
}

Write-Host '[4/4] Listo.' -ForegroundColor Green
$linkFile = Join-Path $desktop 'ENLACE AURORA AI.txt'
@"
AURORA AI

Enlace: $publicUrl
Clave de administrador: $accessCode

El PC debe permanecer encendido y sin suspender.
Cada persona crea su cuenta, registra su pago y espera tu aprobacion.
Si el enlace deja de funcionar, ejecuta nuevamente REACTIVAR AURORA AI.cmd.
"@ | Set-Content -LiteralPath $linkFile -Encoding utf8

$publicUrl | Set-Clipboard
Start-Process $publicUrl

Write-Host ''
Write-Host "ENLACE: $publicUrl" -ForegroundColor Cyan
Write-Host "CLAVE ADMIN: $accessCode" -ForegroundColor Yellow
Write-Host ''
Write-Host 'El enlace se copió al portapapeles y también quedó guardado en:'
Write-Host $linkFile
Write-Host ''
Read-Host 'Presiona Enter para cerrar esta ventana'
