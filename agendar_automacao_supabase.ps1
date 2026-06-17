param(
    [int]$IntervaloMinutos = 30,
    [string]$TaskName = "Construtec Leads Supabase",
    [switch]$ColetarLinks,
    [switch]$ColetarGoogleMaps
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatPath = Join-Path $ScriptDir "rodar_automacao_supabase.bat"
$Argumentos = ""

if ($ColetarGoogleMaps) {
    $BatPath = Join-Path $ScriptDir "ATUALIZAR_LEADS_COMPLETO.bat"
}

if ($ColetarLinks) {
    $Argumentos = "--coletar-links"
}

$Action = New-ScheduledTaskAction -Execute $BatPath -Argument $Argumentos -WorkingDirectory $ScriptDir
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervaloMinutos) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Atualiza CSV de leads e sincroniza com Supabase periodicamente." `
    -Force

if ($ColetarGoogleMaps) {
    Write-Host "Tarefa agendada: $TaskName a cada $IntervaloMinutos minutos com coleta Google Maps limitada a 50 novos leads por execucao."
} else {
    Write-Host "Tarefa agendada: $TaskName a cada $IntervaloMinutos minutos."
}
