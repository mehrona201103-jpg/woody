# Woody AI — подготовка к PWABuilder (v0.7.3)

## Что исправлено в проекте
- Обновлён `frontend/manifest.webmanifest`: стабильный `id`, корректные PWA-поля и отдельные иконки `any` / `maskable`.
- Проверено наличие четырёх PNG-иконок: 192×192 и 512×512 для обычного и maskable вариантов.
- Обновлена версия ссылки на manifest в `frontend/index.html`.
- Обновлена версия shell-кэша Service Worker до `woody-shell-v073`.
- Добавлен шаблон Digital Asset Links: `.well-known/assetlinks.json.example`.

## Важно: предупреждение PWABuilder про screenshots
В исходном ZIP нет настоящих скриншотов экранов приложения. Я не добавлял выдуманные изображения и не выдавал рекламную картинку за экран приложения.
Чтобы убрать предупреждение **Add screenshots to showcase your app**, сделайте скриншоты реального Woody AI после открытия сайта:
- телефон: PNG/JPEG, портретный скриншот приложения;
- планшет/desktop (по желанию): скриншот реального интерфейса в широком формате.

Затем добавьте изображения в `frontend/assets/screenshots/` и добавьте их в массив `screenshots` в `frontend/manifest.webmanifest`. Не используйте скриншот панели PWABuilder как скриншот приложения.

## Digital Asset Links для Android TWA
После генерации пакета в PWABuilder получите SHA-256 fingerprint сертификата подписи. Затем:
1. Скопируйте `.well-known/assetlinks.json.example` в `.well-known/assetlinks.json`.
2. Впишите окончательный Android package ID и SHA-256 fingerprint без изменения формата.
3. Разверните файл на Railway, чтобы он открывался по адресу:
   `https://woodyai.up.railway.app/.well-known/assetlinks.json`
4. Если включите Google Play App Signing, добавьте также SHA-256 сертификат подписи приложения из Play Console.

Не публикуйте keystore, пароли подписи, API-ключи или `WOODY_ACCESS_TOKEN`.

## Проверка перед сборкой
- Сначала обновите проект на GitHub/Railway.
- Откройте `https://woodyai.up.railway.app/manifest.webmanifest` и проверьте, что manifest открывается как JSON.
- Проверьте доступность всех PNG из массива `icons`.
- В PWABuilder повторите анализ сайта.
- Предупреждение про screenshots останется до добавления настоящих скриншотов.
- Сгенерируйте APK для теста и AAB для Google Play только после выбора окончательного package ID и настройки подписи.

## Ограничение
APK/AAB и окончательный `assetlinks.json` не могут быть подготовлены окончательно без выбора package ID и сертификата подписи. Этот проект подготавливает PWA-часть, но сам по себе не создаёт подписанные Android-пакеты.
