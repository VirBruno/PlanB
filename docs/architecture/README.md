# Arquitectura inicial

Plan B comienza con un backend Django y una base PostgreSQL. Este bootstrap
prepara la estructura y la configuración para las User Stories del Sprint 0.

- `backend/config/` contiene la configuración global, URLs y entradas ASGI/WSGI.
- `backend/apps/` es el paquete reservado para futuras aplicaciones de dominio;
  todavía no contiene aplicaciones ni modelos de negocio.
- PostgreSQL se configura con variables `DB_*`. El mismo backend sirve para
  una instalación local o un proveedor externo, incluido Supabase, sin SDK ni
  lógica específica del proveedor. `DB_SSLMODE` permite configurar SSL.
- `python-dotenv` carga el `.env` de la raíz. Las variables del proceso tienen
  prioridad. Los secretos y credenciales requeridos no tienen valores por defecto.
- Se conservan las aplicaciones, middleware, validadores y ruta `/admin/`
  estándar generados por Django. No se agregan funcionalidades personalizadas
  de usuarios, registro, login o autenticación.

No se incluyen grupos de dominio, propuestas, preferencias, modelos de negocio,
endpoints propios ni algoritmos de compatibilidad. Tampoco se ejecutan
migraciones como parte del bootstrap: la preparación de una base real se hace
localmente siguiendo el README.

`manage.py check` valida la configuración de Django, pero no demuestra que se
pueda conectar a PostgreSQL. Las migraciones requieren una base accesible.
