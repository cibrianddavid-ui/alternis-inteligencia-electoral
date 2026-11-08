# Corrección V4.1

- Se homologan los nombres de las elecciones **antes** de consolidar los tres Google Sheets y de persistir datos en SQLite. Los cinco valores canónicos son AYUNTAMIENTO, DIPUTACION_LOC, DIPUTACION_FED, PRESIDENTE y SENADURIA.
- Los marcadores de casillas intentan leer el CSV publicado por Google y un endpoint CSV alternativo; si ambos fallan se usa `data/casillas_2024.csv` cuando exista.
- El visor informa el número de casillas representadas, los registros con coordenadas inválidas y los errores de acceso. Las coordenadas con múltiples puntos se convierten correctamente.

## Importante
Reinicia el servidor y vuelve a ejecutar la importación/sincronización de Sheets desde la aplicación para reemplazar los nombres antiguos guardados en SQLite. El archivo `data/electoral.db` preexistente no se ha reescrito: no se deben modificar sus votos sin una sincronización controlada.

Si el Sheet de casillas requiere inicio de sesión, exporta la pestaña `gid=171473950` como CSV y colócala en `data/casillas_2024.csv`; también puedes habilitar acceso de lectura mediante enlace. La aplicación no inventa ubicaciones cuando no hay acceso a las coordenadas.
