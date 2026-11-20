param([Parameter(Mandatory=$true)][string]$Bootstrapper)
$ErrorActionPreference = 'Stop'
if ($env:CIRCLE_BRANCH -ne 'magnetapi/coderx-01710-circle-win-20261120') { throw 'Wrong build branch' }
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Free Windows VM requires native local admin to install verified build dependencies'
}
$signature = Get-AuthenticodeSignature -LiteralPath $Bootstrapper
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)CN=Microsoft Corporation(,|$)') {
    throw 'Microsoft Authenticode signature verification failed'
}
$installLocation = Join-Path ([IO.Path]::GetPathRoot($Bootstrapper)) 'CoderX-VS'
if (Test-Path $installLocation) { throw 'Expected a disposable empty build tool destination' }
$args = @('--quiet', '--wait', '--norestart', '--nocache', '--installPath', $installLocation,
    '--add', 'Microsoft.VisualStudio.Workload.VCTools',
    '--add', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
    '--add', 'Microsoft.VisualStudio.Component.Windows11SDK.26100')
$setup = Start-Process -FilePath $Bootstrapper -ArgumentList $args -PassThru
if (-not $setup.WaitForExit(12*60*1000)) {
    try { $setup.Kill($true) } finally { throw 'Build tool setup exceeded the 12-minute CI allowance' }
}
if ($setup.ExitCode -notin @(0,3010)) { throw "Build tool installer exit code $($setup.ExitCode)" }
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$selected = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $selected) { throw 'Visual Studio x64 C++ tools are unavailable after installation' }
Write-Output 'Microsoft-signed x64 build tools and SDK installed on disposable Free VM'
