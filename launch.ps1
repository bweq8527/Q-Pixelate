$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonProgram = Get-Command pythonw -ErrorAction Stop
& $pythonProgram.Source -m q_pixelate @args
