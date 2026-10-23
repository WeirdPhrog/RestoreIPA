<#
Сборка Restore: dist\RestoreAppStore.exe — программа одним файлом, без установщика (PyInstaller).

  powershell -ExecutionPolicy Bypass -File build.ps1

Ставить ничего не нужно. Python 3.15 (официальный пакет python.org на nuget.org — без установщика), ipatool и
Unicorn скачиваются в build\ из официальных источников; сборка останавливается, если не совпал SHA-256.
Всё, что сборка скачивает и создаёт, включая кэши pip и PyInstaller, лежит в build\ и dist\.
#>
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$PythonVersion = "3.15.1"
$PythonUrl = "https://api.nuget.org/v3-flatcontainer/python/$PythonVersion/python.$PythonVersion.nupkg"
$PythonSha = "a53df42a4fb57132896c3d99686c19c7f13d6f126bd603a9a9bc0e9b6ed83d22"  # пакет «python» от Python Software Foundation
$IpatoolUrl = "https://github.com/majd/ipatool/releases/download/v2.7.0/ipatool-2.7.0-windows-amd64.tar.gz"
$IpatoolSha = "b729608716908be287c2e6972f9e8cd05f879810ae5d182fc856c9e57cca0dd5"  # ipatool-2.7.0-windows-amd64.tar.gz.sha256sum
$UnicornUrl = "https://github.com/unicorn-engine/unicorn/releases/download/2.1.4/windows-mingw64-shared.7z"
$UnicornSha = "0960f938e66fa12c448742bddd2a03aa88abeeb2b3cda7156493a2da86228d3a"  # digest ассета в релизе 2.1.4
$UnicornDllSha = "d8f9a89222ffa74493a1d47090e17f8e1db8ac171a3128c6a76a4ea09de11469"  # = UNICORN_SHA256 в restore_gui/apple.py
$UnicornLicenseUrl = "https://raw.githubusercontent.com/unicorn-engine/unicorn/2.1.4/COPYING"
$Tar = "$env:SystemRoot\System32\tar.exe"  # bsdtar из Windows: .tar.gz, .zip (nupkg), а в Windows 11 — и .7z

function Assert-Hash($path, $sha) {
    $h = (Get-FileHash $path -Algorithm SHA256).Hash
    if ($h -ne $sha) { throw "SHA-256 не совпал: $path`n  ожидался $sha`n  получен  $h" }
}

function Get-Checked($url, $out, $sha) {
    if (-not (Test-Path $out) -or (Get-FileHash $out -Algorithm SHA256).Hash -ne $sha) {
        Write-Host "Скачиваю $url"
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest $url -OutFile $out
    }
    Assert-Hash $out $sha
}

function Invoke-Native {
    & $args[0] @($args | Select-Object -Skip 1)
    if ($LASTEXITCODE) { throw "Команда завершилась с кодом ${LASTEXITCODE}: $($args -join ' ')" }
}

function Get-PyVersion($exe) {
    if (-not (Test-Path $exe)) { return $null }
    try { return (& $exe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])") } catch { return $null }
}

function New-EmptyDir($path) {
    Remove-Item -Recurse -Force $path -ErrorAction SilentlyContinue
    New-Item -ItemType Directory $path | Out-Null
}

# кэши pip и PyInstaller — тоже в build\, а не в %LOCALAPPDATA%
$env:PIP_CACHE_DIR = Join-Path $PWD "build\cache\pip"
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PWD "build\cache\pyinstaller"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
New-Item -ItemType Directory -Force build\dl, build\res | Out-Null

# --- окружение сборки ---
$base = "build\python\tools\python.exe"
$py = "build\venv\Scripts\python.exe"
if ((Get-PyVersion $base) -ne $PythonVersion) {
    Get-Checked $PythonUrl build\dl\python.nupkg $PythonSha
    New-EmptyDir build\python
    Invoke-Native $Tar -xf build\dl\python.nupkg -C build\python tools
}
if ((Get-PyVersion $py) -ne $PythonVersion) {
    Write-Host "Создаю build\venv (Python $PythonVersion)"
    Remove-Item -Recurse -Force build\venv -ErrorAction SilentlyContinue  # в том числе прежний, на Python 3.12
    Invoke-Native $base -m venv build\venv
}
# requirements-build.txt — уже полный список с точными версиями (см. requirements-build.in), отсюда --no-deps
Invoke-Native $py -m pip install -q --no-deps --only-binary=:all: -r requirements-build.txt

# --- внешние компоненты ---
Get-Checked $IpatoolUrl build\dl\ipatool.tar.gz $IpatoolSha
New-EmptyDir build\dl\ipatool
Invoke-Native $Tar -xzf build\dl\ipatool.tar.gz -C build\dl\ipatool
$ipatoolExe = Get-ChildItem build\dl\ipatool -Recurse -Filter "ipatool*.exe" | Select-Object -First 1
if (-not $ipatoolExe) { throw "В архиве ipatool нет exe" }
Copy-Item $ipatoolExe.FullName build\res\ipatool.exe -Force

Get-Checked $UnicornUrl build\dl\unicorn.7z $UnicornSha
New-EmptyDir build\dl\unicorn
& $Tar -xf build\dl\unicorn.7z -C build\dl\unicorn
if ($LASTEXITCODE) {  # tar в Windows 10 не читает .7z — тогда 7-Zip, если он есть
    $sevenZip = @((Get-Command 7z -ErrorAction SilentlyContinue).Source, "$env:ProgramFiles\7-Zip\7z.exe") |
        Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $sevenZip) { throw "Не удалось распаковать build\dl\unicorn.7z: нужна Windows 11 или 7-Zip (winget install 7zip.7zip)" }
    Invoke-Native $sevenZip x -y -obuild\dl\unicorn build\dl\unicorn.7z | Out-Null
}
$dll = Get-ChildItem build\dl\unicorn -Recurse -Filter "libunicorn.dll" | Select-Object -First 1
if (-not $dll) { throw "В архиве Unicorn нет libunicorn.dll" }
Assert-Hash $dll.FullName $UnicornDllSha
Copy-Item $dll.FullName build\res\libunicorn.dll -Force
if (-not (Test-Path build\res\UNICORN_COPYING.txt)) {
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest $UnicornLicenseUrl -OutFile build\res\UNICORN_COPYING.txt
}

# --- тесты: сломанную логику не собираем ---
Write-Host "Тесты"
Invoke-Native $py -m unittest discover -s tests -q

# --- exe (см. RestoreAppStore.spec) ---
if (-not (Test-Path assets\icon.ico)) { Invoke-Native $py tools\make_icon.py }
$version = (& $py tools\write_version_info.py build\version_info.txt).Trim()
if ($LASTEXITCODE) { throw "Не удалось записать версию" }
Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue  # в dist\ — только результат этой сборки
Write-Host "Собираю RestoreAppStore $version"
Invoke-Native build\venv\Scripts\pyinstaller.exe RestoreAppStore.spec --noconfirm --log-level WARN --workpath build\work --distpath dist

# собранный интерфейс загружается без ошибок (иначе из Qt вырезано что-то нужное — см. DROP в spec)
$exe = "dist\RestoreAppStore.exe"
$report = Join-Path $PWD "build\check-ui.txt"
$p = Start-Process $exe -ArgumentList "--check-ui", "`"$report`"" -Wait -PassThru -WindowStyle Hidden
if ($p.ExitCode -ne 0) { throw "Интерфейс в $exe не загрузился:`n" + (Get-Content $report -Raw -ErrorAction SilentlyContinue) }
Write-Host "Интерфейс загружается: $exe ($([math]::Round((Get-Item $exe).Length / 1MB)) МБ)"

# контрольная сумма — для страницы релиза. Перевод строки LF: с CRLF (как пишет Set-Content) `sha256sum -c`
# в Git Bash и Linux принимает «\r» за часть имени файла и пишет, что файл не найден
$sum = "{0}  {1}" -f (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower(), (Split-Path $exe -Leaf)
[IO.File]::WriteAllText((Join-Path $PWD "dist\SHA256SUMS.txt"), "$sum`n", [Text.Encoding]::ASCII)
Write-Host $sum
