<#
  Podcast Foundry - turn email delivery on.

  Asks for the Gmail address and the 16-character Google app password, then
  writes EMAIL_METHOD / SMTP_USER / SMTP_PASS into the .env file next to this
  script. The password is typed masked, is never echoed back, never written to
  a log or a ticket, and never leaves this machine. .env is gitignored.

  Normal use: double-click setup_email.bat.
  Check what is configured without changing anything:
      powershell -ExecutionPolicy Bypass -File setup_email.ps1 -Check
#>
param(
    [string]$AppDir = $PSScriptRoot,
    [switch]$Check,
    [switch]$SelfTest
)

$Keys = @('EMAIL_METHOD', 'SMTP_USER', 'SMTP_PASS')

function Merge-EnvLines {
    param([string[]]$Existing, [hashtable]$Values)

    $lines = New-Object System.Collections.Generic.List[string]
    if ($Existing) { foreach ($line in $Existing) { $lines.Add($line) } }

    foreach ($key in @('EMAIL_METHOD', 'SMTP_USER', 'SMTP_PASS')) {
        $replaced = $false
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match "^\s*#?\s*$key\s*=") {
                $lines[$i] = "$key=$($Values[$key])"
                $replaced = $true
                break
            }
        }
        if (-not $replaced) { $lines.Add("$key=$($Values[$key])") }
    }
    return $lines.ToArray()
}

function Get-EnvStatus {
    param([string]$Path)

    $status = [ordered]@{}
    foreach ($key in $Keys) { $status[$key] = 'missing' }
    if (-not (Test-Path -LiteralPath $Path)) { return $status }

    foreach ($line in (Get-Content -LiteralPath $Path)) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if ($Keys -contains $name) {
                $status[$name] = if ($value) { 'set' } else { 'empty' }
            }
        }
    }
    return $status
}

function Write-EnvFile {
    param([string]$Path, [string[]]$Lines)
    # UTF-8 with no BOM: a BOM would corrupt the first variable name for
    # python-dotenv.
    [System.IO.File]::WriteAllLines($Path, $Lines, (New-Object System.Text.UTF8Encoding $false))
}

function Show-Status {
    param([string]$Path)
    $status = Get-EnvStatus -Path $Path
    Write-Host ''
    Write-Host "  $Path"
    foreach ($key in $Keys) {
        Write-Host ("    {0,-13} {1}" -f $key, $status[$key])
    }
    Write-Host ''
    if (@($Keys | Where-Object { $status[$_] -ne 'set' }).Count -eq 0) {
        Write-Host '  Email delivery is configured.' -ForegroundColor Green
        return $true
    }
    Write-Host '  Email delivery is still paused - run setup_email.bat to turn it on.' -ForegroundColor Yellow
    return $false
}

# --- self-test (used by the agent team to verify this script, not by the Board)
if ($SelfTest) {
    $failures = 0
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("pfse_" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    try {
        $values = @{ EMAIL_METHOD = 'smtp'; SMTP_USER = 'someone@example.com'; SMTP_PASS = 'abcdefghijklmnop' }

        # fresh file
        $fresh = Merge-EnvLines -Existing @() -Values $values
        $p1 = Join-Path $tmp 'fresh.env'
        Write-EnvFile -Path $p1 -Lines $fresh
        $s1 = Get-EnvStatus -Path $p1
        foreach ($k in $Keys) {
            if ($s1[$k] -ne 'set') { Write-Host "FAIL fresh: $k = $($s1[$k])"; $failures++ }
        }

        # existing file: empty key, commented key, unrelated key preserved
        $existing = @('# comment', 'OUTPUT_FOLDER=C:\somewhere', 'EMAIL_METHOD=', '# SMTP_USER=')
        $merged = Merge-EnvLines -Existing $existing -Values $values
        $p2 = Join-Path $tmp 'existing.env'
        Write-EnvFile -Path $p2 -Lines $merged
        $s2 = Get-EnvStatus -Path $p2
        foreach ($k in $Keys) {
            if ($s2[$k] -ne 'set') { Write-Host "FAIL existing: $k = $($s2[$k])"; $failures++ }
        }
        $text = Get-Content -LiteralPath $p2 -Raw
        if ($text -notmatch 'OUTPUT_FOLDER=C:\\somewhere') { Write-Host 'FAIL: unrelated key lost'; $failures++ }
        if ($text -match '(?m)^\s*#\s*SMTP_USER') { Write-Host 'FAIL: commented SMTP_USER not replaced'; $failures++ }
        if (@([regex]::Matches($text, '(?m)^EMAIL_METHOD=')).Count -ne 1) { Write-Host 'FAIL: EMAIL_METHOD duplicated'; $failures++ }
        if ($text.Substring(0, 1) -eq [char]0xFEFF) { Write-Host 'FAIL: BOM written'; $failures++ }

        # missing file reports missing, not an error
        $s3 = Get-EnvStatus -Path (Join-Path $tmp 'nope.env')
        foreach ($k in $Keys) {
            if ($s3[$k] -ne 'missing') { Write-Host "FAIL missing-file: $k = $($s3[$k])"; $failures++ }
        }
    }
    finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($failures -eq 0) { Write-Host 'self-test OK' } else { Write-Host "self-test FAILED ($failures)" }
    exit $(if ($failures -eq 0) { 0 } else { 1 })
}

$envPath = Join-Path $AppDir '.env'

if ($Check) {
    Show-Status -Path $envPath | Out-Null
    exit 0
}

Write-Host ''
Write-Host '  Podcast Foundry - turn email delivery on' -ForegroundColor Cyan
Write-Host '  -----------------------------------------'
Write-Host '  You need a Google app password first (not your normal password):'
Write-Host '    1. 2-Step Verification On   https://myaccount.google.com/security'
Write-Host '    2. Create one named "Podcast Foundry"'
Write-Host '       https://myaccount.google.com/apppasswords'
Write-Host '  Google shows 16 characters once. Have them ready, then continue.'
Write-Host ''

$user = (Read-Host '  Gmail address episodes are sent FROM').Trim()
if (-not $user) { Write-Host '  Nothing entered - no changes made.' -ForegroundColor Yellow; exit 1 }
if ($user -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
    Write-Host "  '$user' does not look like an email address - no changes made." -ForegroundColor Yellow
    exit 1
}

$secure = Read-Host '  16-character app password (hidden as you type)' -AsSecureString
$plain = [System.Net.NetworkCredential]::new('', $secure).Password
$plain = $plain -replace '\s', ''

if (-not $plain) { Write-Host '  Nothing entered - no changes made.' -ForegroundColor Yellow; exit 1 }
if ($plain.Length -ne 16) {
    Write-Host ''
    Write-Host "  Warning: that is $($plain.Length) characters, not 16." -ForegroundColor Yellow
    Write-Host '  Google app passwords are always 16. Your normal account password will not work.'
    $answer = Read-Host '  Write it anyway? (y/N)'
    if ($answer -notmatch '^[Yy]') { Write-Host '  No changes made.'; exit 1 }
}

$existing = if (Test-Path -LiteralPath $envPath) { Get-Content -LiteralPath $envPath } else { @() }
$lines = Merge-EnvLines -Existing $existing -Values @{
    EMAIL_METHOD = 'smtp'
    SMTP_USER    = $user
    SMTP_PASS    = $plain
}
Write-EnvFile -Path $envPath -Lines $lines

$plain = $null
$secure.Dispose()
[System.GC]::Collect()

Write-Host ''
Write-Host '  Written. The password is in .env only - not shown above, not logged,' -ForegroundColor Green
Write-Host '  not committed (.gitignore covers .env).' -ForegroundColor Green
Show-Status -Path $envPath | Out-Null
Write-Host '  Next: say so on ticket POD-7 and the team runs one real test email.'
Write-Host ''
