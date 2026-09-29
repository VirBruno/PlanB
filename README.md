# Plan B

Plan B es un proyecto universitario desarrollado con metodología Agile.
Esta base técnica prepara el backend para implementar las User Stories del
Sprint 0. Las funcionalidades del producto se incorporarán en tareas posteriores.

## Stack actual

- Python 3.13 (entorno inicial: 3.13.3).
- Django 6.1.1.
- PostgreSQL, local o alojado en un proveedor como Supabase.
- psycopg 3.3.6, con distribución binaria para facilitar la instalación.
- python-dotenv 1.2.3 para cargar variables de entorno locales.

Las versiones de las dependencias directas están fijadas en `requirements.txt`
y corresponden al entorno inicial del proyecto.

## Estructura

```text
PlanB/
|-- backend/
|   |-- manage.py
|   |-- config/
|   |   |-- __init__.py
|   |   |-- settings.py
|   |   |-- urls.py
|   |   |-- asgi.py
|   |   `-- wsgi.py
|   `-- apps/
|       `-- __init__.py
|-- docs/
|   `-- architecture/
|       `-- README.md
|-- .github/
|   `-- pull_request_template.md
|-- .gitignore
|-- .env.example
|-- requirements.txt
`-- README.md
```

- `backend/`: código del backend y comandos de gestión mediante `manage.py`.
- `backend/config/`: configuración de Django, rutas y entradas del servidor.
- `backend/apps/`: futuras aplicaciones de dominio.
- `docs/architecture/`: decisiones y alcance de la arquitectura inicial.
- `.github/`: plantilla para documentar y revisar Pull Requests.
- `.env.example`: referencia de configuración sin credenciales reales.
- `.venv/` y `.env`: recursos locales ignorados por Git, creados por cada integrante.

Se mantienen los componentes estándar de Django, incluidos administración,
autenticación, sesiones y la ruta `/admin/`. Este bootstrap no agrega usuarios,
registro, login, grupos de dominio, propuestas, preferencias, modelos de negocio,
endpoints propios ni algoritmo de compatibilidad.

## Requisitos previos

- Git.
- Python 3.13 con `pip` y `venv` disponibles mediante el comando `py` en Windows.
- Una instancia PostgreSQL accesible, con base y usuario creados y permisos para
  crear tablas. Puede ser local o de un proveedor externo.

Los comandos siguientes están preparados para **PowerShell en Windows**.

## Clonar y preparar el entorno

```powershell
git clone https://github.com/VirBruno/PlanB.git
cd PlanB
```

Para revisar este bootstrap mientras se encuentra en su rama de trabajo:

```powershell
git switch chore/bootstrap-project
```

Crear y activar el entorno desde la raíz del repositorio:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Si PowerShell bloquea la activación por su política de scripts, habilitarla
únicamente para la sesión actual y volver a activar:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

En una nueva terminal, volver a activar `.venv` antes de ejecutar los comandos.

## Configurar variables de entorno

Crear la configuración local desde la raíz:

```powershell
Copy-Item .env.example .env
notepad .env
```

El archivo `.env` no se versiona. Reemplazar los valores de ejemplo con los del
entorno local. Para generar una clave de Django propia con `.venv` activo:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Copiar el resultado en `DJANGO_SECRET_KEY` en `.env`.

| Variable | Uso |
| --- | --- |
| `DJANGO_SECRET_KEY` | Clave propia del entorno, obligatoria. |
| `DJANGO_DEBUG` | `True` para desarrollo local; por defecto `False`. |
| `DJANGO_ALLOWED_HOSTS` | Hosts separados por comas, sin protocolo ni puerto. |
| `DB_NAME` | Nombre de la base PostgreSQL, obligatorio. |
| `DB_USER` | Usuario de PostgreSQL, obligatorio. |
| `DB_PASSWORD` | Contraseña de PostgreSQL, obligatoria. |
| `DB_HOST` | Host de PostgreSQL, obligatorio. |
| `DB_PORT` | Puerto de PostgreSQL; por defecto `5432`. |
| `DB_SSLMODE` | Modo SSL de psycopg; por defecto `prefer`. |

Para una instancia alojada, usar los datos de conexión PostgreSQL y el modo SSL
indicados por el proveedor. No se requieren claves de API ni un SDK de Supabase.
La aplicación carga `.env` desde la raíz del repositorio, incluso al ejecutar
comandos desde `backend/`. Las variables del proceso tienen prioridad sobre `.env`.

## Validar y ejecutar Django

Con el entorno activo y las variables configuradas:

```powershell
cd backend
python manage.py check
```

El resultado esperado es `System check identified no issues (0 silenced).`
Este comando valida la configuración y no verifica la conexión con PostgreSQL.

Con una base PostgreSQL accesible, aplicar las migraciones estándar de Django
antes de iniciar el servidor por primera vez:

```powershell
python manage.py migrate
python manage.py runserver
```

Abrir <http://127.0.0.1:8000/>. Con `DJANGO_DEBUG=True`, se muestra la página
inicial de Django. `/admin/` conserva la administración estándar; este bootstrap
no crea cuentas. Para detener el servidor, presionar `Ctrl+C`.

`runserver` se utiliza para desarrollo local. El despliegue se definirá en una
tarea posterior. Si aparecen errores de conexión, revisar que PostgreSQL está
activo y que los valores `DB_*` correspondan a una base existente.

## Ramas y Pull Requests

Cada tarea o User Story debe desarrollarse en una rama propia, con un alcance
acotado y un nombre descriptivo, por ejemplo `chore/bootstrap-project` o
`feature/<identificador>-<descripcion>`.

Para comenzar una tarea desde la rama base acordada por el equipo:

```powershell
git switch <rama-base>
git pull --ff-only
git switch -c feature/<identificador>-<descripcion>
```

Reemplazar los marcadores entre `<...>` antes de ejecutar esos comandos.
Al completar una tarea, validar los cambios, actualizar la documentación y
abrir un Pull Request hacia la rama base acordada. Completar la plantilla con
objetivo, cambios, motivación, cómo probar, checklist y fuera de alcance.
Solicitar revisión de otro integrante antes de integrar los cambios.

No versionar `.env`, credenciales, `.venv` ni bases locales. Las funcionalidades
de las User Stories posteriores deben permanecer en sus propias ramas y PRs.
