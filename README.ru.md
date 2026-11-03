# DepLoom MCP

[English](README.md) · [Руководство пользователя](USERGUIDE.ru.md) · [Документация](docs/INDEX.ru.md)

Headless Core планирования обновлений зависимостей и MCP-сервер экосистемы DepLoom.
Wheel содержит точный закреплённый Core, схему Blueprint и канонический скилл.
DepLoom Desktop и Electron не нужны. Core поддерживается в
[DepLoom/core](https://github.com/DepLoom/core).

## Быстрый старт

1. [Установите MCP и скилл](USERGUIDE.ru.md#self-hosted-mcp-stdio).
2. [Подключите OpenCode V2](USERGUIDE.ru.md#3-подключите-opencode-v2).
3. [Получите план без изменения проекта](USERGUIDE.ru.md#первая-проба-только-план).
4. Затем явно разрешите миграцию и проверку результата.

Примеры закреплены на [MCP 0.2.2](https://github.com/DepLoom/mcp/releases/tag/v0.2.2),
контракт скилла — **2.0.2**. Для коллег есть [установочный комплект](docs/SHARING_WITH_COLLEAGUES.ru.md).

**Self-hosted MCP (stdio)** поддерживается локально.
**Networked MCP (remote HTTP)**: см. [настройку HTTP и контейнера](docs/HTTP_DEPLOYMENT.md).

## Что делает продукт

`get_upgrade_plan` возвращает структурированное предложение: цель, полномочия
по выбору версий, scope, причины решений, ограничения и недостающее evidence.
`evaluate_assignment` и `explain_conflict` исследуют сохранённые решения;
они не выполняют миграцию и не решают заново произвольное альтернативное дерево.

План остаётся **PLANNING_ONLY / NOT_VERIFIED**. `PROPOSED` не означает, что
обновление установлено или совместимо с приложением. Отдельный explicit-goal
маршрут поддерживает host-authorized выполнение только заявленных standalone
npm-профилей. Импортированный план и текст агента таких полномочий не дают.
[Матрица выполнения](docs/GOAL_EXECUTION_SUPPORT.md).

Read-only инвентаризация workspaces не равна совместному плану монорепозитория
и не разрешает автоматическое выполнение по всему workspace. Проверяйте
покрытие, `jointAssignment`, caps, unknown и происхождение решений.

За пределами пакета: Desktop Autopilot, общий dispatch фоновых агентов,
дашборды и GitLab/Renovate-интеграция. Универсальная экономия времени/токенов
агента не является доказанным свойством MCP.

## Как связаны компоненты

```text
Разработчик → OpenCode / другой MCP-клиент
                    ↓ get_upgrade_plan
              DepLoom MCP → закреплённый Core
                    ↓
             Upgrade Blueprint
                    ↓
       Агент + скилл deploom-upgrade
                    ↓
   разрешённые изменения и реальные проверки
```

**MCP** предоставляет инструменты. **Skill** задаёт процесс: preflight, план,
согласование workspaces, изолированное выполнение, проверки и отчёт.
Ни установка скилла, ни успешная сборка не заменяют итоговый аудит политики.

## Требования и установка из исходников

Python `>=3.12,<3.15`; закреплённый Core и SDK проверяются CI на Python 3.12.
Поддерживаются Windows/Linux; z3-solver устанавливается платформенным wheel.
Нужны окружение проекта, Python-зависимости и доступ к разрешённому registry.

Для пробы проще использовать [релизный wheel](USERGUIDE.ru.md).
Для разработки:

```bash
git clone https://github.com/DepLoom/mcp.git
cd mcp
git submodule update --init --recursive
python -m venv .venv
.venv/Scripts/python -m pip install -e .
# Linux: .venv/bin/python -m pip install -e .
```

Запуск из установленного окружения: `python -m deploom_mcp`.
Это stdio-сервер, ожидающий протокол MCP, а не веб-страница.
[Конфигурация клиента и скилл](USERGUIDE.ru.md), [proxy](docs/SYSTEM_PROXY.md).

## Инструменты 0.2.2

| Назначение | Инструменты |
| --- | --- |
| План и evidence follow-up | `get_upgrade_plan`, `evaluate_assignment`, `explain_conflict` |
| Explicit-goal маршрут | `get_goal_plan`, `apply_goal_candidate`, `verify_goal_candidate`, `get_goal_checkpoint`, `get_goal_report` |
| Read-only workspace inventory | `observe_workspace_inventory_v1` |

Опциональный `python -m deploom_mcp.audit_server` добавляет `audit_dependencies`.
Аудит использует текущий lock и пакетный менеджер; не делает install/audit fix
и сам по себе не доказывает runtime или достижение Yellow/Green.
[Контракт аудита](docs/DEPENDENCY_AUDIT.md).

## Что проверять при обновлении

- Явные пороги политики, exact targets/разрешённые линии, scope и исключения.
- Полный Blueprint и unresolved evidence, а не только краткую сводку.
- Согласованность назначений при частичном workspace-покрытии.
- Чистую установку, физическое дерево, обязательные project checks и runtime
  на точных итоговых байтах.
- Финальный аудит: unknown не равен нулю, deferred остаются в знаменателе,
  установка не означает, что тесты выполнены.

Скилл требует отчёт evidence и документы для разработчика. Без вашего разрешения
он не делает commit, push, merge или публикацию.
[Контракт скилла](docs/SKILL_CONTRACT_V2.md).

## Разработка

`src/deploom_mcp/` — сервер/адаптер; `upstream/deploom/` — pin Core;
`contracts/` — схемы; `skills/deploom-upgrade/` — канонический скилл;
`integrations/` — клиентские инструкции; `tests/` — проверки границ продукта.

```bash
.venv/Scripts/python -m unittest discover -s tests -v
# Linux: .venv/bin/python -m unittest discover -s tests -v
```

[Технические контракты и исторические материалы](docs/INDEX.ru.md).
Мейнтейнерам: [релизы компонентов](docs/RELEASING.md),
[проверка дистрибутива](docs/DISTRIBUTION.md).
Не правьте pin Core на месте: изменения проходят в его собственном репозитории.
Эта документация не меняет условия лицензирования/распространения upstream Core.
