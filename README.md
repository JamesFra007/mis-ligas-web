# Mis Ligas

Web en español con resultados, calendarios y estadísticas de LaLiga, la liga colombiana y la primera división de Hungría (NB I).

- Historial por temporada, equipo, torneo y local/visitante.
- Vistas por jornada/listado original y por fecha y hora de Colombia.
- HT y FT en las tres ligas; Hungría se centra en resultados, sin córners ni tarjetas. Verdes cuando ambos marcaron en HT.
- Próximos partidos con fecha y hora confirmadas, identificando los horarios pendientes.
- Referencia estadística del siguiente ambos marcan en HT, con muestra, incertidumbre y aviso de caducidad. Es una referencia compartida de liga, no un pronóstico específico por equipo ni una garantía.
- Descarga de los Excel históricos y exportación de datos filtrados a CSV.

## Publicación gratuita

En **Settings → Pages → Build and deployment**, selecciona **GitHub Actions**. El trabajo **Actualizar partidos y publicar Mis Ligas** publica la web y consulta las fuentes aproximadamente cada 30 minutos; la ejecución puede retrasarse.

Si Pages todavía no está activado, el trabajo prepara los archivos y muestra el enlace de configuración. La URL solo estará disponible después de una publicación correcta.

Los datos se conservan en JSON versionado. El sitio público contiene únicamente la página generada y su manifiesto; las bases SQLite y credenciales quedan fuera. Si falla el calendario de una liga, se conserva la publicación anterior y su fecha de consulta.

## Fuentes y cobertura

Football-Data (CSV SP1) y ESPN aportan estadísticas y calendario de España y Colombia. La fuente de Hungría es el centro de partidos oficial de la federación húngara (MLSZ). Hungría incluye nueve temporadas desde 2018/19, con dos incidencias históricas señaladas: un resultado administrativo y un abandono con repetición. Los datos faltantes se indican y no se convierten en cero. Los partidos futuros, en juego y administrativos no se usan como encuentros terminados en los resúmenes. La hora se muestra en Colombia. No se inventan jornadas ni horarios pendientes.

## Desarrollo local

Requiere Python 3.12 o posterior.

```sh
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

La web local abre en http://127.0.0.1:8000. Para generar el sitio sin consultar las fuentes:

```sh
python scripts/publish_snapshot.py --output-dir site
```

Para consultar las tres ligas y generar una nueva versión:

```sh
python scripts/publish_snapshot.py --refresh --output-dir site
```

## Pruebas

```sh
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```
