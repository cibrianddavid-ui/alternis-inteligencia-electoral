# Alternis V4

## Fuentes
- 2012: Google Sheet `1UZ-IBRkjbF3-b2GkMcA3zDaqNcAf-X-cjC85WcLamJg`, gid `792258105`.
- Casillas 2024: Google Sheet `1eXXKieq9YybGlk12BIePDY7VVd53zAy6TNIdwH0WaW4`, gid `171473950`.

La aplicación descarga los Sheets al arrancar. Deben permitir exportación CSV pública. Si el Sheet de casillas no es accesible, exportarlo como `data/casillas_2024.csv`. No se incluyeron copias de los Sheets porque no pudieron descargarse durante la preparación.

## Cambios
- Datos 2012 con municipio y distritos homologados a 2024; campos históricos conservados.
- Casillas con capa opcional y normalización de coordenadas.
- Etapa y mes en metas y tareas; asignación y reasignación explícita de área.
- Migraciones SQLite no destructivas.

## Ejecutar
`pip install -r requirements.txt` y `python run.py`. Revisar `/api/health`.
