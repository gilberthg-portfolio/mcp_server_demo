"""Data models for BCCR payloads and the server's internal rate representation.

The BCCR REST API speaks Spanish. A successful response looks like::

    {
        "estado": true,
        "mensaje": "Consulta exitosa",
        "datos": [
            {
                "codigoIndicador": "318",
                "nombreIndicador": "Tipo cambio venta",
                "series": [
                    {"fecha": "2026-04-28", "valorDatoPorPeriodo": 457.93}
                ]
            }
        ]
    }

We mirror that shape into pydantic models so missing or mistyped fields raise
a clear error at parse time. The rest of the codebase deals in
``FlatRate``, which is the merged buy+sell record produced by the BCCR
client.
"""

from __future__ import annotations

from datetime import date
from typing import Any

# --- Python idiom: pydantic BaseModel ---------------------------------------
# pydantic gives us runtime type validation + automatic JSON (de)serialization
# with a tiny amount of code. A class that inherits from ``BaseModel`` and
# declares fields as annotated attributes is ready to use:
#
#     class Foo(BaseModel):
#         bar: int
#
#     Foo.model_validate({"bar": "7"})   # -> Foo(bar=7), with coercion
#
# We use it for the BCCR response shape so invalid or missing fields fail
# loudly at parse time rather than producing mysterious KeyErrors later.
from pydantic import BaseModel, ConfigDict, Field


# BCCR's documented indicator codes.
BCCR_INDICATOR_BUY = "317"   # Tipo de cambio de compra (buy rate)
BCCR_INDICATOR_SELL = "318"  # Tipo de cambio de venta (sell rate)


class BccrSeriesPoint(BaseModel):
    """One row inside an indicator's ``series`` array.

    BCCR returns dates as plain ISO-8601 ``YYYY-MM-DD`` strings; pydantic v2
    parses those into ``date`` automatically when the field is typed as
    ``date``. Likewise it accepts a JSON number for ``valor`` and gives us a
    Python ``float``.
    """

    # ``populate_by_name=True`` lets us construct via Pythonic field names
    # ("valor", "fecha") in addition to the BCCR-supplied alias for valor.
    # ``extra="ignore"`` discards any extra fields (e.g. a future
    # "valorPorAnoBase") rather than failing.
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    fecha: date
    # The actual JSON key is "valorDatoPorPeriodo"; we expose it as the
    # shorter "valor" everywhere else.
    valor: float | None = Field(alias="valorDatoPorPeriodo", default=None)


class BccrIndicator(BaseModel):
    """One entry in the top-level ``datos`` array.

    Each entry corresponds to a single indicator code (e.g. "318") and
    carries a list of dated values in ``series``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    codigo: str = Field(alias="codigoIndicador")
    nombre: str | None = Field(alias="nombreIndicador", default=None)
    series: list[BccrSeriesPoint] = Field(default_factory=list)


class BccrResponse(BaseModel):
    """The complete top-level BCCR response envelope."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # ``estado`` is BCCR's own success boolean. Even with HTTP 200, a
    # ``estado=false`` should be treated as a failure (carries the reason
    # in ``mensaje``).
    estado: bool
    mensaje: str | None = None
    datos: list[BccrIndicator] = Field(default_factory=list)

    def points_for(self, indicator_code: str) -> list[BccrSeriesPoint]:
        """Return the series belonging to the given indicator code.

        BCCR can in theory return multiple indicator entries in one
        response; in practice the ``/indicadoresEconomicos/{code}/series``
        endpoint returns just one. We still filter defensively.
        """
        for indicator in self.datos:
            if indicator.codigo == indicator_code:
                return indicator.series
        return []


class FlatRate(BaseModel):
    """Merged buy/sell row, one per date.

    This is the internal shape every layer above the BCCR client deals in:
    a date plus the two float values. Either value may be ``None`` if BCCR
    happens to publish only one series on that day (rare, but possible).
    """

    model_config = ConfigDict(frozen=True)

    observed_on: date
    buy: float | None
    sell: float | None

    def to_response_dict(self) -> dict[str, Any]:
        """Render this row for the MCP response body.

        The MCP contract wants ISO-8601 date strings and English field names,
        which differ from the internal names used here.
        """
        return {
            "date": self.observed_on.isoformat(),
            "buy": self.buy,
            "sell": self.sell,
        }
