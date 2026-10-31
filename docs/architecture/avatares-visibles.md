# Fotos de perfil y contexto de grupo

La foto guardada en `profile-avatars/<uuid>/avatar` se reutiliza en cabecera,
menú, integrantes, invitados, autores/rankings de propuestas, personas de los
gastos y avisos de invitación/deuda. Los iconos de confirmaciones y recordatorios
siguen representando eventos: no se atribuye una foto a una persona inexistente
en su referencia. Sin foto o ante un fallo de Storage se conservan las iniciales.

## Privacidad

El bucket permanece privado y sus políticas SQL no cambian. La ruta POST
`/avatares/` requiere sesión, CSRF y responde sin caché. Acepta un máximo de 100
descriptores por lote. IDs, contextos y nombres del navegador no conceden permiso:

| Contexto | Comprobación con JWT antes de firmar |
| --- | --- |
| Propio | Identidad de la sesión; ignora otro UUID enviado como propio. |
| Grupo | `list_group_members`; pendientes sólo si el actor es owner. |
| Plan | Acceso al plan, integrantes actuales y, si hace falta, autores/participantes históricos visibles. |
| Búsqueda de invitado | RPC existente de búsqueda exacta, autorizada para owner; el resultado debe contener ese UUID. |
| Notificación | Filas del destinatario filtradas por RLS; acreedor explícito o invitador por FK, nunca IDs del payload. |

Sólo después de validar se firma el conjunto de objetos autorizados mediante el
cliente administrativo del servidor. Se necesita porque la policy existente de
Storage sólo permite leer la propia foto. No se concede lectura pública ni se
expone la secret key al navegador. La firma se hace en una única llamada Storage
por lote y las notificaciones/invitaciones se consultan también por conjuntos.
Se descartan rutas no solicitadas y URLs ajenas al objeto/proveedor esperados.

Los enlaces duran cinco minutos. Son capacidades temporales: quien reciba un
enlace firmado puede usarlo hasta su expiración; no hay revocación instantánea
al cambiar membresías. No se guardan en localStorage ni en una caché compartida.
El navegador reutiliza resultados durante cuatro minutos en esa página y vuelve
a consultar al abrir contenido dinámico después del vencimiento. La carga de
fotos no bloquea el render ni afecta a operaciones de negocio.

## Presentación

`components/user_avatar.html` y `avatars.js` se usan en toda la interfaz privada.
El script agrupa identidades repetidas, atiende contenido cargado dentro del
popup y reemplaza las iniciales sólo cuando la imagen terminó de cargar. Usa
`object-fit: cover`, imágenes decorativas y `referrerpolicy=no-referrer`.

Mis Planes resuelve los nombres de grupo en una consulta adicional por página
con JWT/RLS. Las cards muestran «Grupo: …». Notificaciones, calendario, popup y
formularios mantienen el contexto explícito. Si ya no hay acceso al grupo, la
bandeja indica «Grupo: No disponible» sin intentar obtenerlo con privilegios.

En Inicio las letras de los grupos se sustituyen por SVG decorativos con ondas
y geometrías de la paleta existente. La variación es ornamental y no representa
categorías. No se modifica el fondo uniforme de las cards ni se agregan recursos
raster, dependencias o migraciones.

## Verificación

`apps.users.tests.test_visible_avatars` cubre autorización, rechazo de IDs
arbitrarios, lectura JWT, firma por lotes, TTL, fallback, CSRF, sesión y etiquetas.
La suite habitual simula el transporte HTTP y no ejecuta SQL remoto.

Prueba manual: cargar una foto desde Mi perfil; abrir Inicio, Mis grupos,
propuestas, gastos y notificaciones desde otra cuenta autorizada; comprobar la
imagen y que una cuenta sin foto conserva iniciales. Revisar en ambos temas y
en móvil. Las fotos se cargan después del HTML; si Storage está sin configurar
o no disponible, el resto de la página sigue funcionando.
