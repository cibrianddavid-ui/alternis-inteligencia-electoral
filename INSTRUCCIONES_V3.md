# Alternis V3: fuentes electorales 2018 y 2024

La aplicación importa ambas hojas Google Sheets en cada arranque y construye `data/electoral.db`.

1. Copia tu archivo `.env` de la instalación anterior a esta carpeta. No se incluye en el ZIP por seguridad.
2. Verifica que ambas hojas permitan lectura sin iniciar sesión y tengan encabezados homologados.
3. Ejecuta `python run.py` como habitualmente.
4. Si aparece error de importación, consulta `/api/health`; la aplicación no reemplazará la base existente hasta que ambas fuentes se hayan descargado y validado.

**Importante:** No se pudo comprobar desde este entorno el contenido real de la hoja 2018. Se exige que ambas fuentes tengan los campos: `anio`, `entidad`, `municipio`, `seccion`, `id_distrito_local`, `id_distrito_federal`, `tipo_eleccion`, `partido`, `votos`. El campo `anio` puede faltar: se asignará automáticamente según la fuente. Las columnas adicionales se conservan.

La base SQLite incluida procede del ZIP original; hasta ejecutar con acceso a ambas hojas no contiene necesariamente 2018.
