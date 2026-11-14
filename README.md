# Mis Ligas

Web en español con resultados, calendarios y estadísticas de LaLiga, la liga colombiana, Hungría (NB I), Polonia (Ekstraklasa), Italia (Serie A) y Paraguay (Primera División).

- Historial por temporada, equipo, torneo y local/visitante.
- Jornadas numeradas para acceder directamente a sus encuentros y lista cronológica con «Ver más», sin pasar de página.
- Mayores rachas sin ambos marcan en HT y FT, con fechas, jornadas y racha actual; en España e Italia también sin tarjetas rojas. Los datos pendientes interrumpen la verificación y los partidos simultáneos se agrupan.
- HT y FT en las seis ligas; Hungría y Polonia se centran en resultados, sin córners ni tarjetas. Paraguay y Colombia incluyen los córners disponibles, sin tarjetas. España e Italia incluyen córners, amarillas y rojas. Verdes cuando ambos marcaron en HT.
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

Italia abarca 2018/19–2026/27: Football-Data I1 aporta marcadores, córners y tarjetas; ESPN aporta el calendario y los nuevos resultados, y BetExplorer las jornadas explícitas y las horas históricas ausentes del CSV. El histórico se contrastó con RSSSF. Se conserva el desempate Spezia–Verona de 2022/23 como partido adicional, y Verona–Roma de 2020/21 como resultado administrativo 3–0, excluido del análisis. El Excel italiano mantiene cada temporada en su hoja, con las fuentes y excepciones en «Notas».

La actualización italiana puede reutilizar las jornadas ya verificadas de la misma temporada, siempre que los 380 partidos coincidan con el calendario consultado de ESPN. Los resultados, estadísticas y horarios se siguen consultando en sus fuentes. Una captura incompleta o de otra temporada no sustituye la comprobación de jornadas.

Paraguay abarca 2018–2026, con Apertura y Clausura en cada año, las eliminatorias de 2020 y los dos encuentros de promoción de 2021. ESPN aporta el calendario; BetExplorer las jornadas explícitas y los marcadores de cada tiempo, comprobados en los 2.212 partidos disputados de la captura inicial. RSSSF contrasta los resultados históricos. Los cinco resultados administrativos se conservan fuera de los análisis y hay 18 encuentros programados de 2026. Las jornadas conservan su número aunque se hayan disputado fuera de orden.

Los córners de Paraguay se obtienen de estadísticas publicadas por ESPN y TotalCorner, cruzando equipos, fecha y hora UTC y resultado final. La cobertura inicial es parcial: 602 de 2.212 partidos disputados. No se encontraron córners verificables de 2018–2023; en los demás años se señala cada dato ausente como N/D. Los ceros generados por bloques estadísticos incompletos se rechazan. El detalle de cobertura y conciliación está en `data/paraguay_coverage.json`; el Excel contiene una hoja por año y las fuentes en «Notas», sin tarjetas ni filas inmovilizadas.

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
