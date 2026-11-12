# Mis Ligas

Web en español con resultados, calendarios y estadísticas de LaLiga, la liga colombiana, Hungría (NB I) y Polonia (Ekstraklasa).

- Historial por temporada, equipo, torneo y local/visitante.
- Jornadas numeradas para acceder directamente a sus encuentros y lista cronológica con «Ver más», sin pasar de página.
- Mayores rachas sin ambos marcan en HT y FT, con fechas, jornadas y racha actual; en España también sin tarjetas rojas. Los datos pendientes interrumpen la verificación y los partidos simultáneos se agrupan.
- HT y FT en las cuatro ligas; Hungría y Polonia se centran en resultados, sin córners ni tarjetas. Verdes cuando ambos marcaron en HT.
- Próximos partidos con fecha y hora confirmadas, identificando los horarios pendientes.
- Referencia estadística del siguiente ambos marcan en HT, con muestra, incertidumbre y aviso de caducidad. Es una referencia compartida de liga, no un pronóstico específico por equipo ni una garantía.
- Descarga de los Excel históricos y exportación de datos filtrados a CSV.

## Publicación gratuita

En **Settings → Pages → Build and deployment**, selecciona **GitHub Actions**. El trabajo **Actualizar partidos y publicar Mis Ligas** publica la web y consulta las fuentes aproximadamente cada 30 minutos; la ejecución puede retrasarse.

Si Pages todavía no está activado, el trabajo prepara los archivos y muestra el enlace de configuración. La URL solo estará disponible después de una publicación correcta.

Los datos se conservan en JSON versionado. El sitio público contiene únicamente la página generada y su manifiesto; las bases SQLite y credenciales quedan fuera. Si falla el calendario de una liga, se conserva la publicación anterior y su fecha de consulta.

## Fuentes y cobertura

Football-Data (CSV SP1) y ESPN aportan estadísticas y calendario de España y Colombia. La fuente de Hungría es el centro de partidos oficial de la federación húngara (MLSZ). Hungría incluye nueve temporadas desde 2018/19, con dos incidencias históricas señaladas: un resultado administrativo y un abandono con repetición. Polonia utiliza el centro de partidos oficial de la Ekstraklasa para el calendario actual y BetExplorer para el histórico desde 2018/19, contrastado con RSSSF y 90minut. Las temporadas 2018/19 y 2019/20 incluyen la fase regular y ambos grupos finales. Los datos faltantes se indican y no se convierten en cero. Los partidos futuros, en juego y administrativos no se usan como encuentros terminados en los resúmenes. La hora se muestra en Colombia. No se inventan jornadas ni horarios pendientes.

Las 38 jornadas de España 2026/27 se verificaron con el calendario oficial de LaLiga, contrastando los 380 partidos por local y visitante, fechas confirmadas y resultados finales disponibles. La correspondencia se conserva en `data/laliga_2026_27_rounds.json`; esta incorporación solo completa la jornada y la vuelta, sin alterar marcadores.

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

Para consultar las ligas y generar una nueva versión:

```sh
python scripts/publish_snapshot.py --refresh --output-dir site
```

## Pruebas

```sh
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```
