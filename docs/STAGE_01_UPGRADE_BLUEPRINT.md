# Stage 1: Upstream Blueprint — headless planning boundary

> Repository note: этот документ и связанные артефакты Stage 1 перенесены из
> `AlexanderLevenskikh/deploom` в самостоятельный репозиторий `DepLoom/mcp`.
> Новые пути: `src/deploom_mcp/blueprint.py` (бывш. `upgrade_blueprint.py`),
> `contracts/upgrade-blueprint.schema.json` (бывш.
> `docs/ecosystem/upgrade-blueprint.schema.json`), `tests/test_blueprint.py`
> (бывш. `tests/test_upgrade_blueprint_headless.py`),
> `benchmarks/measure_blueprint.py` (бывш.
> `scripts/measure_upgrade_blueprint.py`). Ядро планирования подключено как
> submodule `upstream/deploom` (пин `9e20107`). Старый репозиторий не изменён.

Роль документа: техническая основа Stage 2 (MCP-инструмент, OpenCode Skill,
порт Autopilot). Здесь зафиксированы факты о том, как планирование зависимостей
реально работает в DepLoom, какая headless-граница предложена для экосистемы,
какой JSON-контракт выдаётся и что именно нельзя выдавать за доказанное.

## A. Постановка, объём, терминология

Цель Stage 1 — дать AI-экосистеме (агент, скоуп) способ получить план обновления
зависимостей **без Electron и без прав на запись в проект**, с честной метой
«это планирование, а не проверенная истина».

Объём Stage 1 (договорён из задания):
- аудит существующего механизма Draft и поиск headless-границы;
- дизайн контракта Upgrade Blueprint (schemaVersion 2);
- замер производительности (cold/warm кэш, лимиты бюджета);
- тесты на производственной границе;
- этот документ.

НЕ делаем в Stage 1 (и не «подготавливаем фальшивок»): MCP-сервер, OpenCode
Skill, порт Autopilot, REST, новый решатель. Никакого нового планирования.

Термины:
- **Draft** — режим планирования `dependency_live_roadmap_generator.py`
  (`--draft-baseline` / `--mode draft`): только план, без установки, без
  проверок, без git-изменений.
- **Verified pipeline** — полный конвейер (baseline capture, solver
  verification, materialization, project checks). Ему принадлежат статусы
  `CONSTRAINT_SATISFIED` / `MATERIALIZED` / `PROJECT_VERIFIED`.
- **Upgrade Blueprint** — JSON-документ, который отдаёт headless-граница.
- **Boundary** — код `upgrade_blueprint.py`: subprocess-адаптер + проекция на
  blueprint.

## B. Текущее состояние: где реально считается планирование

Всё планирование живёт в `dependency_live_roadmap_generator.py` (один файл,
≈27 000 строк). Ключевые точки (номера строк на момент написания):

| Что | Где |
| --- | --- |
| CLI и аргументы | `main()` ~26306; `--draft-baseline`, `--mode draft`, `--projects-file`, `--artifacts-dir`, `--dashboard-state`, `--events-log`, `--runs-dir`, `--index-file`, `--draft-deadline-seconds`, `--draft-max-candidates`, `--target-level`, `--min-lag-ok-pct`, `--registry`, `--registry-metadata-cache`, `--registry-metadata-ttl` |
| Точка входа воркера | `run_cli()` ~27415 |
| Построение машинного плана | `build_draft_plan()` ~24087 (proposals/unknowns/conflicts/unresolved/counts) |
| Публикация result.json | `publish_draft_result()` ~25617 (атомарно, план+промпт с SHA-256) |
| Каталог артефактов | `_draft_artifacts_dir()` ~26245 → `<artifacts-dir>/runs/<runId>/draft/*` |
| Решатель Draft | `resolve_peer_compatibility()` ~9462 с `modes=("default",)` и `partial_on_incomplete=True`; НЕ `...with_verification` |
| Таксономия статусов строки | `_draft_row_status()` ~24025 |
| Снимок политики/интента | `policy_snapshot_from_env()` ~23725; `DEPLOOM_BASELINE_INTENT_JSON`, `DEPLOOM_ACCEPTANCE_POLICY_JSON` |

Что реально делает Draft-прогон (подтверждено кодом и физическим прогоном):
1. Читает `package.json` + lockfile (инвентаризация, runtime через engine-декларации).
2. Получает метаданные/OSV из registry (сетевой шаг, может быть недоступен).
3. Считает цели обновления и остатки по политике (yellow/green, lag, C/H/U).
4. По необходимости зовёт z3-решатель для peer-совместимости (`solver-components.json`).
5. Публикует `plan.json`, `result.json`, `prompt.md`, `summary.md` — только в
   `<artifacts-dir>/runs/<runId>/draft/*`.
6. НЕ устанавливает, НЕ делает `npm test`, НЕ трогает git (в Draft `source
   checkout guard` отключён), НЕ пишет legacy MD/JSON/HTML.

Важно: Draft **не** использует verified-решатель, поэтому его результат — всегда
планировочный вердикт. Статусы `ok` / `proposed` / `no-change` — это «насколько
плотник может предложить по имеющимся данным», а не доказательство установки.

### Побочные файлы Draft (управление ими — часть границы)
При запуске без параметров Draft кладёт `dashboard-state.json` и историю в
`<settings_base>` (= рабочая директория процесса). Headless-граница обязана
выпинить ВСЕ эти пути в управляемый `output-dir`, чтобы не писать в проект:
`--artifacts-dir`, `--dashboard-state`, `--events-log`, `--runs-dir`,
`--index-file`. Проект при этом только читается.

## C. Headless-граница (Stage 1 реализация)

Реализовано в `upgrade_blueprint.py` (новый модуль в корне репозитория; не
импортирует генератор, работает subprocess-ом — изоляция и воспроизводимость).

```
generate_upgrade_blueprint(project_path, *, policy=..., options=...)
  -> UpgradeBlueprintResult(ok, blueprint, draft_status, plan_path, manifest_path, error, ...)
```

Контракт:
- проект передаётся **абсолютным путём** (читается только `package.json` +
  lockfile);
- child запускается с `cwd = <output-dir>/exec`; ВСЕ пути побочных файлов
  выпинены в `<output-dir>` (см. B);
- `projects.txt` с одной строкой — путь проекта; `--only-project` не нужен
  (single project);
- env очищается от чужих `DEPLOOM_*` и заново выставляются
  `DEPLOOM_MODE=draft`, run/workspace/project id, политика (`target_level`,
  `min_lag_ok_pct`, `DEPLOOM_BASELINE_INTENT_JSON`,
  `DEPLOOM_ACCEPTANCE_POLICY_JSON`), deadline, max-candidates;
- после успеха читаются `plan.json` + `result.json` + `solver-components.json`
  и проектируются в blueprint (без пересчёта);
- ошибки — машиночитаемый `result.error {code,message,detail}`, НЕ исключения:
  `project-not-found`, `invalid-project`, `generator-failed`,
  `generator-timeout`, `manifest-missing`, `manifest-invalid`.

Пример командной строки (для скриптов/агентов):
```
python upgrade_blueprint.py --project C:\path\to\proj \
  --output-dir .\out --run-id run-eco-1 \
  --target-level yellow --min-lag-ok-pct 80 \
  --deadline-seconds 15 --registry-metadata-ttl 0
```

Соответствие существующему воркеру Desktop: Draft-вызов из
`desktop/electron/main.ts` использует ту же планировщиковую логику
(`--draft-baseline`, те же env). Наша граница не меняет Desktop-путь, читатель
`desktop/electron/draft-artifact-reader.ts` остаётся каноническим для result.json.

## D. Upgrade Blueprint (контракт, schemaVersion 2)

Файл-схема: `docs/ecosystem/upgrade-blueprint.schema.json`.

Принципы честности:
1. `status.verificationStatus=NOT_VERIFIED`, `authority=PLANNING_ONLY`,
   `compatibility=UNKNOWN` — всегда, копия из манифеста (не выдуманы).
2. `status.parallelExecutionSafety=UNPROVEN` — планировщик НЕ доказывает
   безопасность параллельной установки.
3. `draftStatus` = `DRAFT_READY` | `DRAFT_PARTIAL` (плюс `partialReason`).
4. Зарезервированные verified-статусы `CONSTRAINT_SATISFIED`,
   `MATERIALIZED`, `PROJECT_VERIFIED` объявлены в схеме, но граница их
   **никогда** не выставляет: `draft_status_to_blueprint()` — замкнутое
   сопоставление, неизвестное → `UNKNOWN`. Тест это проверяет.
5. Исходные счётчики планировщика сохраняются дословно (`draftCounts`), а
   статусные счётчики blueprint считаются по фактическим строкам — чтобы
   вторичный счётчик `unknown-security` не завышал `needsReview`.

Маппинг статусов строки (Draft → Blueprint):
`proposed→PROPOSED`, `no-change→NO_CHANGE`, `ok→POLICY_SATISFIED`,
`excluded→EXCLUDED`, `deferred→DEFERRED`, `blocked→BLOCKED`,
`candidate-search-truncated→CANDIDATE_SEARCH_TRUNCATED`, `no-target→NO_TARGET`,
`unknown-security→UNKNOWN_SECURITY`, `unknown-metadata→UNKNOWN_METADATA`,
`peer-compat-unresolved→PEER_UNRESOLVED`.

`needsReview=true` для: CANDIDATE_SEARCH_TRUNCATED, BLOCKED, NO_TARGET,
UNKNOWN_SECURITY, UNKNOWN_METADATA, PEER_UNRESOLVED. PROPOSED остаётся
действий-подсказкой, даже если в его evidence есть caveat (truncation) — но
только как планирование.

Рекомендательные группы (`recommendationGroups`): группировка по `group`
планировщика (display priority), `kind=display-priority`,
`parallelSafety=UNPROVEN`. Более сильный сигнал связанности — `solverComponents`
из `solver-components.json` (компоненты z3). Обе группы НЕ дают права запускать
`npm install` параллельно; это отражено явно.

## E. Производительность (замер Stage 1)

Инструмент: `scripts/measure_upgrade_blueprint.py` (синтетический проект N deps,
по N повторений, cold/warm через `--registry-metadata-cache`).

Хост замера: Windows, Python 3.14.3 (.venv), реестр npm **недоступен**
(`hostRegistryReachable=false`), поэтому cold==warm — сетевая часть упирается в
HTTP-deadlock (~0.75 c/пакет), а не в кэш. Числа честно отражают это.

| Сценарий | wall (медиана) | planner elapsedMs (медиана) | Статус |
| --- | --- | --- | --- |
| 0 deps (фикс-оверхед) | 0.85 s | 122 ms | DRAFT_READY |
| 5 deps, deadline 15 s | 4.73 s | 3 979 ms | DRAFT_READY (5× registry-failed) |
| 40 deps, deadline 120 s | 31.3 s | 30 530 ms | DRAFT_READY (40× registry-failed) |
| 40 deps, deadline 15 s | 14.7 s | 14 021 ms | **DRAFT_PARTIAL** (18/40 registry-failed, остаток pending) |

Выводы:
- **Фиксированный локальный оверхед ≈ 0.85 s wall** (из них ~0.7 s — старт
  интерпретатора на Windows; planner сам ~0.12 s). Это «потолок скорости» для
  холодного прогона сюда.
- **Реестр — доминирующая статья**: при недоступном registry цена ≈ 0.75 s /
  пакет до HTTP-таймаута. При доступном registry время уходит на packument
  fetch; warm-кэш (`--registry-metadata-cache`, TTL 3600) должен убрать повторные
  fetch — повторный замер на хосте с реестром покажет разницу cold/warm.
- **Deadline-бюджет — главный регулятор стоимости**: `--draft-deadline-seconds`
  превращает долгий прогон в `DRAFT_PARTIAL` за ~deadline (14.7 s вместо 31+ s при
  недоступном реестре). Для агентских вызовов рекомендуем 15–30 s.
- Воспроизводимость: два одинаковых прогона дают один и тот же `hashes.plan`
  (SHA-256 plan.json) и одинаковый набор вердиктов (проверяется тестом).

## F. Тесты и верификация

`tests/test_upgrade_blueprint_headless.py` (10 тестов, все зелёные):

- **Unit** (`StatusVocabularyTests`, `TranscodeUnitTests`): замкнутый маппинг
  статусов; зарезервированные verified-статусы недостижимы; `needsReview`;
  рекомендателые группы; проекция транскодера (semantics, hashes, counts).
- **Integration (production boundary)**: реальный subprocess планировщика на
  реальном проекте —
  - валидный blueprint, `NOT_VERIFIED/PLANNING_ONLY/UNKNOWN`, schemaVersion 2;
  - **проект не записан**: бит-в-бит снимок директории до/после;
  - детерминизм: два прогона → одинаковый `hashes.plan` и вердикты;
  - машиночитаемые ошибки: `project-not-found`, `invalid-project`.

Прогон: `python run_tool_tests.py --suite all` (кандидат на валидацию кода,
см. ниже фактический результат) и отдельно
`python -m unittest tests.test_upgrade_blueprint_headless -v`.

## G. Ограничения и риски

- **Нет доказательства безопасности параллельной установки.** Группы
  (display-priority) и solverComponents — планирование, не гарантия
  параллельности. Стадия миграции с реальным `npm install` должна решать это
  отдельно (isolated trial workspace, cohort handoff — задел уже есть в
  `iterative_*`).
- **Draft_plan ≠ baseline-ручной handoff.** Итеративные модули
  (`iterative_migration.py` и др.) потребляют собственный durable state и
  `--targets-file`, а не Draft `plan.json`. Прямого контракта Draft → миграция в
  Stage 1 не создаётся; это работа для Stage 2/3 (согласовать таргеты через
  blueprint или явный handoff).
- **Registry недоступность** делает часть строк `UNKNOWN_METADATA` —
  искусственно «исправлять» их нельзя (правило AGENTS.md: инфра-фейлы не должны
  превращаться в ноль уязвимостей или ложную совместимость).
- Repro/сборка: `plan.json` содержит абсолютные пути проекта — стабильны при
  том же пути; при переносе проекта хэш плана меняется (это ожидаемо).
- Прогон в `output_dir` по умолчанию в системном tmp — данные не сохраняются.
  Для аудита передавать явный `--output-dir`.

## H. Рекомендации для Stage 2 (MCP, Skill, Autopilot)

1. **MCP-инструмент** `get_upgrade_plan(project_path, policy...) → blueprint`:
   вызывает `generate_upgrade_blueprint`; возвращает компактный протокол
   (статусы, вердикты, counts), не весь plan.json целиком в контекст.
2. **Честный протокол статусов в ответе агенту**: всегда сопровождать
   `authority=PLANNING_ONLY`; просить подтверждение на действия, помеченные
   `needsReview`; `PROPOSED` — «можно предлагать», не «уже установлено».
3. **Бюджет**: дефолт `deadline-seconds 15..30`, кэш metadata
   (`registry-metadata-cache`) сохранять между вызовами в управляемом каталоге.
4. **Skill/Autopilot**: использовать рекомендателые группы как «порядок», а не
   «параллельность»; параллельная установка — только после миграционного
   трейла/cohort handoff и проектных проверок (это владение Stage 3+).
5. **Никуда не светить priv-артефакты**: работа всегда в `--output-dir` вне
   проекта; при публикации — `scripts/check-public-sanitization.py`.
6. **Не расширять `main()` генератора**: новый планировщик/порты — в отдельных
   модулях поверх существующих python-функций (edge boundary), как сделано в
   `upgrade_blueprint.py`.

## I. Стабилизационный патч (P0–P2) — что изменилось в границе

Граница переехала в пакет `src/deploom_mcp/blueprint.py` (не корневой модуль).
Стабилизационный патч перед Stage 3 добавил fail-closed проверки на выходной
границе; всё это зафиксировано тестами `tests/test_blueprint_hardening.py`
против **fake-core** (`tests/fixtures/fake_core/dependency_live_roadmap_generator.py`),
без сети:

- **Контракт статусов — fail-closed**: из `result.json` принимаются только
  `DRAFT_READY` / `DRAFT_PARTIAL`; `NOT_VERIFIED` / `PLANNING_ONLY` / вердикт
  вне планировочного словаря → `contract-violation`, планирование отказано.
- **plan.json обязателен**: отсутствие → `plan-missing`, невалидная структура
  (proposals/counts) → `plan-invalid`. До патча план был опциональным.
- **Хэш-верификация**: `plan.json` и `prompt.md` сверяются с SHA-256,
  объявленным планировщиком (`publish_draft_result` пишет их атомарно);
  расхождение → `hash-mismatch`, артефакт не возвращается как достоверный.
- **JSON Schema на выходе**: blueprint валидируется против
  `contracts/upgrade-blueprint.schema.json` до `ok=true` (`jsonschema` — теперь
  runtime-зависимость); сбой → `schema-missing` / `schema-validator-unavailable`
  / `schema-invalid`. До патча схема проверялась только в тестах.
- **Fs-изоляция на границе записи**: `resolve_output_outside()` запрещает
  output/cache внутри проекта и его родителей (`output-inside-project`);
  каталог запуска уникален на вызов (`run-<hash>-<n>`, exclusive mkdir), путь
  без изменений при `--run-id` → `output-conflict`, повторная запись в
  существующие артефакты невозможна.
- **Исключения**: `exclude`/`--exclude-dependency <pkg>|reason` применяются до
  планирования (как в Desktop-вызове); пустой reason — `invalid-exclude`.
- **Solver components**: канонический источник — манифест; fallback — обёртка
  `{"components": [...]}` из `solver-components.json`.
- **inputFiles**: относительные имена резолвятся от корня проекта, не от CWD.
- **Async + отмена**: `generate_upgrade_blueprint_async(..., cancel_event)`;
  отмена/таймаут убивают всё дерево процессов и возвращают `DRAFT_PARTIAL`
  (если часть плана уже есть) вместо ложной ошибки. Общий runner —
  `src/deploom_mcp/process_runner.py` (Windows `taskkill /T /F`, POSIX killpg,
  bounded output).

Новые коды ошибок границы: `plan-missing`, `plan-invalid`, `contract-violation`,
`hash-mismatch`, `schema-missing`, `schema-validator-unavailable`,
`schema-invalid`, `output-conflict`. Полный список и форма ответа — в
`STAGE_02_MCP_SERVER.md`.

