# Plan B

Plan B es un proyecto universitario desarrollado con metodología Agile para
facilitar la organización de planes compartidos. Esta entrega implementa registro, inicio de sesión por email o username
y gestión de grupos con owner, detalle y listado de pertenencias.
El owner puede editar el grupo o eliminarlo previa confirmación.
Propuestas, invitaciones, preferencias y compatibilidad quedan para próximas US.

## Stack y responsabilidades

- Python 3.13, Django 6.1.1 y templates/forms/views del servidor.
- Supabase Auth como única autoridad de identidad y contraseñas.
- Supabase Data API y PostgreSQL para perfiles, grupos y membresías, con RLS.
- PostgreSQL por conexión directa para sesiones técnicas Django en
  `django_internal`; puede estar en Supabase o en otra instancia.
- psycopg 3.3.6, python-dotenv 1.2.3, supabase-py 2.31.0 y httpx 0.28.1.
- CSS y JavaScript propios, sin framework frontend ni Django REST Framework.

Las dependencias directas están fijadas en `requirements.txt`. No existe un
usuario Django paralelo: `auth`, `admin` y `contenttypes` dejan de estar
instalados y `/admin/` deja de exponerse. Se mantienen sesiones, mensajes,
archivos estáticos, CSRF y middleware de seguridad. Ninguna tabla preexistente
se elimina automáticamente al cambiar de arquitectura.

## Estructura

```text
PlanB/
├── backend/
│   ├── manage.py
│   ├── config/                 # Settings de ejecución/tests, URLs, ASGI/WSGI
│   └── apps/users/
│       ├── services/           # Auth, perfiles, clientes y sesiones
│       ├── migrations/         # Modelo técnico de sesión; no usuario Django
│       ├── templates/          # Formularios y dashboard
│       ├── static/users/       # CSS y JavaScript accesible
│       └── tests/              # Suite offline con mocks
├── supabase/
│   ├── migrations/             # Infraestructura y profiles, revisión manual
│   ├── templates/              # Correo de confirmación
│   └── tests/                  # Integración SQL, sólo entorno autorizado
├── docs/architecture/          # Flujos, decisiones y esquema de datos
├── docs/testing/               # Tests offline y validación real pendiente
├── .github/pull_request_template.md
├── .env.example
├── .gitignore
└── requirements.txt
```

## Requisitos previos

Para instalar y correr tests: Git y Python 3.13 con `pip` y `venv`.
Para registro/login reales: una instancia Supabase preparada, PostgreSQL para
sesiones y acceso a sus configuraciones por el equipo. Los comandos siguientes
se ejecutan en PowerShell en Windows.

## Clonar e instalar

```powershell
git clone https://github.com/VirBruno/PlanB.git
cd PlanB
git switch feature/group-creation
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

La rama indicada corresponde a esta entrega; después de integrar el PR, usar
la rama base del equipo. Si PowerShell bloquea la activación:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

## Tests sin servicios externos

Con `.venv` activo, no hacen falta `.env`, credenciales, PostgreSQL ni Supabase:

```powershell
cd backend
python manage.py test
python manage.py check --settings=config.settings_test
python manage.py makemigrations --check --dry-run --settings=config.settings_test
python -m pip check
```

`test` selecciona automáticamente settings aislados, migra SQLite en memoria y
usa mocks para la red. Un check exitoso no prueba conectividad, RLS ni correo.
Ver la [guía de pruebas](docs/testing/autenticacion.md).

## Configurar el entorno de ejecución

Desde la raíz, cada integrante crea su archivo local; no se versiona:

```powershell
Copy-Item .env.example .env
notepad .env
```

Generar una clave propia de Django y guardarla únicamente en ese archivo:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

| Variable | Uso |
| --- | --- |
| `DJANGO_SECRET_KEY` | Clave privada de Django de este entorno. |
| `DJANGO_DEBUG` | `True` sólo en desarrollo; `False` para despliegue HTTPS. |
| `DJANGO_ALLOWED_HOSTS` | Hosts separados por comas, sin protocolo ni puerto. |
| `DJANGO_PUBLIC_URL` | Origen público sin barra final; local: `http://127.0.0.1:8000`. |
| `SUPABASE_URL` | URL HTTPS del proyecto; HTTP sólo para una instancia local. |
| `SUPABASE_PUBLISHABLE_KEY` | API key para operaciones con privilegios del usuario. |
| `SUPABASE_SECRET_KEY` | Clave administrativa exclusivamente del servidor. |
| `DB_NAME`, `DB_HOST`, `DB_PORT` | Conexión PostgreSQL para sesiones técnicas. |
| `DB_USER`, `DB_PASSWORD` | Rol de ejecución y contraseña, provistos por el administrador. |
| `DB_SSLMODE` | TLS de PostgreSQL; usar `verify-full` en despliegue. |

Las variables del proceso tienen prioridad sobre `.env`. Nunca copiar la
secret key, tokens ni credenciales a templates, JavaScript, capturas o PRs.

## Preparación manual de Supabase y PostgreSQL

La creación local de archivos **no aplica migraciones ni cambia el proyecto
remoto**. El equipo debe revisar y autorizar estos pasos en su entorno:

1. Revisar si ya existen usuarios, `profiles`, roles o tablas del bootstrap.
2. Aplicar en orden las dos migraciones de `supabase/migrations/`, según el
   [procedimiento de esquema y permisos](docs/architecture/esquema-datos.md).
3. Habilitar credenciales independientes para `planb_django_migrator` y
   `planb_django_runtime`. Ejecutar `migrate` con el primero; usar el segundo
   para servir requests. El SQL crea ambos sin LOGIN ni contraseñas.
4. Mantener `django_internal` y `planb_private` fuera de los esquemas expuestos
   por Data API. Verificar RLS y grants en `public.profiles`.
5. Configurar Auth con **12 caracteres, una letra ASCII y un dígito**, Site URL,
   URLs permitidas, SMTP y la plantilla de confirmación. Las opciones exactas
   están en la [guía de autenticación](docs/architecture/autenticacion-supabase.md#configuración-manual-de-supabase-auth).

Con variables del **rol migrador** cargadas de manera segura en una terminal
dedicada, desde `backend/`:

```powershell
python manage.py check
python manage.py migrate --plan
python manage.py migrate
```

Cerrar esa terminal para no heredar sus credenciales. Abrir otra con `.venv`
activo y `.env` configurado para el **rol de ejecución**:

```powershell
cd backend
python manage.py check
python manage.py runserver
```

Abrir <http://127.0.0.1:8000/registro/> o <http://127.0.0.1:8000/login/>.
El dashboard está en `/dashboard/` y requiere una sesión válida. Para detener
el servidor: `Ctrl+C`. `runserver` es únicamente para desarrollo.

## Flujo de uso

Registro solicita username, email y contraseña con confirmación. El username
tiene 3 a 30 caracteres ASCII: letras, dígitos, punto, guion y guion bajo;
comienza con letra o dígito y es único sin distinguir mayúsculas.

Si Auth exige confirmar el email, se muestra un aviso. El correo abre una
página que pide confirmar mediante un botón; después se inicia sesión desde
login. Si el proyecto permite sesión inmediata, el registro abre el dashboard.
Login acepta email o username. El dashboard muestra los grupos a los que pertenecés y permite crear uno
con nombre y descripción opcional. El creador queda asociado como owner.
El cierre de sesión se realiza con su botón POST.

## Ramas y Pull Requests

Cada tarea/US usa una rama acotada (`feature/<us>-<descripcion>` o
`chore/<descripcion>`). Reemplazar los marcadores antes de ejecutar:

```powershell
git switch <rama-base>
git pull --ff-only
git switch -c feature/<us>-<descripcion>
```

Antes del PR, ejecutar validaciones, actualizar documentación y completar la
plantilla con objetivo, cambios, motivación, cómo probar y fuera de alcance.
Solicitar revisión de otro integrante. No incluir `.env`, `.venv`, tokens ni
datos reales. Aplicar SQL remoto es un paso de despliegue separado.

## Documentación

- [Arquitectura y alcance](docs/architecture/README.md).
- [Autenticación y configuración del proveedor](docs/architecture/autenticacion-supabase.md).
- [Esquema, roles, migraciones y protección de tokens](docs/architecture/esquema-datos.md).
- [Pruebas offline e integración](docs/testing/autenticacion.md).

## Grupos

La app `backend/apps/groups/` coordina el dominio mediante Data API.
Las tablas `public.groups` y `public.group_members` se almacenan en el
PostgreSQL de Supabase. No existen modelos Django ni una copia local.

Después de la preparación inicial, aplicar una sola vez
`supabase/migrations/202609300001_groups.sql` como administrador.
No requiere nuevas migraciones Django ni cambios en `.env`.
Ver [despliegue y pruebas de grupos](docs/testing/grupos.md) para los pasos
exactos, validación de persistencia real, RLS y atomicidad.

Para habilitar edición y eliminación por el owner, aplicar después
`supabase/migrations/202609300002_group_management.sql`. Se mantienen
RLS y escritura exclusivamente por RPC. Ver el procedimiento completo en
[edición y eliminación](docs/testing/grupos.md#edición-y-eliminación-del-grupo).
