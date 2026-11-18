"""Métrica de negocio del proyecto 3: boletos vendidos por evento.

Se calcula en cada lectura de /metrics a partir de la vista ventas_por_evento
(~60 ms para los 22 eventos), así siempre refleja la base, sin importar qué
servicio vendió. La alerta de negocio (más del 90 % vendido) usa
boletera_porcentaje_vendido.

Con 2 réplicas, Prometheus lee el mismo valor de las dos; en las consultas se
usa  max by (evento_id) (...).
"""
import logging

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

log = logging.getLogger("metricas_negocio")

ETIQUETAS = ["evento_id", "evento", "artista", "recinto"]


class MetricasNegocio(Collector):
    def __init__(self, repositorio):
        self.repositorio = repositorio

    def describe(self):
        # Sin esto, prometheus_client consultaría la base al registrar el
        # colector (al arrancar), cuando el pool quizá aún no está listo.
        return []

    def collect(self):
        ok = GaugeMetricFamily("boletera_metricas_negocio_ok", "1 si las métricas de negocio se leyeron de la base")
        try:
            filas = self.repositorio.ventas_por_evento()
        except Exception:
            log.exception("No se pudieron leer las métricas de negocio")
            ok.add_metric([], 0)
            yield ok
            return

        vendidos = GaugeMetricFamily("boletera_boletos_vendidos", "Boletos vendidos por evento", labels=ETIQUETAS)
        capacidad = GaugeMetricFamily("boletera_capacidad", "Aforo del evento", labels=ETIQUETAS)
        disponibles = GaugeMetricFamily("boletera_boletos_disponibles", "Boletos disponibles por evento", labels=ETIQUETAS)
        porcentaje = GaugeMetricFamily("boletera_porcentaje_vendido", "Porcentaje del aforo vendido", labels=ETIQUETAS)
        for f in filas:
            etiquetas = [str(f["id"]), f["nombre"], f["artista"], f["recinto"]]
            vendidos.add_metric(etiquetas, f["vendidos"])
            capacidad.add_metric(etiquetas, f["capacidad"])
            disponibles.add_metric(etiquetas, f["disponibles"])
            porcentaje.add_metric(etiquetas, float(f["porcentaje_vendido"]))
        ok.add_metric([], 1)
        yield from (vendidos, capacidad, disponibles, porcentaje, ok)
