# Headless-дистрибутив MCP

[English](DISTRIBUTION.md) · [Установка для пользователей](../USERGUIDE.ru.md)

Wheel включает закреплённый Core, схемы, canonical Skill и установщик.
Он запускается без Electron, Desktop и соседнего checkout.
Прохождение package smoke не является проверкой агентской миграции.

## Сборка из закреплённых исходников

Используйте Python 3.12–3.13 и инициализируйте submodule.
Установите runtime-зависимости и инструменты `build`, `setuptools`, `wheel`
в отдельное окружение. После намеренного изменения pin Core или ресурсов:

```bash
python scripts/check_skill.py --write
git add upstream/deploom
python scripts/update_release_manifest.py
python scripts/check_skill.py
python scripts/update_release_manifest.py --check
python -m build --no-isolation
```

Обновление manifest требует совпадения HEAD submodule со staged gitlink
и не принимает tracked-правки Core. Изменяйте Core в его собственном
репозитории и затем обновляйте pin. Команды не повышают версию,
не создают tag и не публикуют пакет.

Сборка проверяет явный inventory Core и canonical Skill до копирования.
В пакет не включаются Desktop, Git history, тесты, caches и неучтённые
локальные файлы. Sdist содержит тот же явный inventory Core:
для пересборки wheel из sdist загрузка submodule не нужна.
Drift схемы/скилла требует осознанного обновления manifest.

## Установка и запуск локальной сборки

```bash
python -m pip install dist/deploom_mcp-0.2.2-py3-none-any.whl
deploom-mcp
deploom-install-skill --client codex --scope user --verify
```

Для изолированной проверки установщика используйте
`--target /absolute/test/skill-dir` вместо user scope.
Установщик не редактирует MCP-конфиг. Планирование остаётся
`PLANNING_ONLY`, даже когда wheel и ресурсы прошли distribution checks.

## Проверки дистрибутива

```bash
python -m unittest discover -s tests -v
python scripts/smoke_stdio.py
python -m build --no-isolation
python scripts/smoke_wheel.py dist/deploom_mcp-0.2.2-py3-none-any.whl
```

`smoke_wheel.py` создаёт временное окружение вне checkout, переиспользует
установленные runtime-зависимости и ставит только указанный wheel.
Он проверяет запуск реального bundled Core, shipped schema, read-only
планирование пустого synthetic-проекта и побайтную установку скилла.
Registry и мигрированное приложение в этом smoke не проверяются;
непустой planning отдельно проверяется production-boundary тестами.

При изменениях упаковки проверьте и wheel, пересобранный из sdist.
Зафиксируйте SHA wheel и версии Python/зависимостей.
Локальная сборка не равна публичному релизу и не доказывает качество/скорость миграции.
