# Security и Middleware

## Порядок middleware
`Error -> Throttling -> ActionLogger -> FSMActivity`

## Назначение каждого слоя
- `ErrorMiddleware`
  - перехватывает непредвиденные исключения,
  - отдает безопасное сообщение пользователю,
  - пишет системную ошибку в `user_actions`.
- `ThrottlingMiddleware`
  - ограничивает частоту запросов по `user_id`,
  - хранит last-touch в Redis, fallback на in-memory.
- `ActionLoggerMiddleware`
  - фиксирует пользовательские действия, контекст FSM и ответ бота.
- `FSMActivityMiddleware`
  - отслеживает активность в формах,
  - совместно с reminder-loop отправляет напоминание о незавершенном процессе.

## Механизмы безопасности данных
- Ввод валидируется Pydantic-схемами.
- Пользовательский текст перед выводом экранируется (`formatting.e`).
- Доступ к партнёрским заявкам проверяется через `owner_user_id/service_id`.
- Админ-доступ привязан к `ADMIN_USERNAMES`.

## Интеграционная безопасность
- Google Sheets: доступ только через Service Account.
- Секреты не должны храниться в репозитории.
