# UNO entre integrantes

## Cambio solicitado: elegir después de ganar

Este cambio todavía no está implementado ni activado. Una persona podrá jugar sin haber creado ni seleccionado una propuesta propia. Si gana sin tener una propuesta representada, conservará el derecho exclusivo de elegir una propuesta existente o crear una nueva completa para confirmar el plan. Una victoria con propuesta representada confirmará esa propuesta automáticamente; el abandono da la victoria a la otra persona. Si gana el bot no se confirma una propuesta.

Antes de iniciar una partida, todas las propuestas que existan en el plan deben tener fecha, hora y ubicación. Si aún no hay propuestas, se podrá jugar y la persona ganadora podrá crear una al finalizar. La selección pendiente debe sobrevivir a recargas y evitar que otra partida confirme el plan mientras se resuelve. Hay que volver a validar las propuestas al confirmar para manejar cambios durante la partida.

El borrador anterior `uno_ganador_automatico.sql` y su prueba transaccional quedaron bloqueados intencionalmente: no contemplan esta elección posterior y no se deben ejecutar. `uno_validar_propuestas.sql` contiene únicamente una función privada preliminar para comprobar fecha y ubicación; aún no está instalada ni integrada en el juego.

## Lo que ya existe

- Las invitaciones a grupos se crean y responden mediante funciones de Supabase. Las notificaciones son privadas y se muestran en la bandeja existente.
- UNO contra el bot guarda en Supabase un estado privado por propuesta y jugador. El motor actual ejecuta automáticamente el turno del bot, por lo que no alcanza con compartir esa partida entre dos personas.
- Las victorias de UNO se registran por partida y propuesta; la confirmación del plan vuelve a comprobar la clasificación en la base de datos.

## Primera versión propuesta

1. Un integrante elige cualquier propuesta vigente del plan de combate para representarla e invita a otro integrante del mismo grupo.
2. La persona invitada recibe una notificación con el plan y puede aceptar o rechazar. Al aceptar, elige qué propuesta representar, incluso si no creó una propia. El plan debe seguir abierto y ambos deben seguir en el grupo.
3. Al aceptar se inicia una partida compartida. Cada persona ve sus cartas, la cantidad de cartas del rival, el descarte, el color y a quién le toca.
4. Cada acción se procesa en Supabase con la identidad del integrante. La base valida la partida, el turno, la carta y el estado del plan, y registra una victoria como máximo una vez.
5. Recargar la página o perder la conexión conserva la partida. Si alguien confirma que abandona, gana la otra persona y suma un punto para la propuesta que eligió.

## Reglas acordadas

- Cada participante elige la propuesta del plan que representa; puede elegir la propia o la de otra persona.
- Una victoria, incluida la producida por un abandono confirmado, suma un punto para la propuesta elegida por la persona ganadora.
- Una desconexión o recarga conserva la partida y no cuenta como abandono.

## Avance técnico

`supabase/drafts/uno_multijugador_reglas.sql` contiene un borrador de almacenamiento privado, turnos y vistas para dos personas. La prueba `supabase/tests/uno_multijugador_transaction.sql` pasó en Supabase y revirtió la instalación temporal.

`supabase/drafts/uno_multijugador_invitaciones.sql` prepara la invitación, la elección de propuestas y la aceptación o el rechazo. La prueba reversible `supabase/tests/uno_multijugador_invitaciones_transaction.sql` pasó en Supabase y revirtió los cambios.

`supabase/drafts/uno_multijugador_partidas.sql` prepara las jugadas compartidas, el historial visible y el resultado, incluido el abandono. La prueba `supabase/tests/uno_multijugador_partidas_transaction.sql` pasó en Supabase y revirtió sus datos.

`supabase/drafts/uno_multijugador_listado.sql` prepara la consulta privada de invitaciones y partidas. La prueba integral `supabase/tests/uno_multijugador_integracion_transaction.sql` pasó en Supabase y revirtió la instalación temporal de los cuatro borradores. Después se ejecutó `supabase/uno_multijugador_instalacion_preparada.sql`: los objetos quedaron instalados, con el acceso de integrantes todavía deshabilitado.

La interfaz y las rutas de Django están preparadas detrás de `UNO_MULTIPLAYER_ENABLED`, desactivado por defecto. Para usarlas también debe estar activado `UNO_SUPABASE_ENABLED`. Además de las pruebas simuladas, se confirmó una partida funcional desde dos cuentas del mismo grupo.

`supabase/uno_multijugador_instalacion_preparada.sql` reúne los borradores. La prueba `supabase/tests/uno_multijugador_instalado_transaction.sql` pasó en Supabase y revirtió los datos y permisos temporales. Después se ejecutó `supabase/uno_multijugador_activacion_preparada.sql`, que habilitó las funciones para integrantes autenticados. La comprobación de permisos `supabase/tests/uno_multijugador_activado_preflight.sql` pasó.

## Implementación y comprobaciones

- Usar una entidad propia para la invitación de juego; `notifications.invitation_id` pertenece a las invitaciones de grupo y no debe reutilizarse para otro tipo. Incorporar un tipo de notificación de juego y ampliar su lectura y presentación.
- Mantener el mazo y las dos manos en almacenamiento privado. Las respuestas nunca deben contener la mano rival ni el orden del mazo; comprobar identidad y pertenencia en cada lectura y acción.
- Serializar invitaciones, acciones, victoria y cierre del plan de manera transaccional, respetando el orden de bloqueos existente. Gestionar repeticiones, doble aceptación y jugadas simultáneas sin duplicar resultados.
- Probar invitación, rechazo, aceptación, privacidad, turnos, robo, cartas especiales, cierre, reconexión, permisos y concurrencia. Probar después desde dos sesiones reales con usuarios distintos.
- Preparar y verificar cualquier migración antes de aplicarla al proyecto de Supabase. No sustituir ni interrumpir UNO contra el bot.
