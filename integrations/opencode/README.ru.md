# deploom-upgrade: подключение OpenCode

[English](README.md) · [Простой старт с wheel](../../USERGUIDE.ru.md)

Это инструкция подключения одного канонического скилла `skills/deploom-upgrade`.
Процесс миграции и evidence определены в самом `SKILL.md` и references;
отдельного варианта `deploom-upgrade-opencode` нет.

## Установка скилла

После установки wheel:

```powershell
deploom-install-skill --client opencode --scope user --dry-run
deploom-install-skill --client opencode --scope user --verify
```

Либо из исходников:

```bash
python scripts/install_skill.py --client opencode --scope project --project /path/to/project
python scripts/install_skill.py --client opencode --scope user --dry-run
```

Каталоги: `<project>/.opencode/skills/deploom-upgrade/` или
`~/.config/opencode/skills/deploom-upgrade/`. Копируется весь комплект:
SKILL, contract, references, examples, scripts. Инструкции используются
клиентом по мере необходимости, а не загружаются целиком в каждый запрос.

Старую отличающуюся установку сначала сохраните и проверьте dry-run;
для обновления канонических файлов нужен `--force --verify`.
Квитанция установки доказывает байты файлов, но не загрузку клиентом.

## MCP: формат OpenCode V2

В существующем `~/.config/opencode/opencode.jsonc` или конфиге проекта
добавьте только запись `mcp.servers.deploom`. Сохраните остальные настройки.

```jsonc
{
  "mcp": {
    "servers": {
      "deploom": {
        "type": "local",
        "command": ["C:/path/to/venv/Scripts/python.exe", "-m", "deploom_mcp"],
        "environment": {"DEPLOOM_MCP_STATE_DIR": "C:/path/to/deploom-state"},
        "disabled": false
      }
    }
  }
}
```

Укажите реальные абсолютные пути к установленному Python и state-каталогу.
На Linux используйте `.../venv/bin/python`. В V2 серверы лежат в
`mcp.servers`; старые `mcp.<name>` и `enabled` не подходят.
[Официальный формат](https://opencode.ai/v2/docs/mcp-servers).

Wheel включает собственный Core. Устаревшие `PYTHONPATH` и
`DEPLOOM_MCP_GENERATOR_PATH` из другого checkout могут подменить runtime;
обычной релизной установке они не нужны.

После изменения MCP/скилла перезапустите OpenCode, проверьте connected в
`/mcps` и попросите явно загрузить `deploom-upgrade`. Стандартный сервер
0.2.2 предоставляет девять инструментов, перечисленных в [руководстве](../../USERGUIDE.ru.md).
Репозиторий/установщик скилла не меняет глобальный MCP-конфиг автоматически.

## Права и первая проба

Скилл использует обычные инструменты клиента: чтение/правки, shell для
пакетного менеджера и проверок, MCP для плана. Внешняя изолированная рабочая
копия может потребовать разрешения `external_directory`.
Скилл не включает опасные permissions и не обходит отказ клиента.

Начните с [PLAN_ONLY запроса](../../USERGUIDE.ru.md#первая-проба-только-план).
Headless-запуск с автоматическим подтверждением применим только к disposable
fixture и подтверждённым флагам вашей версии CLI; для рабочего проекта лучше
интерактивное выполнение с явными полномочиями.

Скилл не создаёт commit/PR/merge без разрешения и не придумывает решатель.
Follow-up и explicit-goal инструменты сохраняют собственные ограничения
authority и поддерживаемых профилей. Частичный workspace-план не является
планом всего проекта.
