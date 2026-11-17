"""Esquemas Pydantic v2 para validación estricta de paradas, conexiones y viajes Va y Ven."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

CODE_REGEX = r"^[A-Z0-9][A-Z0-9_-]{1,31}$"


def _normalize_code(value: Any) -> Any:
    """Normaliza códigos de parada a mayúsculas sin espacios en los extremos."""
    if isinstance(value, str):
        return value.strip().upper()
    return value


def _reject_boolean(value: Any) -> Any:
    """Evita coacción silenciosa de booleanos (True/False) a números."""
    if isinstance(value, bool):
        raise ValueError("El valor numérico no puede ser booleano.")
    return value


CodeStr = Annotated[
    str,
    BeforeValidator(_normalize_code),
    StringConstraints(pattern=CODE_REGEX),
]
LabelStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
ZoneStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)]
PositiveInt = Annotated[int, BeforeValidator(_reject_boolean), Field(ge=1, le=240)]


class StrictInputModel(BaseModel):
    """Modelo base estricto para entradas: recorta espacios y prohíbe campos extra."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class ParadaCreate(StrictInputModel):
    """Datos validados para registrar una nueva parada en la red Va y Ven."""

    id: CodeStr | None = Field(
        default=None,
        description="Código único de parada (incremental automático si se omite)",
        examples=["PAR-11"],
    )
    nombre: LabelStr = Field(description="Nombre de la parada o paradero", examples=["Parque San Juan"])
    zona: ZoneStr = Field(description="Zona o sector de Mérida", examples=["Centro"])
    tipo: Literal["Terminal", "Paradero"] = Field(
        description="Tipo de infraestructura permitido",
        examples=["Paradero"],
    )


class ParadaUpdate(StrictInputModel):
    """Datos validados para actualizar una parada existente."""

    nombre: LabelStr | None = Field(default=None, examples=["Parque San Juan (Centro)"])
    zona: ZoneStr | None = Field(default=None, examples=["Centro"])
    tipo: ZoneStr | None = Field(default=None, examples=["Paradero"])

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> ParadaUpdate:
        """Exige al menos un campo modificable."""
        if self.nombre is None and self.zona is None and self.tipo is None:
            raise ValueError("Debes enviar al menos un campo para actualizar (nombre, zona o tipo).")
        return self


def _validate_finite_distance(value: float) -> float:
    """Valida que la distancia sea finita y la redondea a 2 decimales."""
    if not math.isfinite(value):
        raise ValueError("La distancia debe ser un número finito válido.")
    return round(float(value), 2)


class ConexionCreate(StrictInputModel):
    """Datos validados para crear una nueva relación dirigida entre dos paradas."""

    origen_id: CodeStr = Field(examples=["PAR-01"])
    destino_id: CodeStr = Field(examples=["PAR-02"])
    minutos: PositiveInt = Field(description="Tiempo estimado del tramo en minutos (1-240)", examples=[10])
    distancia_km: float = Field(gt=0, le=100, description="Distancia del tramo en km", examples=[3.2])

    @field_validator("distancia_km")
    @classmethod
    def validate_distance(cls, value: float) -> float:
        """Valida distancia finita para la nueva conexión."""
        return _validate_finite_distance(value)

    @model_validator(mode="after")
    def reject_self_loop(self) -> ConexionCreate:
        """Evita crear un tramo cuyo origen y destino sean la misma parada."""
        if self.origen_id == self.destino_id:
            raise ValueError("La parada de origen y destino no pueden ser la misma.")
        return self


class ConexionUpdate(StrictInputModel):
    """Datos validados para actualizar una conexión existente."""

    minutos: PositiveInt | None = Field(default=None, examples=[12])
    distancia_km: float | None = Field(default=None, gt=0, le=100, examples=[3.5])

    @field_validator("distancia_km")
    @classmethod
    def validate_distance(cls, value: float | None) -> float | None:
        """Valida distancia finita cuando se envía para actualización."""
        return None if value is None else _validate_finite_distance(value)

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> ConexionUpdate:
        """Exige al menos un campo modificable."""
        if self.minutos is None and self.distancia_km is None:
            raise ValueError("Debes enviar al menos un campo para actualizar (minutos o distancia_km).")
        return self


class ParadaOut(BaseModel):
    """Representación de un nodo Parada."""

    id: str
    nombre: str
    zona: str
    tipo: str


class ParadaCreatedResponse(BaseModel):
    """Respuesta al crear una parada."""

    mensaje: str
    parada: ParadaOut


class ParadaUpdatedResponse(BaseModel):
    """Respuesta al actualizar una parada."""

    mensaje: str
    parada: ParadaOut


class ParadaDeletedResponse(BaseModel):
    """Respuesta al eliminar una parada."""

    mensaje: str
    id: str
    eliminados: int


class ConexionOut(BaseModel):
    """Tramo dirigido entre dos paradas."""

    origen_id: str
    origen: str
    destino_id: str
    destino: str
    minutos: int
    distancia_km: float


class ConexionDeletedResponse(BaseModel):
    """Respuesta al eliminar un tramo entre dos paradas."""

    mensaje: str
    eliminados: int


class TramoViajeOut(BaseModel):
    """Paso individual dentro de un itinerario calculado."""

    desde_id: str
    desde_nombre: str
    hacia_id: str
    hacia_nombre: str
    minutos: int
    distancia_km: float


class ViajePlanResponse(BaseModel):
    """Resultado completo del cálculo de viaje con tarifa Va y Ven."""

    origen_id: str
    origen: str
    destino_id: str
    destino: str
    tipo_tarifa: Literal["general", "estudiante"]
    minutos_totales: int
    distancia_total_km: float
    numero_paradas: int
    costo_total: float
    tramos: list[TramoViajeOut]


class SalidaDirectaOut(BaseModel):
    """Destino directo alcanzable desde una parada en 1 tramo."""

    destino_id: str
    destino: str
    zona_destino: str
    minutos: int
    distancia_km: float


class ParadaAlcanceOut(BaseModel):
    """Parada alcanzable en hasta 2 tramos desde el origen."""

    destino_id: str
    destino: str
    zona_destino: str
    saltos: int
    minutos_totales: int


class RedTransporteResponse(BaseModel):
    """Paradas y conexiones completas de la red Va y Ven."""

    paradas: list[ParadaOut]
    conexiones: list[ConexionOut]


class HealthResponse(BaseModel):
    """Estado de configuración y conectividad real con Neo4j AuraDB."""

    status: str
    configured: bool
    connected: bool
    database: str
    issues: list[str]
    detail: str
