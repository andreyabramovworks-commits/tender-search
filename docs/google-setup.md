# Доступ сервера к Google Таблице

Нужен сервисный аккаунт и доступ редактора к таблице tender-search. Обычный API key для записи не подходит. Публиковать таблицу для всех не требуется.

1. Войди нужным Google-аккаунтом в https://console.cloud.google.com/ и создай проект `tender-search` либо выбери существующий рабочий проект.
2. В выбранном проекте открой https://console.cloud.google.com/apis/library/sheets.googleapis.com и включи Google Sheets API.
3. Открой https://console.cloud.google.com/iam-admin/serviceaccounts и создай сервисный аккаунт с именем `tender-search`. Роли администратора проекта для записи в одну таблицу ему не нужны; шаг выдачи ролей проекту можно пропустить.
4. Открой созданный аккаунт → Keys / Ключи → Add key / Добавить ключ → Create new key / Создать новый ключ → JSON. Сохрани скачанный файл на ПК. Если создание ключа запрещено политикой организации, не ослабляй её самостоятельно: сообщи об ограничении, выберем другой способ доступа.
5. Скопируй email сервисного аккаунта, вида `tender-search@PROJECT_ID.iam.gserviceaccount.com`. В своей Google Таблице нажми «Настройки доступа», добавь этот email с ролью «Редактор». Отправлять письмо-уведомление не обязательно.
6. Сообщи локальный путь к скачанному JSON, например `C:\Users\Andrey\Downloads\tender-search-123.json`. Содержимое ключа в чат не вставляй. Файл будет скопирован в закрытую папку секретов; в Git он не попадёт.

Целевая таблица: https://docs.google.com/spreadsheets/d/1-bZ7tvZ2f0yGy8_lue_v8nrWJh-3xGqCvAEZATn6X2g/edit

Временный доступ Codex к Google через подключённый аккаунт не заменяет отдельные серверные учётные данные. На момент проверки коннектор мог читать эту таблицу, но запись вернула 403. После выдачи сервисному аккаунту прав сервер проверит реальную запись и чтение.

Официальные инструкции: https://developers.google.com/workspace/guides/create-credentials#service-account и https://docs.cloud.google.com/iam/docs/keys-create-delete
