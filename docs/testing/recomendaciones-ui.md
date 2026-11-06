# Sugerencias en el detalle del plan · Prompt 2

## 1. Arquitectura frontend

Fragmento Django en el detalle existente, CSS localizado y JavaScript nativo con
fetch. Sin framework ni paquetes nuevos. Se reutilizan el servicio, snapshot,
endpoints y Leaflet 1.9.4 ya instalado. La sección aparece al final del contenido
funcional, después del bloque completo de propuestas del grupo. Es visible sólo con `status=True`,
`selected_proposal_id` y `confirmed_at`, igual que el servicio de recomendaciones.
Organización, inactivos y finalizados no la muestran.

No se consulta Geoapify ni el snapshot al renderizar Django: se agrega únicamente
la condición `show_recommendations`. La confirmación conserva su flujo original.
El título solicitado es «✨ Sugerencias con IA» y el subtítulo aclara que, por
ahora, las sugerencias dependen del tipo de actividad elegida. No se integra IA.

## 2. Archivos creados

- `backend/apps/plans/templates/plans/partials/recommendations.html`.
- `backend/apps/users/static/users/css/plan-recommendations.css`.
- `backend/apps/users/static/users/js/plan-recommendations.js`.
- `backend/apps/plans/tests/test_recommendations_ui.py`.
- `backend/apps/plans/tests/browser_recommendations.py`.
- `docs/testing/recomendaciones-ui.md`.

## 3. Archivos modificados

- `backend/apps/plans/views.py`: booleano de visibilidad, sin red adicional.
- `backend/apps/plans/templates/plans/detail.html`: sección y assets condicionales,
  carga única de Leaflet también cuando no hay propuestas visibles.
- `backend/apps/plans/recommendation_views.py`: respuesta común y POST de reintento.
- `backend/apps/plans/urls.py`: ruta del reintento.
- `docs/testing/recomendaciones.md`: referencia a este bloque.
- `README.md`: actualización del alcance de la experiencia.

## 4. Generación automática

Al abrir el detalle confirmado, JS hace GET al endpoint existente. Ready muestra
el snapshot, incluso vacío. Sólo not_generated provoca un POST automático. Un
registro de inicialización por elemento y mapas de promesas compartidas por URL
evitan inicializaciones/requests concurrentes duplicadas en el documento.
Una generación automática fallida no se repite silenciosamente. La reserva
existente en Supabase sigue protegiendo refresh y consultas de otros miembros.

Failed no reintenta al recargar. Pending muestra estado y un botón «Consultar
estado» que sólo hace GET. No hay polling ni temporizadores de reintento. El
timeout de fetch de 30 segundos sólo limita la espera de esa petición; no cancela
necesariamente el trabajo que ya está ejecutando el servidor.

## 5. Reintento

POST `/planes/<uuid>/recomendaciones/reintentar/`, con sesión, autorización de
plan/grupo y CSRF. Relee el estado autorizado; sólo failed o pending llaman al
mismo `generate_recommendations(..., replace=True)`. Ready no se recalcula.
Si una petición anterior falló antes de crear el snapshot, not_generated usa
la generación normal existente. No se aceptan lugares, categorías, ubicación,
decisión o snapshots del cliente. El form envía exclusivamente CSRF.

La RPC existente impide reclamar pending reciente. La UI ofrece reintento de
pending sólo cuando `updated_at` tiene al menos dos minutos; el reloj del cliente
no decide permisos: la RPC sigue siendo autoridad. No se agrega un timer para
habilitarlo; «Consultar estado» vuelve a evaluar su antigüedad. Un clic inicia
loading y deshabilita el doble envío. Las recomendaciones anteriores permanecen
visibles mientras se reintenta y si falla la consulta/generación.

## 6–7. Mapa, proveedor y atribución

Leaflet 1.9.4 del `node_modules` existente, servido por staticfiles. Un marcador
numerado por recomendación válida, bounds automáticos con zoom máximo inicial
16, y controles estándar. Funciona con uno a cinco resultados. Las coordenadas
deben ser números finitos dentro de rangos geográficos; null/bool/texto no se
convierten a coordenadas. Las cards siguen visibles aunque no tengan punto.

El título de cada card es un botón que destaca el marcador, centra el mapa y
abre su popup. El marcador identifica la card y devuelve foco a su botón.
Popups y cards se crean con nodos DOM y textContent, sin HTML del proveedor.
El mapa es complementario: todos los datos y enlaces están en las cards.

Tiles: `https://tile.openstreetmap.org/{z}/{x}/{y}.png`, sin claves ni billing,
con atribución visible «© OpenStreetMap contributors» y crédito Leaflet.
El referrerPolicy del tile layer permite Referer sin cambiar la política global
del sitio; se respeta la caché normal del navegador, sin prefetch/descargas masivas.
La cartografía conserva el estilo estándar de OSM en ambos temas. Contenedores,
marcadores, controles y popups usan los tokens del tema de Plan B.

Referencias oficiales:
[Leaflet 1.9.4](https://leafletjs.com/reference-1.9.4.html) y
[política de tiles OSM](https://operations.osmfoundation.org/policies/tiles/).
OSM es adecuado para el bajo tráfico académico previsto; no ofrece SLA.

## 8–9. Responsive y temas

Más de 1000 px: listado y mapa en dos columnas con altura equilibrada y scroll
propio del listado. Hasta 1000 px:
cards completas y mapa debajo. Se revisó en 320, 390, 820 y 1440 px, sin scroll
horizontal. En mobile el mapa tiene altura acotada y los enlaces/controles de
acción tienen superficie táctil. Se heredan las variables de la identidad verde
y crema del detalle; dark usa las superficies verdes y acento dorado existentes.
Foco visible, botones/enlaces semánticos, estado aria-live, marcadores numerados
y respeto por prefers-reduced-motion. No hay geolocalización, rutas o tiempos de viaje.

## 10. Presentación y estados

Cards: ordinal, categoría legible, nombre, dirección o ausencia explícita,
distancia geodésica aproximada, horarios textuales y website si están disponibles.
Restaurante/Café/Parque/Área de picnic/Supermercado tienen etiquetas controladas;
lo desconocido se presenta como «Lugar de interés». Distancias en metros o km
con coma decimal. Sin fotos inventadas, rating, reviews, precio ni «abierto ahora».
El área visual abstracta por categoría funciona sin media; puede alojar imágenes
posteriormente cuando exista un contrato de fuente válida, sin rediseñar la card.
Actualmente no carga ninguna imagen de lugares.

- Loading: spinner discreto y texto de consulta/búsqueda.
- Ready con lugares: cards, mapa y dos acciones externas diferentes.
- Ready vacío: «No encontramos lugares compatibles cerca del punto elegido».
- Failed: mensaje amigable, reintento explícito y snapshot anterior si existe.
- Pending: procesamiento sin otro POST automático; consulta manual del estado.
- Storage/network/respuesta inválida: error localizado; conserva cards previas.
- Mapa/tiles no disponibles: explicación local, cards y enlaces siguen usables.
- Plan reabierto/finalizado mientras se consulta: mensaje de disponibilidad sólo
  para encuentros confirmados activos, sin recomendaciones obsoletas.
- Sin JS: aviso noscript; el resto del detalle sigue disponible.

Los enlaces «Ver en Google», «Abrir en Maps» y «Sitio web» utilizan las URLs
normalizadas existentes. Se permite sólo HTTP/HTTPS sin credenciales; las dos
acciones Google requieren el host HTTPS www.google.com. Usan nueva pestaña y
noopener/noreferrer. No se asegura que Google muestre una ficha comercial.

## 11. Consumo y seguridad

El frontend no conoce la clave de Geoapify, ni consulta al proveedor. Ready
reutiliza el snapshot. Zoom, selección, movimiento y navegación externa no
solicitan recomendaciones. Los únicos POST son la primera generación automática
y el reintento explícito. El nuevo endpoint reutiliza la autorización del
servicio y no ejecuta URLs arbitrarias. No cambia RLS/RPC/ranking/persistencia
ni el límite de candidatos. No hay dependencias, claves o billing adicionales.

## 12. Pruebas

12 tests Django nuevos cubren sección condicional, carga única de Leaflet, detalle
sin consulta a recomendaciones, ausencia de secretos, permisos, método/CSRF,
reintento por estado y fallo de almacenamiento que no bloquea el detalle.
Se mantienen los 57 tests previos de recomendaciones.

La herramienta opcional Chrome sigue las convenciones existentes: TestClient
Django, fixtures, estáticos reales, CDP con websockets ya instalado. Leaflet es
real y las respuestas de recomendaciones/tiles son simuladas; DNS externo queda
bloqueado. Cubre el ciclo JS, doble inicialización/componentes, estados,
opcionalidad, categorías/distancias, enlaces/escape, uno a cinco marcadores,
interacción, fallo de mapa, ausencia de polling, layout y cambio de tema.
Guarda informes JSON y capturas temporales; la cuadrícula del mapa en estas
capturas es un tile de pruebas, no cartografía consultada a OSM.

Desde `backend` con el virtualenv activado:

```text
python manage.py test apps.plans.tests.test_recommendations apps.plans.tests.test_recommendations_ui --noinput
python -m apps.plans.tests.browser_recommendations
python manage.py check
python manage.py test
```

## 13–15. Resultados

Tests específicos: `Ran 69 tests in 0.283s` · `OK`.

Chrome headless con Leaflet real y respuestas/tiles simulados:

```text
{"ok": true, "width": 320, "checks": 73}
{"ok": true, "width": 390, "checks": 73}
{"ok": true, "width": 820, "checks": 73}
{"ok": true, "width": 1440, "checks": 73}
```

Se revisaron capturas de desktop claro y mobile oscuro. Informes JSON y ocho
capturas light/dark quedaron en la carpeta temporal
`C:\Users\Administrator\AppData\Local\Temp\planb-recommendations-browser-xctdqib_`.

Resultado exacto de `python manage.py check`:

```text
System check identified no issues (0 silenced).
```

Resultado exacto de `python manage.py test`:

```text
Ran 698 tests in 26.820s

OK
```

La suite conserva los 686 existentes y agrega 12; no se modificaron tests
previos. `git diff --check` terminó sin errores de whitespace (sólo avisos de
normalización LF/CRLF de Git). Las pruebas Python bloquearon red y DNS.

## 16. SQL estable

No se creó ni modificó ninguna migration, ni RLS/RPC. La migration ya aplicada
`202611050001_plan_recommendations.sql` se conserva. Su SHA256 previo al bloque:
`755CF9901B8F4AF1AA63450CF0C87068E604736A677BF6524D5D3A0321C85866`.
El hash final coincide exactamente y `git diff --exit-code -- supabase/migrations`
terminó con código 0, sin diferencias.
No se ejecutó SQL remoto ni se modificó `.env`.

## 17. Prueba manual

1. Reiniciar Django y hacer recarga completa para cargar los nuevos estáticos.
   No hay SQL ni instalación de dependencias pendiente.
2. Abrir un plan confirmado activo con una actividad reconocida: el módulo
   consulta el snapshot y genera si falta. Ready previo no vuelve a Geoapify.
3. Desde otro miembro, comprobar las mismas cards, orden y coordenadas.
4. Seleccionar títulos y marcadores; abrir Google/Maps/website y comprobar que
   el resto del plan permanece intacto. Probar también teclado y foco visible.
5. Probar mobile y los dos temas. En una sesión con red normal, comprobar tiles
   OSM y atribución; la prueba automatizada usa tiles simulados.
6. En entorno controlado con failed, usar Reintentar sin reconfirmar el plan.
   Pending reciente sólo consulta estado; un pending viejo permite reintentar.
7. Verificar ready vacío, error de red, horarios/website ausentes y proveedor
   caído. Los errores deben quedar dentro del módulo y conservar datos previos.
8. Comprobar que organización, inactivos y finalizados no muestran la sección.

## 18. Limitaciones y decisiones para revisar

La generación arranca al abrir el detalle confirmado, no al confirmar ni con
un worker. No hay actualización automática de un pending de otro miembro:
se usa Consultar estado o recarga para cuidar consumo. La política de dos
minutos sigue siendo la ya implementada en SQL. No se muestra el punto base
como marcador: el snapshot público actual no lo incluye y no se cambia su
contrato. Sin fotos y sin Gemini; el frontend es independiente del resolver.
La validación local usa servicios simulados y no demuestra disponibilidad de
tiles externos o del proveedor real. No hubo commit ni push.

## Ajuste posterior: jerarquía, variedad y equilibrio desktop

La inclusión del fragmento de sugerencias se movió después del cierre de la
sección de propuestas, con sus elecciones, acciones y mapas. No hay HTML
duplicado ni cambios de backend para alterar ese orden.

En desktop, listado y panel del mapa comparten una altura entre 420 y 620 px,
adaptada al alto del viewport. El listado tiene scrollbar, padding para el foco
y navegación por teclado; desplazar cards no desplaza el mapa ni solicita datos.
En mobile/tablet se elimina el límite y el scroll interior: todas las cards
permanecen en el flujo normal antes del mapa. Se mantienen colores y atribución.

La búsqueda sigue haciendo UNA solicitud Places, con máximo 20 candidatos y el
mismo límite exterior de 5 km. Cambia únicamente el bias de proximity a circle,
para preferir una zona de hasta 600 m en vez de ordenar los candidatos sólo por
proximidad al centro. Si el criterio recibido tiene un radio menor, se respeta.
Referencia: [bias circular de Geoapify](https://apidocs.geoapify.com/docs/places/).

Ranking dentro de una zona razonable:

1. Descartar categorías incompatibles y lugares fuera del radio autorizado,
   y deduplicar IDs antes de evaluar la cantidad disponible.
2. Usar los candidatos dentro de 600 m. Si no alcanzan cinco, ampliar la zona
   sólo hasta el quinto candidato compatible disponible, dentro del radio
   exterior. Si hay menos de cinco, conservar los que haya.
3. Priorizar compatibilidad exacta, luego cantidad de datos objetivos presentes
   entre dirección, website, horarios y subcategoría específica compatible.
   Nombre/ID/coordenadas válidos ya son requisitos de la normalización existente.
4. Entre opciones con la misma compatibilidad y completitud, favorecer las que
   estén al menos a 150 m de las ya seleccionadas; después, menor distancia al
   punto del plan e ID estable. Es una preferencia suave: no excluye candidatos
   agrupados ni impone una distancia mínima respecto del punto base.

Así un local documentado a 420 m puede preceder a uno incompleto a 46 m, y una
opción equivalente en otra parte de la zona puede entrar antes que otra pegada
a las seleccionadas. Esto NO afirma mejor calidad comercial, reputación o
popularidad. No se guardan ratings ni nuevos scores; se conserva el snapshot.

La variedad sólo puede elegirse entre los candidatos realmente devueltos en esa
única llamada. Si no hay alternativas o el proveedor devuelve un conjunto
concentrado, se conservan los lugares disponibles sin inventar resultados ni
hacer búsquedas extra. Los snapshots ready existentes NO se recalculan: esta
política afecta próximas generaciones o recálculos explícitos autorizados.
Para probarla sin alterar snapshots actuales, usar un nuevo plan confirmado.
No se modifican RLS, RPC, SQL, endpoints, persistencia, consumo por generación,
Gemini ni `.env`. No hay commit ni push.

Validación del ajuste: tests específicos `Ran 80 tests in 1.325s`, `OK`.
Suite completa: `Ran 709 tests in 21.708s`, `OK` (11 tests nuevos sobre 698).
Check: `System check identified no issues (0 silenced).`
Chrome: 75 verificaciones OK en cada ancho 320/390/820 y 79 OK en desktop 1440,
incluyendo orden, scroll interno, equilibrio de alturas, teclado y ausencia de
requests al desplazar cards. Capturas e informes temporales:
`C:\Users\Administrator\AppData\Local\Temp\planb-recommendations-browser-ohf1c01r`.
El runner ahora tolera el bloqueo transitorio del archivo DevToolsActivePort
de Chrome en Windows. `git diff --check` no encontró errores de whitespace;
`.env` y migrations no tienen diferencias y el SHA256 de la migration sigue
coincidiendo con el registrado arriba. Sin llamadas reales a Geoapify en tests.
