# Revisión de la aplicación electoral — versión 3

Fecha: 8 de octubre de 2026. Fuente: código, SQLite y cartografía del ZIP proporcionado.

## Resultado

La aplicación ya integra consultas, perfiles, gráficos, mapas, prensa, escucha social, redacción y seguimiento de tareas.
Tiene buenas bases: cálculos deterministas, consultas SQL parametrizadas, roles comprobados en el servidor,
contraseñas con hash, sesiones propias por usuario y persistencia de publicaciones y análisis sociales.
La prioridad es asegurar coherencia de datos, configuración y trazabilidad antes de ampliar funciones.

Se corrigieron únicamente el comparativo del perfil territorial y su configuración de Groq, junto con su documentación y pruebas.
El resto de los puntos de este informe son hallazgos o propuestas pendientes, no funciones ya implementadas.

## Correcciones entregadas

1. El comparativo deja de agrupar PAN/PRI/PRD y MORENA/PT/PVEM. Mantiene cada opción registrada por separado en cálculos, gráfico, diferencias, ganadores por sección, histórico y ficha de redacción.
2. El frontend utiliza `partidos` y `por_partido`; muestra todas las opciones en la tabla de diferencias, en lugar de limitarla a cuatro bloques.
3. Las coaliciones se conservan como opciones independientes. Un registro `PAN_PRI_PRD` no se atribuye a PAN, PRI o PRD. No se modificaron los votos ni las bases originales.
4. La clave recibida estaba en `.env.example`; la aplicación solo leía `.env`. Se conserva la configuración en `.env`, y el ejemplo queda sin claves. La configuración se carga antes de importar módulos internos que leen variables de entorno.
5. El cliente principal establece un tiempo de espera de 45 segundos por intento y un reintento. El análisis territorial distingue clave rechazada, límite de uso y tiempo de espera. Un reintento puede ampliar el tiempo total de la solicitud.
6. Los mensajes explican qué archivo configurar y que debe reiniciarse el servidor. Al reinicializar el estado se limpian errores anteriores.
7. La fecha de generación incluye zona horaria. La ficha advierte que diferencias agregadas no demuestran transferencias individuales de votos.

### Límite de la vista por partido

La base contiene partidos individuales y combinaciones de coalición. Quitar los bloques no elimina esas combinaciones.
Si se necesita que únicamente aparezcan partidos individuales, hay que definir una capa de resultados derivados:
conservar datos originales, elegir y documentar un método de reparto, comprobar conservación del total y señalar qué votos son estimados.
Un reparto igualitario o proporcional es una hipótesis analítica; no debe presentarse automáticamente como resultado oficial.
También debe distinguirse una combinación marcada en boleta de votos ya agregados por candidatura para evitar duplicarlos.

## Datos comprobados

| Indicador | Resultado |
|---|---:|
| Registros de resultados | 210,105 |
| Año presente | 2024 |
| Tipos de elección | 5 |
| Registros por tipo de elección | 42,021 |
| Opciones distintas, incluidos auxiliares | 23 |
| Combinaciones sección-elección con total distinto de la suma, según auditoría existente | 196 |
| Combinaciones sección-elección con emitidos mayores que lista nominal | 53 |
| Secciones con lista nominal distinta entre tipos de elección | 7 |
| Tamaño del GeoJSON | 5,907,761 bytes |

La auditoría no detectó duplicados de su clave actual ni conflictos de jerarquía territorial en esta base.
Los 196 descuadres usan el umbral de más de un voto de `limpieza.auditar`; el perfil usa otra tolerancia,
de hasta 0.5 % del total. Por ello ambos pueden mostrar estados de calidad diferentes. Un aviso no determina
por sí solo cuál es la causa: debe contrastarse con la fuente. La participación superior al 100 % requiere
revisar captura y casillas especiales; no debe suponerse que todo caso sea una casilla especial.
No hay evidencia de evolución temporal hasta incorporar otros años.

## Mejoras por prioridad

| Prioridad | Área y evidencia en el código | Mejora concreta | Beneficio |
|---|---|---|---|
| Alta | `preparar_base` descarga Google Sheets en cada arranque y `guardar_sqlite` reemplaza la tabla | Separar iniciar de sincronizar; iniciar con la última base validada; actualización explícita con timeout, validación, copia y sustitución atómica | La app abre sin depender de Google y evita reemplazar datos válidos por una actualización defectuosa |
| Alta | Descuadres y tolerancias diferentes entre `limpieza`, `territorio` y análisis | Unificar reglas de calidad y denominadores; mostrar faltantes, porcentaje de cobertura, fuente y fecha en cada vista/exportación | Explicar por qué una cifra es utilizable o necesita revisión |
| Alta | Coaliciones registradas de forma diferente entre elecciones | Catálogo por año, elección y candidatura; conservar originales y documentar cualquier reparto derivado | Comparaciones interpretables sin atribuciones arbitrarias |
| Alta | `analisis_territorial._ganadores_seccion` rompe empates por orden alfabético | Marcar empate y excluirlo o contabilizarlo aparte en cambios de ganador; extender regla a mapas y municipios | Evitar declarar un ganador que los datos no determinan |
| Alta | `auth` protege mutaciones por origen, pero CORS permite cualquier origen con credenciales | Restringir CORS a orígenes autorizados o retirarlo si todo opera en el mismo origen; revisar GET autenticados desde otros orígenes | Reducir exposición de lecturas autenticadas |
| Alta | Clave real en el archivo de ejemplo; el ZIP recibido incluye entorno virtual y metadatos Git | Distribución reproducible sin `.env`, sesiones, datos personales, `.git` ni `.venv`; separar paquete privado y demo | Evitar distribuir configuración privada y dependencias no portables |
| Alta | Todos los usuarios comparten campaña y resultados, sin entidad de proyecto/campaña | Si se ofrecerá a varios clientes: aislamiento por organización/proyecto, aplicado en todas las consultas y archivos | Evitar mezclar cuentas y datos de clientes |
| Alta | El chat conserva un asistente mutable por sesión; el lock protege la obtención, no toda la pregunta | Serializar solicitudes por sesión y proteger sus cambios de historial; límites de concurrencia y presupuesto de IA | Evitar respuestas cruzadas y consumo accidental |
| Media | `verificar_cifras` valida pertenencia de valores, sin asociarlos con partido, año y métrica | Validar hechos estructurados: valor + unidad + sujeto + filtros; separar aportado por usuario de comprobado con base | Una cifra existente no avala una atribución equivocada |
| Media | Auditoría agrupa varias verificaciones sin `anio`; clave de duplicados omite entidad | Incorporar año y entidad donde correspondan; usar la misma clave territorial en mapas y comparativos | Incorporar más años o estados sin mezclar resultados |
| Media | Perfil, mapa, gráficos y asistente mantienen cálculos similares en módulos diferentes | Capa común de métricas: votos válidos/emitidos, total de referencia, participación, empate y ranking | Una misma pregunta produce la misma cifra en todas las secciones |
| Media | No hay importador visible con previsualización y confirmación de calidad | Importar CSV/XLSX con mapeo de columnas, revisión y versión de dataset; registrar fuente, licencia/uso autorizado y fecha | Actualizar la base con trazabilidad y posibilidad de revertir |
| Media | Campaña guarda estados, responsables y metas, pero no gastos ni presupuestos | Añadir movimientos de gasto, presupuesto, comprobantes y reportes por área/meta/tarea; evitar duplicar gasto por relaciones | Medir presupuesto ejecutado y costo por unidad realmente completada |
| Media | El asistente electoral consulta resultados, sin integración al modelo de tareas/gastos | Consultas operativas controladas sobre tareas y presupuesto, con permisos y filtros visibles | Responder informes internos sin copiar datos manualmente |
| Media | Escucha social tiene persistencia, versiones y fallback; prensa usa léxicos | Mostrar fuente, periodo, tamaño/cobertura de muestra, método usado y estado parcial; validar clasificación con muestra humana | Distinguir una medición de comentarios de una encuesta de población |
| Media | Prompt social contiene reglas absolutas para sarcasmo, burlas e insultos | Evaluación con conjunto etiquetado, matriz de confusión y categoría de incertidumbre para revisión | Medir errores antes de confiar en etiquetas automáticas |
| Media | Pipelines externos se ejecutan en executor, pero la experiencia depende de una petición larga | Trabajos persistentes con progreso, cancelación, reintento y resultado recuperable | Consultas largas que sobreviven a recargar la página |
| Media | Perfil y comparativo disparan varias peticiones; GeoJSON de aproximadamente 5.9 MB | Cancelar respuestas obsoletas, cachear catálogo/geométrica, comprimir y simplificar geometría según zoom | Mejor respuesta en móvil y conexiones lentas |
| Media | `basedatos.iniciar` ignora cualquier excepción durante migraciones | Migraciones versionadas; distinguir columna existente de error real; transacciones, registro y backups | Detectar fallos de esquema en lugar de ocultarlos |
| Media | Pantalla inicial permite crear primer administrador si no hay usuarios | En servidor, preparar administrador antes de exposición y cerrar el setup mediante configuración; operación atómica ante carreras | Controlar quién obtiene el primer acceso |
| Baja | Formularios y tablas grandes necesitan comprobarse en pantallas pequeñas | Revisión móvil, teclado, lectores de pantalla, leyendas y contraste; filtros dependientes y búsqueda de unidades | Menos errores de selección y mejor accesibilidad |
| Baja | Algunos mensajes y estilos conservan nombres/planes de versiones anteriores | Actualizar versión de API, glosario, documentación y planes; notas de lanzamiento por versión | Saber qué funciones están realmente implementadas |
| Baja | No hay automatización externa integrada al modelo de tareas | API operativa con credenciales limitadas, idempotencia, validaciones y auditoría para n8n; después entrada por WhatsApp con revisión de campos | Registrar y actualizar tareas sin duplicarlas y con responsable verificable |

Estas prioridades se basan en el código revisado. Riesgos de producción como aislamiento entre clientes aplican
cuando el producto se comparta entre organizaciones; no significan que ya se haya probado una fuga en esta instalación.

## Orden sugerido de trabajo

1. Conservar la corrección entregada; decidir si se desea reparto de coaliciones y bajo qué definición.
2. Estabilizar sincronización, calidad, empates y motor común de métricas.
3. Añadir importación/versionado y reportes reproducibles con fuente y filtros.
4. Mejorar gastos/tareas y consultas operativas; probar permisos y concurrencia.
5. Preparar operación multiusuario, evaluación de IA y automatizaciones.

Para la próxima iteración, el cambio más valioso es separar la actualización de datos del arranque y cerrar
los descuadres. Añadir funciones antes de eso multiplicaría los lugares donde aparecen resultados difíciles de explicar.

## Validación realizada y límites

- Doce pruebas de regresión aprobadas. Incluyen separación de aliados/coaliciones, conservación de opciones desconocidas,
  exclusión de auxiliares, porcentajes, ausencia de años inexistentes, redacción simulada, caché y precedencia de configuración.
- Comparativo contrastado con ranking y porcentajes del perfil en estado, municipio, distrito local, distrito federal y sección.
- Gráfica y tabla ejecutadas con datos reales en un entorno JavaScript controlado: 19 opciones separadas para el municipio de San Luis Potosí.
- Sintaxis Python y JavaScript comprobada. La configuración incluida carga la clave desde `.env` sin mostrarla.
- Las bases de datos incluidas conservan exactamente los bytes recibidos.
- No se realizó una llamada externa a Groq: no se comprobaron vigencia de la clave, cuota, modelo ni conectividad de la cuenta.
- No se inició el servidor completo ni se hizo una prueba visual en navegador: el entorno de revisión no tiene todas las dependencias
  del proyecto instaladas y el entorno virtual recibido pertenece a macOS. La prueba de redacción usa un cliente simulado.

## Uso de la entrega

Descomprime en una carpeta nueva. Crea el entorno virtual, instala `requirements.txt` y ejecuta `python run.py` como indica README.
El paquete privado conserva la configuración recibida en `.env`. Si actualizas una instalación existente, conserva tus bases
actuales y tu `.env`; reemplaza código/frontend y reinicia. No es necesario regenerar datos por esta corrección.
Ten presente que el comportamiento actual de arranque todavía intenta sincronizar Google Sheets: la separación de ese proceso
es una mejora pendiente, no un cambio de esta entrega.

Prueba Perfil territorial → Municipio → SAN LUIS POTOSI → 2024. Deben aparecer PAN, PRI, PRD, MORENA, PT y PVEM
como opciones distintas cuando tengan votos, además de las coaliciones presentes. Después pulsa Redactar análisis.
Si Groq falla, el mensaje permitirá distinguir configuración ausente, rechazo de clave, límite o timeout.
Las variables de entorno del servidor tienen precedencia sobre `.env`; revisa ambas si persiste una clave distinta.

## Seguimiento: configuración no detectada

El mensaje persistió en la instalación del usuario. No se pudo confirmar desde aquí qué carpeta o entorno
está ejecutando. Se añadieron recuperación desde `.env.example` para una clave con formato Groq, recuperación
cuando una variable exportada vacía oculta `.env`, recarga al inicializar y `configurar_claves.py`.
Este comando muestra la ruta activa, solicita la clave localmente si hace falta y conserva las otras variables.
La recuperación del formato no prueba la vigencia de la clave. Sigue pendiente la llamada real al proveedor.
Esta actualización sustituye la descripción anterior que indicaba que nunca se lee `.env.example`.
