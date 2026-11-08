# V4.3: casillas y domicilios

- Fuente local `data/casillas_2024.csv` prioritaria, sin dependencia de internet.
- Descarga alternativa de Google Sheets cuando no hay CSV local; identificadores configurables en `.env`.
- Mensajes de diagnóstico visibles cuando falla la descarga.
- Panel de domicilios por sección independiente del interruptor de marcadores.
- Normalización de coordenadas y marcadores Leaflet conservados.
- Se corrigió `.env.example` para no distribuir claves privadas.

## Comprobación
1. Descarga el segundo Google Sheet como CSV y guárdalo en `data/casillas_2024.csv` si el acceso público falla.
2. Reinicia `python run.py` y recarga la página forzadamente (Cmd+Shift+R).
3. Abre `/api/cartografia/casillas` en sesión iniciada y verifica `total_registros`, `total`, `advertencia` y `diagnostico`.
4. Selecciona una sección para ver sus domicilios, aun sin activar los marcadores.

**Nota:** no se garantiza que el Sheet sea accesible desde cada red o con permisos privados.
