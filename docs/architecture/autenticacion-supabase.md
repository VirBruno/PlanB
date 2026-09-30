# Autenticación con Supabase

## Alcance y autoridad de identidad

US1 registra username/email/contraseña; US2 permite ingresar con email o
username. Supabase Auth almacena y verifica contraseñas. `public.profiles`
contiene solamente el UUID de Auth, username y timestamps. Django no crea
`User`, `AbstractUser`, passwords locales ni perfiles duplicados en el ORM.

Las views delegan en servicios. Formularios y CSRF se validan en el servidor;
JavaScript sólo mejora la interfaz. El dashboard requiere identidad validada
por Supabase y muestra únicamente un saludo y el estado vacío de grupos.

## Clientes y separación de privilegios

Cada operación construye un cliente y transporte nuevos, configura
`persist_session=False` y `auto_refresh_token=False`, y cierra sus recursos al
terminar. No existen singletons de cliente ni almacenamiento compartido entre
requests. El SDK puede guardar temporalmente sesión y headers en su instancia;
por eso los flags se combinan con el ciclo de vida efímero.

| Operación | Credenciales del cliente |
| --- | --- |
| `sign_up`, `sign_in_with_password`, `verify_otp`, `refresh_session` | Publishable key. |
| `get_user(jwt=...)` y lectura del perfil propio | Publishable key + access token del usuario. |
| Disponibilidad/resolución username → UUID | Secret key; SELECT sólo `id, username_normalized`. |
| Resolver email desde UUID | Secret key y `auth.admin.get_user_by_id`. |
| Logout | HTTP con publishable key + Bearer del usuario, sin `auth.admin`. |

No usamos `get_session()` ni `set_session()` para reconstruir estado; pueden
disparar refresh. El servicio decide cuándo renovar mediante
`refresh_session(refresh_token=...)`. Sólo los datos públicos necesarios
llegan a las views: no se pasan respuestas completas del SDK a templates.

## Registro y confirmación

El formulario valida username ASCII de 3–30 caracteres, email, confirmación
de contraseña y la política canónica indicada más abajo. Recorta espacios
externos de los identificadores, nunca de la contraseña. La consulta de
disponibilidad mejora el mensaje, pero la constraint UNIQUE de PostgreSQL
resuelve carreras. `sign_up` envía username en metadata; el trigger crea el
perfil dentro de la transacción de Auth. No se inserta desde Python.

Si la respuesta incluye sesión, se establece la sesión local y se abre el
dashboard. Sin sesión, se muestra el aviso de correo y el visitante continúa
sin autenticar. No se utiliza el UUID de una respuesta sin sesión para inferir
si existe un email: Auth puede ofuscar respuestas a registros duplicados.

La plantilla versionada en `supabase/templates/confirmation.html` usa
`{{ .SiteURL }}/auth/confirmar-email/?token_hash={{ .TokenHash }}`:

1. GET recibe sólo el hash, valida su formato y guarda hash/fecha límite local
   de diez minutos en la sesión Django del servidor.
2. Redirige inmediatamente a `/auth/confirmar-email/`, sin query string.
3. La página muestra un botón, sin hash oculto ni datos de Auth en JavaScript.
4. POST con CSRF consume el estado temporal y llama a:

   ```python
   client.auth.verify_otp({"token_hash": token_hash, "type": "email"})
   ```

5. Limpia el estado temporal ante éxito, caducidad o error. Si Auth devuelve
   una sesión, intenta revocarla sin autenticar localmente y vuelve a login.

No se envía email a `verify_otp` en la variante de hash; `type` es fijo y no
proviene de parámetros arbitrarios. La vigencia del proveedor se verifica
además del límite local. GET nunca consume el token remoto, evitando que una
previsualización de correo confirme automáticamente. Un POST fallido elimina
el pendiente; se puede volver a abrir el correo mientras el hash siga válido.

La URL inicial contiene un secreto de un solo uso. Las respuestas usan
`no-store` y `Referrer-Policy: no-referrer`; no cargan recursos externos. El
filtro de logs de Django elimina query strings sensibles y suprime datos de
requests de Auth. **También configurar el proxy, servidor HTTP y observabilidad**:
registrar sólo el path, no query strings, headers Authorization, cookies,
formularios ni cuerpos de respuestas. Un filtro de Django no limpia los logs
de infraestructura que ocurre antes de Django.

Referencia: [plantillas oficiales](https://supabase.com/docs/guides/auth/auth-email-templates)
y [contrato de tipos del SDK 2.31.0](https://github.com/supabase/supabase-py/blob/v2.31.0/src/auth/src/supabase_auth/types.py).

## Login, sesión y logout

El email se normaliza a minúsculas. Para username se consulta por igualdad
`username_normalized`, se obtiene el UUID y el cliente administrativo obtiene
su email. No se listan todos los usuarios ni se comunica el email resuelto.
La contraseña se verifica únicamente con `sign_in_with_password`.

Cuenta inexistente, no utilizable o password incorrecta usan el mismo mensaje:
“Usuario/email o contraseña incorrectos.” Una caída temporal del proveedor
se trata como indisponibilidad, sin exponer excepciones técnicas. El mismo
mensaje reduce enumeración directa; no garantiza tiempos idénticos para
login email y username.

Al autenticar se regenera la sesión Django y se rota CSRF. Tokens y expiración
se guardan en `SupabaseSession`; el navegador recibe un identificador opaco.
La sesión local dura ocho horas, con cookies HttpOnly, SameSite=Lax y Secure
en producción. El servicio renueva antes del vencimiento, con margen de 60
segundos, bloqueando y releyendo la fila en PostgreSQL. Se guardan juntos ambos
tokens y expiración. Un fallo de refresh invalida la sesión local.

Las rutas protegidas comprueban la sesión, validan el usuario en Auth y leen
el perfil con su JWT sujeto a RLS. `next` sólo permite rutas internas seguras;
el fallback es el dashboard. Un fallo temporal del perfil muestra una respuesta
controlada de servicio no disponible.

Logout sólo acepta POST con CSRF. La llamada está encapsulada en servicios:

```text
POST <SUPABASE_URL>/auth/v1/logout?scope=local
apikey: <SUPABASE_PUBLISHABLE_KEY>
Authorization: Bearer <access token del usuario>
```

No usa secret key ni `auth.admin.sign_out`. Si hace falta renovar un token
vencido, se hace explícitamente. El cierre coordina la misma fila que refresh;
si desaparece, una request tardía no la recrea. Aunque falle la revocación
remota se eliminan `SupabaseSession`, sesión Django y cookie local. Una caída
de la base impide garantizar escrituras: la aplicación no debe presentar esa
condición como una revocación remota exitosa.

El scope local deja abiertas otras sesiones del usuario. Un JWT ya emitido
puede seguir siendo válido hasta su vencimiento; eliminar el refresh token no
revoca criptográficamente ese JWT. Referencia: [sesiones Supabase](https://supabase.com/docs/guides/auth/sessions).

## Configuración manual de Supabase Auth

Los nombres siguientes se verificaron en fuentes oficiales el 29/09/2026.
No se cambian automáticamente por ejecutar Django, instalar el SDK o guardar
estas instrucciones. El administrador debe aplicarlos en cada proyecto.

1. Abrir **Authentication → Sign In / Providers → Auth Providers → Email**
   en el Dashboard del proyecto (`/project/<ref>/auth/providers`). Habilitar
   email/password y registro; decidir si **Confirm Email** estará activo.
2. En el proveedor Email, configurar **Minimum password length** en **12**
   y los requisitos de contraseña en **Letters and digits**. Guardar los
   cambios y reabrir el formulario para comprobarlos. No seleccionar la
   variante que exige mayúsculas/minúsculas separadas o símbolos.
3. Los campos exactos de Management API (`GET/PATCH /v1/projects/{ref}/config/auth`)
   equivalentes son:

   ```json
   {
     "password_min_length": 12,
     "password_required_characters": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ:0123456789"
   }
   ```

   `:` separa los dos conjuntos obligatorios: al menos una letra ASCII de
   cualquier caja y al menos un dígito `0–9`. No es una expresión regular.
   Se permiten otros caracteres y espacios; no reemplazan esos mínimos.
   Referencia: [configuración Auth oficial](https://supabase.com/docs/reference/api/v1-update-auth-service-config).
4. Para una instancia local administrada con Supabase CLI, los nombres son
   distintos y pertenecen a `[auth]` de su `supabase/config.toml`:

   ```toml
   [auth]
   minimum_password_length = 12
   password_requirements = "letters_digits"
   ```

   Integrar esas líneas en el archivo de la instancia, sin duplicar `[auth]`.
   Se verificaron en el [ejemplo oficial versionado](https://github.com/supabase/supabase/blob/master/apps/ui-library/supabase/config.toml#L113-L117).
   No se incluye un `config.toml` parcial que parezca preparar una instancia
   completa; esta aplicación puede conectarse a proyectos locales o alojados.
5. Plan B valida exactamente longitud mínima, letra ASCII y dígito en el
   servidor. No rechaza por similitud, lista de passwords comunes de Django,
   falta de símbolos ni uso de una sola caja. La interfaz recomienda evitar
   información personal/passwords comunes y utilizar un gestor.
6. Si el plan lo permite, recomendamos activar **leaked password protection**
   (HIBP, Pro y superiores). Es una protección opcional del proveedor; la app
   no depende de ella y trata sus rechazos como errores seguros de contraseña.
   [Seguridad de passwords](https://supabase.com/docs/guides/auth/password-security).
7. En **Authentication → URL Configuration**, configurar **Site URL** igual a
   `DJANGO_PUBLIC_URL`, sin barra final: `http://127.0.0.1:8000` en desarrollo y
   el origen HTTPS real en despliegue. Agregar la URL exacta de confirmación a
   **Redirect URLs**. La plantilla usa `SiteURL`, no un destino recibido del
   navegador. Si se usa `localhost`, mantener ese host en toda la configuración.
8. En **Authentication → Email Templates → Confirm signup**, copiar
   `supabase/templates/confirmation.html`. Configurar SMTP para destinatarios
   y volumen del entorno; el correo de prueba incorporado tiene restricciones.
   Comprobar que el email recibido apunta a Django y no incluye access/refresh
   tokens. Ver [SMTP oficial](https://supabase.com/docs/guides/auth/auth-smtp).

Antes de habilitar el servicio, probar altas también por Auth API con 11
caracteres, sin letras y sin números: deben rechazarse; 12 caracteres con ambos
conjuntos debe cumplir la política. El SDK fijado no fija la versión del
servicio remoto: revisar estos ajustes al cambiar de proyecto o proveedor.

## Despliegue y límites

Usar `DJANGO_DEBUG=False`, HTTPS y `DB_SSLMODE=verify-full` con CA confiable.
`DJANGO_PUBLIC_URL` define el origen CSRF de confianza. La configuración activa
redirección HTTPS y cookies Secure fuera de desarrollo. Si un proxy termina
TLS, configurar headers y confianza en el servidor/proxy antes del despliegue;
no confiar en headers reenviados de clientes arbitrarios. `SECURE_PROXY_SSL_HEADER`
no se presupone universalmente.

Credenciales de la base y secret key sólo están en el proceso del servidor.
Para logs/APM no activar captura indiscriminada de variables, cuerpos o
consultas SQL con parámetros. Se conservan controles de frecuencia del
proveedor; el despliegue debe revisar límites por IP en su proxy porque las
llamadas a Auth salen del servidor Django.
