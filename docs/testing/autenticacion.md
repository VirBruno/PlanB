# Validación de US1 y US2

## Suite offline

Instalar `requirements.txt` y activar `.venv`. Desde `backend/`:

```powershell
python manage.py test
python manage.py check --settings=config.settings_test
python manage.py makemigrations --check --dry-run --settings=config.settings_test
python -m pip check
```

`manage.py test` selecciona `config.settings_test` antes de cargar Django, sin
necesitar `.env` o variables del proveedor. Usa SQLite en memoria y ejecuta
las migraciones de sesiones y `users.0001_initial`. El runner bloquea sockets
y DNS para detectar conexiones externas accidentales. Auth, perfiles y logout
remoto se simulan en los tests; los tests de contrato usan transportes HTTP
simulados sin red.

Para ver cada caso o limitar la ejecución:

```powershell
python manage.py test --verbosity 2
python manage.py test apps.users.tests
```

La suite cubre formularios/contraseñas, registro con y sin sesión, confirmación
por hash, login por email y username, clientes aislados, errores del proveedor,
sesión, refresh, logout, rutas protegidas, CSRF y redirecciones. También
comprueba que tokens/claves no aparezcan en HTML y que logout HTTP use API key
pública, Bearer del usuario y scope local. Los tests no aplican SQL remoto.

Los checks esperados son `System check identified no issues`,
`No changes detected` y `No broken requirements found`. Al reportar resultados
del PR, registrar la cantidad y nombres de tests fallidos/omitidos, si los hay,
en lugar de asumir éxito por esos mensajes de check.

## Configuración real

Con `.env` local y PostgreSQL/Supabase preparados según la documentación:

```powershell
python manage.py check
```

Este check valida la configuración, no demuestra conectividad ni existencia
de tablas. Migraciones y servidor se ejecutan después de la preparación manual
documentada; no utilizar datos o credenciales reales en la suite offline.

## Integración SQL

En una instancia **de prueba autorizada**, aplicar primero las dos migraciones
SQL. Como administrador ejecutar `supabase/tests/profiles.sql`, mediante SQL
Editor o `psql`. Con `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` y autenticación
segura preparados para esa instancia, desde la raíz del repositorio:

```powershell
psql -v ON_ERROR_STOP=1 -f supabase/tests/profiles.sql
```

No colocar passwords en la línea de comandos. El script crea fixtures de
Auth de direcciones `example.invalid`, prueba trigger, normalización UNIQUE,
FK/cascade, rollback de alta fallida, grants y RLS para los roles API y termina
con ROLLBACK. No envía email ni prueba la API de Auth. Si falla, la transacción
se revierte al cerrar esa conexión; revisar el primer error antes de repetir.
No ejecutarlo en una base que contenga triggers ajenos con efectos externos.

## Validación funcional manual pendiente

Realizar en desarrollo/pruebas; no habilitar ni modificar producción para
ejecutar esta matriz:

| Caso | Resultado esperado |
| --- | --- |
| Registro válido, confirmación activa | Aviso de correo, sin sesión autenticada; perfil creado por trigger. |
| Correo real | URL contiene únicamente `token_hash`, abre pantalla limpia y botón POST. |
| GET de confirmación/previsualización | No consume el hash en Auth. |
| POST de confirmación con CSRF | Verifica hash/type=email, limpia pendiente y dirige a login. |
| Enlace vencido o usado | Mensaje seguro, sin hash ni excepción técnica en HTML/logs. |
| Registro con confirmación desactivada | Sesión inmediata y dashboard. |
| Password de 11 caracteres, sin letra o sin dígito, vía Auth API | Rechazado por la política configurada en el proveedor. |
| Password 12 caracteres con letra ASCII y dígito | Cumple la política compartida; HIBP podría rechazarla si se activó. |
| Username repetido con otra caja | Se rechaza, sin identidad huérfana. |
| Dos registros simultáneos con mismo username | Sólo uno persiste en Auth y profiles. |
| Login email y username | Mismo usuario y saludo correcto, sin exponer email resuelto. |
| Usuario inexistente/password incorrecta | Mismo mensaje genérico. |
| Dashboard sin sesión | Redirección a login con `next` interno. |
| Refresh próximo a vencer en dos pestañas | Una rotación persistida, segunda request reutiliza el resultado. |
| Refresh/logout concurrentes | Después de logout no reaparece una fila técnica ni sesión válida. |
| Logout mientras Auth no responde | Se elimina la sesión local y su fila técnica. |
| Logout con otra sesión abierta | Sólo se cierra la sesión actual. |
| Logout mediante GET o POST sin CSRF | Operación rechazada. |
| Perfil ajeno con JWT del usuario | RLS impide lectura/actualización. |
| `next` externo, `//host` o esquema inesperado | No se redirige fuera de Plan B. |
| Proxy y logs del servidor | No contienen hash de confirmación, tokens, passwords ni Authorization. |
| Pantalla de 320 px, teclado y sin JavaScript | Formularios utilizables, foco/errores accesibles. |

## Límites de la validación

SQLite no implementa los locks de `select_for_update()`, y mocks no prueban
RLS, SMTP, políticas del proyecto ni la compatibilidad del pooler. Para evaluar
concurrencia real hacen falta conexiones PostgreSQL independientes y barreras
controladas alrededor de la rotación. Estas pruebas no se presentan como
ejecutadas por la suite offline.

El cierre local no revoca de inmediato un JWT emitido; Auth controla su
expiración. Una caída remota puede impedir la revocación, aunque el navegador
ya no tenga una sesión local. Registrar esos límites al probar el flujo.
