# Руководство пользователя DepLoom MCP

[English](USERGUIDE.md) · [Обзор](README.ru.md) · [Раздача коллегам](docs/SHARING_WITH_COLLEAGUES.ru.md)

## Выберите способ запуска

- **Self-hosted MCP (stdio):** каждый разработчик запускает MCP локально через агентский клиент.
- **Networked MCP (remote HTTP):** см. [настройку HTTP и контейнера](docs/HTTP_DEPLOYMENT.md) и ограничения безопасности ниже.

Wheel содержит закреплённый headless Core и канонический скилл. DepLoom Desktop, Electron, клонирование Git и отдельная установка Core не нужны.

## Сетевые запросы и данные проекта

Локальный stdio — это transport между клиентом и сервером, а не обещание офлайн-планирования. Планировщик может запрашивать package/version metadata у заданного registry и vulnerability evidence у OSV (bundled Core по умолчанию использует `api.osv.dev`). Параметр `registry` не перенаправляет трафик OSV в этот registry. Необязательные migration-reference запросы обращаются к описанным официальным источникам. Для первой пробы возьмите несекретный проект, а перед планированием приватных зависимостей проверьте политику передачи данных вашей организации. Proxy меняет маршрут, а не разрешение на передачу данных.

## Self-hosted MCP (stdio)

### 1. Установите релиз

Для первой пробы рекомендуется Python 3.12 или 3.13; пакет объявляет `>=3.12,<3.15`. Также нужны Node, пакетный менеджер проекта и доступ к его разрешённому registry.

Windows, PowerShell:

```powershell
$install = Join-Path $env:LOCALAPPDATA 'DepLoom'
py -3.12 -m venv "$install\venv"
$python = Join-Path $install 'venv\Scripts\python.exe'
& $python -m pip install "https://github.com/DepLoom/mcp/releases/download/v0.2.2/deploom_mcp-0.2.2-py3-none-any.whl"
& $python -m pip check
& $python -c "import importlib.metadata; print(importlib.metadata.version('deploom-mcp'))"
```

Можно сначала скачать wheel, сравнить SHA-256 с `SHA256SUMS.txt` из [того же релиза](https://github.com/DepLoom/mcp/releases/tag/v0.2.2) и установить локальный файл. Это GitHub Releases; публикация в PyPI не предполагается.

Linux с установленным Python 3.12:

```bash
python3.12 -m venv "$HOME/.local/share/deploom/venv"
PYTHON="$HOME/.local/share/deploom/venv/bin/python"
"$PYTHON" -m pip install "https://github.com/DepLoom/mcp/releases/download/v0.2.2/deploom_mcp-0.2.2-py3-none-any.whl"
"$PYTHON" -m pip check
```

Используйте обычные разрешённые настройки Python index, сертификатов и proxy. [Автономный комплект](docs/SHARING_WITH_COLLEAGUES.ru.md) позволяет не скачивать Python-зависимости повторно.

### 2. Установите скилл

Для OpenCode на Windows:

```powershell
& "$install\venv\Scripts\deploom-install-skill.exe" --client opencode --scope user --dry-run
& "$install\venv\Scripts\deploom-install-skill.exe" --client opencode --scope user --verify
```

Linux:

```bash
"$HOME/.local/share/deploom/venv/bin/deploom-install-skill" --client opencode --scope user --verify
```

Каталог: `~/.config/opencode/skills/deploom-upgrade/`. В нём находятся `SKILL.md`, `contract.json`, references, examples и scripts. Релиз 0.2.2 включает контракт скилла **2.0.2**.

Если старые файлы отличаются, сохраните их копию, изучите `--dry-run` и обновите с `--force --verify`. Посторонние дополнительные файлы сохраняются. Не переводите установленные файлы скилла на месте: они связаны контрольными суммами.

Другие варианты: `--client codex`, `--client claude-code` или `--scope project --project /absolute/path/to/project`. Установка скилла **не регистрирует MCP** и не доказывает его загрузку уже работающим клиентом.

### 3. Подключите OpenCode V2

Добавьте запись сервера в существующий `mcp.servers` файла `~/.config/opencode/opencode.jsonc`. Сохраните остальные серверы, провайдеры и permissions. Замените `YOUR_USER` на свой каталог.

```jsonc
{
  "mcp": {
    "servers": {
      "deploom": {
        "type": "local",
        "command": [
          "C:/Users/YOUR_USER/AppData/Local/DepLoom/venv/Scripts/python.exe",
          "-m",
          "deploom_mcp"
        ],
        "environment": {
          "DEPLOOM_MCP_STATE_DIR": "C:/Users/YOUR_USER/AppData/Local/DepLoom/state"
        },
        "disabled": false
      }
    }
  }
}
```

На Linux укажите абсолютный путь `.../.local/share/deploom/venv/bin/python` и доступный для записи state-каталог. В V2 нужны `mcp.servers` и `disabled`, а не старые `mcp.<name>` / `enabled`. [Официальная документация OpenCode](https://opencode.ai/v2/docs/mcp-servers).

Wheel сам находит свой bundled Core. Не переносите старые `PYTHONPATH` и `DEPLOOM_MCP_GENERATOR_PATH` из исходников. Codex и Claude Code используют другие форматы конфига — см. [интеграции](integrations/).

### 4. Перезапустите и проверьте

Перезапустите OpenCode. В списке MCP (`/mcps`) `deploom` должен быть connected. Попросите агента загрузить скилл с точным именем **`deploom-upgrade`**.

Стандартный сервер 0.2.2 предоставляет девять инструментов:

- План: `get_upgrade_plan`, `evaluate_assignment`, `explain_conflict`.
- Explicit goals: `get_goal_plan`, `apply_goal_candidate`, `verify_goal_candidate`, `get_goal_checkpoint`, `get_goal_report`.
- Read-only inventory: `observe_workspace_inventory_v1`.

Необязательно: замените модуль на `deploom_mcp.audit_server`, чтобы добавить `audit_dependencies`. См. [контракт аудита](docs/DEPENDENCY_AUDIT.md).

Обычно сервер запускает клиент. Команда `python -m deploom_mcp` в терминале ждёт сообщения протокола MCP, а не открывает страницу. Не отправляйте обычные чат-запросы в её stdin.

## Первая проба: только план

Откройте небольшой несекретный проект и отправьте:

> Загрузи deploom-upgrade. Через DepLoom MCP подготовь план Yellow. Режим PLAN_ONLY: не меняй файлы, не устанавливай пакеты, не делай commit или публикацию. Используй разрешённый registry проекта. Учти все workspaces в инвентаре, покажи фактическое покрытие планирования, unknown и блокеры. Объясни минимальные изменения и сохрани реальный запрос MCP и путь Blueprint.

Пример прямого вызова инструмента:

```json
{
  "project_path": "C:/projects/example-app",
  "target_level": "yellow",
  "min_lag_ok_pct": 80,
  "target_mode": "advisory",
  "objective": "minimum-change-to-policy",
  "deadline_seconds": 120
}
```

При необходимости передайте разрешённый URL в `registry`; не добавляйте credentials в запрос или URL. Читайте полный Blueprint: `DRAFT_READY` не исключает unknown, caps и частичного workspace-покрытия.

## Затем отдельно разрешите обновление

> По согласованному плану доведи проект до Yellow в изолированной рабочей копии. Сохрани мои изменения, scope, исключения и peer/engine ограничения. Проверь чистую установку, физическое дерево, обязательные проверки проекта и затронутый runtime. Выполни итоговый аудит политики. Покажи версии до/после, блокеры и evidence. Не делай commit, push, релиз или деплой.

Отдельно задайте exact targets, исключённые пакеты и допустимые major-линии. Уровень здоровья не означает «всё до latest».

`PROPOSED` и `PLANNING_ONLY/NOT_VERIFIED` — предложение, а не уже установленное или проверенное в runtime обновление. Тесты/build подтверждают только выполненные сценарии. Финальный аудит должен сохранять scope, знаменатель, пороги, unknown и актуальность evidence.

Для workspaces `SINGLE_MANIFEST_PLANNING`, `PARTIAL` или `jointAssignment=NOT_COMPUTED` требуют согласования на стороне клиента. Инвентарь не разрешает автоматическое выполнение по всему workspace. Explicit-goal verify/apply требует host-авторизации и [поддерживаемого standalone npm-профиля](docs/GOAL_EXECUTION_SUPPORT.md). Ручные изменения агента не становятся вердиктом Core/checkpoint автоматически.

## Networked MCP (remote HTTP)

HTTP-транспорт и контейнер описаны в [инструкции по развёртыванию](docs/HTTP_DEPLOYMENT.md). Общему серверу по-прежнему нужны аутентификация, границы путей и прав проектов, изоляция state/evidence и ограничение конкурентных процессов. Пути относятся к файловой системе **сервера**, а не ноутбука коллеги. Удалённое планирование без границы путей может раскрыть файлы хоста.

Для текущих проб передавайте [локальный установочный комплект](docs/SHARING_WITH_COLLEAGUES.ru.md). HTTP-раздача wheel — **передача файлов, не Networked MCP**.

## Если что-то не работает

- MCP подключён, скилла нет: установите обе части, перезапустите клиент и явно загрузите `deploom-upgrade`.
- Ошибка старта: проверьте stderr, абсолютный Python-путь и реальную версию пакета.
- Другой Core/версия: проверьте runtime provenance и устаревшие source override.
- Ошибка registry/TLS/proxy: сохраняйте UNKNOWN, исправляйте разрешённый маршрут; см. [proxy](docs/SYSTEM_PROXY.md). Не переключайте корпоративный проект на публичные сервисы молча.
- Частичный/capped план: прочитайте recovery action; не повторяйте неизменный запрос.

[Техническая документация](docs/INDEX.ru.md).
