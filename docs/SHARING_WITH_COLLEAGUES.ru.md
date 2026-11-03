# Как передать DepLoom MCP коллегам

[English](SHARING_WITH_COLLEAGUES.md) · [Руководство](../USERGUIDE.ru.md)

## Self-hosted MCP (stdio)

Самая простая проба: отправить ссылку на релиз и руководство. Каждый коллега устанавливает MCP локально и прописывает собственный Python-путь и скилл. Wheel включает закреплённый Core. Ваш компьютер после этого может быть выключен.

Также можно подготовить ZIP с публичным wheel, контрольными суммами, Python-зависимостями и инструкцией. Не копируйте свой venv, конфиг агента, credentials, state MCP или evidence проектов. Windows venv не является переносимым пакетом.

### Подготовьте автономный комплект на Windows

Используйте Python версии, ОС и архитектуры CPU получателя: зависимые wheel могут быть платформенными. Выполняйте команды в отдельном пустом рабочем каталоге:

```powershell
$bundle = Join-Path $PWD 'deploom-trial'
New-Item -ItemType Directory -Force "$bundle\wheelhouse" | Out-Null
$wheel = Join-Path $bundle 'deploom_mcp-0.2.2-py3-none-any.whl'
Invoke-WebRequest 'https://github.com/DepLoom/mcp/releases/download/v0.2.2/deploom_mcp-0.2.2-py3-none-any.whl' -OutFile $wheel
Invoke-WebRequest 'https://github.com/DepLoom/mcp/releases/download/v0.2.2/SHA256SUMS.txt' -OutFile "$bundle\SHA256SUMS.txt"
$line = Get-Content "$bundle\SHA256SUMS.txt" | Where-Object { $_ -match 'deploom_mcp-0\.2\.2-py3-none-any\.whl$' }
$expected = ($line -split '\s+')[0]
$actual = (Get-FileHash $wheel -Algorithm SHA256).Hash.ToLowerInvariant()
if (-not $expected -or $actual -ne $expected.ToLowerInvariant()) { throw 'Release checksum mismatch' }
python -m pip download --only-binary=:all: --dest "$bundle\wheelhouse" $wheel
if ($LASTEXITCODE -ne 0) { throw 'Incomplete wheelhouse; do not distribute' }
Get-ChildItem "$bundle\wheelhouse" -File | Get-FileHash -Algorithm SHA256 |
  Select-Object @{n='File';e={Split-Path $_.Path -Leaf}}, Hash |
  Export-Csv "$bundle\wheelhouse-sha256.csv" -NoTypeInformation -Encoding UTF8
```

Используйте разрешённый Python index/proxy, не добавляя credentials в комплект. Положите копию руководства и упакуйте только эту папку. CSV описывает скачанные зависимости, но не заменяет независимую подпись издателя. Доставку проверяйте обычным доверенным каналом.

### Установка на компьютере коллеги

После распаковки откройте PowerShell в каталоге комплекта:

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --no-index --find-links .\wheelhouse deploom-mcp==0.2.2
if ($LASTEXITCODE -ne 0) { throw 'Install failed' }
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\deploom-install-skill.exe --client opencode --scope user --verify
```

Пропишите абсолютный путь **этого** компьютера к `.venv/Scripts/python.exe` в `mcp.servers` по [руководству](../USERGUIDE.ru.md#3-подключите-opencode-v2). После установки не перемещайте venv.

Автономная установка Python не делает планирование офлайн: доступ к registry/advisory всё равно нужен, иначе недостающие данные остаются UNKNOWN.

### Раздача файлов со своего компьютера

Обычно достаточно разрешённой общей папки или внутреннего сервиса передачи файлов. Если в вашей среде допустима HTTP-раздача, отдавайте только отдельный каталог комплекта, заменив `YOUR_LAN_ADDRESS`:

```powershell
python -m http.server 8765 --bind YOUR_LAN_ADDRESS --directory .\deploom-trial
```

Коллеги скачивают файлы по этому адресу и порту. Завершите сервер Ctrl+C после раздачи; применяйте обычные правила firewall/сети. У простого сервера нет аутентификации/TLS — при необходимости используйте уже имеющийся защищённый внутренний сервис. Не раздавайте домашний каталог или проект.

**Это передача файлов, а не endpoint Networked MCP.**

## Networked MCP (remote HTTP)

Один общий сервер для нескольких MCP-клиентов — отдельное развёртывание. Штатный entrypoint 0.2.2 поддерживает **только stdio**: готового HTTP-флага, настроек аутентификации и профиля изоляции нескольких пользователей/проектов нет.

Для будущего SDK/gateway-развёртывания нужны authenticated transport, явные границы путей проектов, минимальные права хоста, изоляция state/evidence и ограничения конкурентных процессов. Локальный проект коллеги не появляется на сервере автоматически.

Файловый сервер не является MCP, а непроверенный transport-адаптер — production-поддержкой. Для сегодняшних проб используйте локальный stdio и передавайте установочный комплект.
