# Resultados de UNO: preparación

La versión actual de UNO contra el bot funciona como prueba y guarda el estado
en la sesión de Django. No registra victorias ni interviene en la elección del
plan. Esta nota describe el trabajo pendiente; no habilita ningún cambio en la
base de datos.

## Comportamiento acordado

- Una victoria suma un punto a la propuesta con la que se jugó.
- Las victorias se acumulan y se muestran por propuesta; un empate comparte
  posición y no produce una propuesta líder.
- El administrador puede confirmar una propuesta cuando existe un líder único
  y se cumplen los requisitos actuales del encuentro.
- Un resultado solo puede contarse una vez. Perder o abandonar no suma puntos.

## Requisitos para implementar el guardado

El estado actual vive en la sesión del servidor, mientras que las operaciones de
negocio del proyecto se realizan mediante la Data API de Supabase y el JWT del
integrante. Una función pública que reciba «gané» o un puntaje no es suficiente:
el usuario podría invocarla directamente sin haber ganado.

La solución debe validar la partida en un componente confiable y registrar su
identificador único y su resultado en una operación atómica. También debe
comprobar membresía, autoría de la propuesta, método de elección y estado del
plan. La lectura de resultados debe respetar RLS y la confirmación debe volver
a comprobar el líder dentro de la transacción, sin confiar en la página.

Antes de activarla hacen falta pruebas de victoria, derrota, repetición,
solicitudes simultáneas, empate, permisos y plan confirmado. También habrá que
definir cómo compartir la validación con las futuras partidas por invitación.
La migración `202611110001_uno_victory_storage.sql` prepara únicamente una tabla
privada para resultados, sin acceso desde clientes ni una operación para escribir
victorias. La prueba SQL `uno_victory_storage.sql` cubre permisos y duplicados.
Ninguna de las dos se ha ejecutado contra Supabase. Siguen pendientes la
validación confiable de partidas, la conexión de la tabla, las demás pruebas y
la confirmación del resultado.

El borrador `202611110002_uno_confirmation_draft.sql` prepara la comprobación
transaccional de un líder único y las notificaciones. No concede permiso de
ejecución a los roles de la aplicación; requiere pruebas de integración antes
de habilitarse. El futuro registro de victorias debe bloquear primero el grupo y
después el plan, igual que la confirmación, para evitar carreras entre ambos.

## Comprobación local

Las dos migraciones y `uno_victory_storage.sql` se ejecutaron en PostgreSQL 18
temporal, con datos ficticios y un esquema reducido que simula las tablas y roles
necesarios. Pasaron los casos incluidos de permisos, duplicados, empate, líder y
notificaciones. Esa comprobación no incluye PostGIS real, las políticas RLS ni
los servicios de Supabase: sigue pendiente la integración completa antes de
habilitar el guardado o aplicar cambios al proyecto de la aplicación.

La migración `202611110003_uno_rules_draft.sql` prepara funciones privadas para
validar cartas, robar y resolver una jugada. No expone una API ni guarda
resultados. `uno_rules_draft.sql` contiene las pruebas SQL correspondientes.

La migración `202611110004_uno_game_draft.sql` prepara el reparto, la vista sin
cartas ocultas, las acciones y los turnos del bot. `uno_game_draft.sql` propone
comprobar partidas completas y conservación de las cartas. Estos borradores aún
no están conectados a la aplicación. La prueba transaccional de reglas y
partidas completas se ejecutó en Supabase y terminó con rollback; todavía faltan
las pruebas de integración de permisos y registro de victorias.

La migración `202611110005_uno_session_draft.sql` prepara el estado privado de
partida y dos funciones para jugar y consultar la clasificación. El borrador
registra la victoria en la misma transacción que la última jugada, verifica la
pertenencia al grupo y la autoría de la propuesta, y devuelve solo la información
visible. No concede permisos de ejecución.
`uno_session_draft.sql` prepara casos de privacidad, validación y duplicados.
Se ejecutaron en Supabase las pruebas transaccionales de permisos, integración y
acceso autenticado, con datos ficticios y rollback. La prueba autenticada
prepara la última jugada como administrador para comprobar el registro. Sigue
pendiente una prueba de solicitudes concurrentes y de la interfaz conectada.
La instalación de las primeras cinco migraciones se ejecutó en Supabase; sus
funciones todavía no estaban habilitadas para integrantes al registrar esa etapa.

El adaptador `uno_supabase.py` y la ruta alternativa de Django están preparados
para llamar al RPC con el JWT del integrante y filtrar la respuesta. La ruta
está desactivada por defecto (`UNO_SUPABASE_ENABLED`); no debe habilitarse hasta
revisar las migraciones y probar los RPC. El adaptador, la clasificación y la
confirmación de Django tienen pruebas simuladas; la opción actual de prueba
continúa usando la sesión de Django mientras no se active la variable de proceso.

`supabase/uno_instalacion_preparada.sql` agrupa las primeras cinco migraciones
en una sola transacción. Instala los objetos sin dar permisos de ejecución a
integrantes. `202611110006_uno_activation_draft.sql` prepara esos permisos; la
variable opcional `UNO_SUPABASE_ENABLED` permite usar la integración desde el
proceso de Django, sin modificar `.env`.

La actualización `202611110007_uno_game_events.sql` conserva las reglas y agrega
eventos visibles para jugadas, robos y avisos de UNO. La prueba transaccional
`uno_events_transaction.sql` pasó en Supabase y revirtió los cambios. Después se
aplicó la actualización de forma permanente; queda pendiente verificar la
presentación de los eventos en una partida real desde la interfaz.
