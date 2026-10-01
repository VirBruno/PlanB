# Crear grupos: validación y despliegue

## Alcance

Un usuario autenticado crea un grupo con nombre y descripción opcional. La RPC
`public.create_group(text,text)` inserta el grupo y su owner en una transacción.
El dashboard muestra pertenencias mediante RLS, con páginas de 12 grupos.
El detalle incluye nombre, descripción, fecha y rol propio. El owner puede editar nombre/descripción y eliminar el grupo previa
confirmación. No hay invitaciones, propuestas, votaciones ni otras funciones.

## Validación offline

En PowerShell, desde la raíz con el entorno virtual activado:

```powershell
cd backend
python manage.py test
python manage.py check --settings=config.settings_test
python manage.py makemigrations --check --dry-run --settings=config.settings_test
python -m pip check
```

Los tests de grupos usan el SDK real con transporte HTTP simulado o mocks de
servicios. SQLite sólo almacena sesiones técnicas; nunca grupos o membresías.
El runner bloquea la red. Esta suite no demuestra RLS ni atomicidad PostgreSQL.

## Paso manual en el proyecto Supabase compartido

1. Confirmar en el Dashboard que se seleccionó el proyecto usado por
   `SUPABASE_URL`. No copiar claves a SQL, PRs ni capturas.
2. Confirmar que ya se aplicaron `202609290001_django_infrastructure.sql`
   y `202609290002_profiles.sql`. Si la autenticación ya funciona con esos
   objetos, no volver a ejecutarlas: no son scripts idempotentes.
3. En **SQL Editor → New query**, con el rol administrador (`postgres`),
   copiar y ejecutar el contenido completo de:
   `supabase/migrations/202609300001_groups.sql`.
   Ejecutarlo una sola vez. Contiene BEGIN/COMMIT y recarga del esquema PostgREST.
   Si existen tablas o una función del mismo nombre, detenerse y revisar antes
   de aplicarlo; no borrar ni sobrescribir datos para resolver la colisión.
4. Verificar que `public` esté expuesto por Data API y que
   `django_internal` y `planb_private` continúen fuera de los esquemas expuestos.
   La migración activa RLS, restringe grants y habilita únicamente la RPC de
   creación para `authenticated`. No requiere cambiar claves, Auth ni SMTP.
5. Mantener Django con `planb_django_runtime`. Esta US no requiere ejecutar
   `manage.py migrate`, dar permisos de dominio al runtime ni utilizar el
   rol migrador desde el servidor.

Alternativa al SQL Editor, con conexión administrativa preparada de manera
segura y sin contraseñas en la línea de comandos, desde la raíz:

```powershell
psql -v ON_ERROR_STOP=1 -f supabase/migrations/202609300001_groups.sql
```

Usar **una** de las dos alternativas. Crear archivos locales no aplica el SQL
remoto: sin ese paso, las pantallas mostrarán un error controlado de servicio.

## Integración SQL

Sólo en una instancia Supabase **de prueba autorizada**, con las tres
migraciones aplicadas, ejecutar como administrador:

```powershell
psql -v ON_ERROR_STOP=1 -f supabase/tests/groups.sql
```

También se puede ejecutar su contenido completo en SQL Editor. El script crea
usuarios ficticios directamente en Auth, perfiles y membresías de prueba;
no envía correo. Crea temporalmente un trigger para hacer fallar el segundo
INSERT y comprobar rollback del grupo. Valida acceso como owner, miembro y
extraño; permisos, constraints y eliminación de fixtures. Todo termina con
ROLLBACK, incluyendo el trigger. No usar en un entorno con triggers ajenos
que tengan efectos externos. El resultado esperado es `OK: grupos...`.

## Prueba manual de la US

Con la migración aplicada al proyecto compartido y el entorno existente
configurado, activar `.venv` y ejecutar:

```powershell
cd backend
python manage.py check
python manage.py runserver
```

1. Iniciar sesión con una cuenta confirmada y abrir `/dashboard/`.
2. Sin grupos: comprobar el empty state y **Crear mi primer grupo**.
3. Abrir **Crear grupo**. Enviar nombre vacío: debe aparecer validación.
4. Crear un nombre válido, con o sin descripción. Debe abrir
   `/grupos/<uuid>/`, mostrar mensaje de éxito y rol **Owner**.
5. Volver a **Mis grupos** y recargar. El grupo debe seguir apareciendo.
6. En SQL Editor, comprobar las filas reales usando el UUID mostrado
   en la URL (reemplazar el marcador):

   ```sql
   select id, name, description, created_by, created_at, updated_at
   from public.groups where id = '<UUID_DEL_GRUPO>'::uuid;

   select group_id, user_id, role, joined_at
   from public.group_members where group_id = '<UUID_DEL_GRUPO>'::uuid;
   ```

   Debe existir un owner cuyo `user_id` sea igual a `created_by`.
7. Con otra cuenta que no pertenece al grupo, comprobar que no aparece en
   su dashboard y que acceder a la URL de detalle responde 404.
8. Sin sesión, abrir creación/detalle: debe redirigir a login. Tras entrar,
   debe continuar en la ruta interna solicitada.
9. Verificar nombre y descripción con caracteres especiales: se muestran
   como texto. Probar límites de 100/1.000 caracteres.
10. Confirmar login/registro a 1366×768 y 1440×900, mobile de 320/390 px,
    errores, navegación con Tab y mostrar/ocultar contraseña. No debe existir
    recorte de campos ni scroll horizontal. Con errores o zoom puede haber
    scroll vertical; no se oculta contenido.
11. La pertenencia como `member` se valida con fixtures en
    `supabase/tests/groups.sql`; esta entrega no permite invitar ni agregar
    miembros desde la aplicación.

## Decisiones y límites

- No hay reintento automático de creación. Si la respuesta se pierde después
  del commit, revisar **Mis grupos** antes de reenviar. La operación es atómica,
  no idempotente; el botón deshabilitado evita dobles clics habituales.
- Un índice parcial admite como máximo un owner. La RPC garantiza que al
  crear exista exactamente uno. Sólo un administrador externo podría
  modificar ese estado: los roles de aplicación no tienen escritura directa.
- Eliminar un creador de Auth está bloqueado mientras posea grupos
  (`ON DELETE RESTRICT`). La eliminación del grupo ya está disponible para su owner; transferencia
  y eliminación de cuentas quedan para otra US.
- Las políticas de membresía permiten leer únicamente la membresía propia.
  Un futuro listado de integrantes deberá ampliar ese permiso deliberadamente.
- El acceso al token reutiliza el servicio de sesiones y su bloqueo/refresh.
  Un JWT rechazado durante una operación invalida la sesión local y conduce
  a login. Ningún token se incorpora al contexto de templates.
- La RPC es SECURITY DEFINER de forma deliberada; el owner SQL es el
  administrador que aplica la migración. Tiene search_path vacío, referencias
  cualificadas, sin SQL dinámico, identidad desde auth.uid() y EXECUTE limitado.
  Revisar cualquier alerta del asesor de seguridad sobre esa función según
  este contrato, sin desactivar controles globales.

## Edición y eliminación del grupo

La migración adicional es `supabase/migrations/202609300002_group_management.sql`.
Aplicar **una sola vez** en el SQL Editor del proyecto compartido, como
administrador (`postgres`), después de `202609300001_groups.sql`. No repetir
migraciones anteriores ni cambiar `.env`. Si ya existen funciones con esas
firmas, revisar el estado antes de ejecutar; no borrar objetos para forzarla.

Alternativa desde la raíz con una conexión administrativa segura preparada:

```powershell
psql -v ON_ERROR_STOP=1 -f supabase/migrations/202609300002_group_management.sql
```

Esta migración agrega únicamente `update_group(uuid,text,text)` y
`delete_group(uuid)`, sus permisos y la recarga del esquema PostgREST. No
agrega columnas, modelos Django ni dependencias. Se mantienen RLS, el rol
runtime y los permisos de lectura. No concede UPDATE/DELETE directo.
Las llamadas usan publishable key + JWT; nunca la secret key.

Ambas RPC obtienen auth.uid(), comprueban role='owner' en group_members y
bloquean grupo y membresía durante la escritura. No confían en created_by
como autorización ni aceptan user_id/owner_id/roles del cliente. Son
SECURITY DEFINER con search_path vacío y objetos cualificados. Sólo
authenticated tiene EXECUTE. El owner SQL debe seguir siendo el administrador
confiable que aplica la migración.

update_group valida igual que create_group, modifica exclusivamente name y
description, y deja updated_at al trigger existente. delete_group elimina
sólo el UUID autorizado; las membresías se eliminan por la FK CASCADE.

### Prueba manual

1. Entrar como owner, abrir un grupo y pulsar **Editar grupo**.
2. Comprobar campos precargados. Guardar nombre y descripción; debe regresar
   al detalle con los cambios. Recargar dashboard para ver el nuevo nombre.
3. Probar nombre vacío, más de 100 caracteres y descripción de más de 1.000.
   Deben rechazarse; los valores escritos se conservan. Una descripción vacía
   es válida.
4. En detalle pulsar **Eliminar grupo**: abre
   `/grupos/<uuid>/eliminar/confirmar/`. Deben verse el nombre, la advertencia
   de eliminación permanente y **Cancelar y volver**. Este GET no elimina.
5. Cancelar y verificar que sigue existiendo. Volver a la confirmación y
   pulsar **Sí, eliminar grupo**. El formulario hace POST con CSRF a
   `/grupos/<uuid>/eliminar/` y redirige a dashboard. El grupo desaparece
   y su antiguo detalle devuelve 404.
6. Comprobar en SQL Editor, sustituyendo el UUID:

   ```sql
   select * from public.groups where id = '<UUID_ELIMINADO>'::uuid;
   select * from public.group_members where group_id = '<UUID_ELIMINADO>'::uuid;
   ```

   Ambas consultas deben devolver cero filas.
7. Como miembro no owner, no aparecen las acciones; intentar las rutas de
   edición/confirmación o POST de eliminación devuelve 403. Como extraño,
   devuelve 404. Sin sesión, se redirige a login. Abrir la ruta de eliminación
   por GET devuelve 405; POST sin CSRF devuelve 403.
8. Si falla la conexión al guardar/eliminar, se indica que el resultado no
   pudo confirmarse. Revisar detalle/dashboard antes de repetir: no hay
   reintentos automáticos ni garantía de idempotencia.

### Tests

La suite offline agrega `test_management.py` y contratos en
`test_sdk_contracts.py`: owner, miembro, extraño, anónimo, CSRF, métodos,
validaciones, identidad falsificada, pérdida de permisos entre lectura y
escritura, errores de red y recorrido edición → eliminación → dashboard.
Los tests anteriores se conservan sin debilitar sus aserciones.

En una instancia Supabase **de prueba autorizada**, con las cuatro
migraciones aplicadas, ejecutar como administrador:

```powershell
psql -v ON_ERROR_STOP=1 -f supabase/tests/group_management.sql
```

Puede ejecutarse también su contenido completo en SQL Editor. Valida las
RPC con los roles reales, rechazo de miembro/extraño/anónimo, campos protegidos,
límites, permisos mínimos y CASCADE. Incluye un cambio de owner sólo como
fixture administrativa para comprobar que la autorización se basa en el rol
actual. La aplicación no implementa transferencia. Todas las fixtures se
revierten con ROLLBACK. Los mocks offline no sustituyen esta prueba.

### Ajustes visuales

Se conservan dimensiones compactas, estructura y paleta. Hay fondos
radiales sutiles, sombras y elevación breve de botones/cards, feedback al
presionar, foco visible y tarjetas enlazadas completas. La acción destructiva
reutiliza el color de peligro existente. No hay animaciones continuas,
dependencias ni cursores personalizados. prefers-reduced-motion desactiva
transiciones y desplazamientos interactivos; las pantallas siguen utilizables
sin JavaScript.
