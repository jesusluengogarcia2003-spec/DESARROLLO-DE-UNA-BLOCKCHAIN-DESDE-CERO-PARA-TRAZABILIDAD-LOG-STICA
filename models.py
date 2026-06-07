"""Modelos de dominio para la trazabilidad y sus reglas básicas."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

# Importamos herramientas necesarias para definir eventos de trazabilidad.
# dataclass permite crear modelos de datos de forma sencilla.
# datetime se usa para guardar la fecha y hora de los eventos.
# uuid4 genera identificadores únicos para cada evento.


TIPOS_EVENTO_PERMITIDOS = {
    "CREATED",
    "SENT",
    "RECEIVED",
    "IN_TRANSIT",
    "DELIVERED",
}

# Lista de tipos de evento aceptados por la blockchain.
# Sirve para evitar que se registren estados inventados o incorrectos.
# Cada evento representa una fase del ciclo logístico del producto.


TRANSICIONES_VALIDAS = {
    None: {"CREATED"},
    "CREATED": {"SENT", "IN_TRANSIT", "RECEIVED"},
    "SENT": {"IN_TRANSIT", "RECEIVED", "DELIVERED"},
    "IN_TRANSIT": {"IN_TRANSIT", "RECEIVED", "DELIVERED"},
    "RECEIVED": {"SENT", "IN_TRANSIT", "DELIVERED"},
    "DELIVERED": set(),
}

# Reglas de transición entre estados.
# Indican qué eventos pueden ocurrir después de cada estado.
# None representa que el producto todavía no tiene historial.
# Por eso, el primer evento de cualquier producto debe ser CREATED.
# DELIVERED no permite transiciones posteriores porque es un estado final.

ESTADOS_FINALES = {"DELIVERED"}

# Estados que finalizan el ciclo de vida del producto.
# Si un producto llega a DELIVERED, ya no se aceptan más eventos para él.


def ahora_utc_iso() -> str:
    """Devuelve la fecha actual en UTC y formato ISO-8601."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

# Genera la fecha y hora actual en UTC.
# UTC evita problemas entre nodos ubicados en distintas zonas horarias.
# Se eliminan los microsegundos para que el timestamp sea más limpio.
# El formato ISO-8601 facilita guardar y comparar fechas.


def limpiar_texto(valor: Any, nombre_campo: str) -> str:
    """Normaliza campos de texto y evita valores vacíos."""

    texto = str(valor).strip()
    if not texto:
        raise ValueError(f"El campo '{nombre_campo}' no puede estar vacío.")
    return texto

# Limpia un campo de texto eliminando espacios innecesarios.
# También comprueba que el campo no quede vacío.
# Esto evita guardar eventos con datos incompletos o sin sentido.


def validar_timestamp_iso(valor: str) -> str:
    """Comprueba que una fecha siga un formato ISO-8601 legible."""

    texto = limpiar_texto(valor, "timestamp")
    try:
        datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("El campo 'timestamp' debe estar en formato ISO-8601 válido.") from exc
    return texto

# Valida que el timestamp tenga un formato de fecha correcto.
# Acepta fechas ISO-8601 y adapta la Z de UTC a +00:00.
# Si la fecha no se puede interpretar, se rechaza el evento.


@dataclass
class EventoTrazabilidad:
    """Evento firmado que registra un cambio de estado logístico."""

    event_id: str
    product_id: str
    event_type: str
    location: str
    actor_id: str
    timestamp: str
    signature: str
    public_key: str

    # Modelo principal del evento de trazabilidad.
    # Cada instancia representa una acción ocurrida sobre un producto,
    # firmada digitalmente por un actor.

    # Clase que representa un evento de trazabilidad.
    # Un evento indica qué ha ocurrido con un producto, dónde, cuándo y quién lo hizo.
    # Además incluye una firma digital y la clave pública para poder verificar su autenticidad.

    def a_diccionario(self) -> Dict[str, str]:
        return asdict(self)
    # Convierte el evento en un diccionario.
    # Esto facilita guardarlo en JSON, enviarlo por la API o incluirlo en un bloque.

    def datos_para_firma(self) -> Dict[str, str]:
        datos = self.a_diccionario()
        datos.pop("signature", None)
        return datos
    # Devuelve los datos que se usan para calcular o verificar la firma.
    # Se elimina el campo signature porque una firma no puede firmarse a sí misma.
    # Así se evita una dependencia circular.
    

    @classmethod
    def crear(
        cls,
        product_id: str,
        event_type: str,
        location: str,
        actor_id: str,
        public_key: str,
        signature: str = "",
        timestamp: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> "EventoTrazabilidad":
        tipo_normalizado = limpiar_texto(event_type, "event_type").upper()
        if tipo_normalizado not in TIPOS_EVENTO_PERMITIDOS:
            raise ValueError(
                f"Tipo de evento no válido: '{event_type}'. "
                f"Permitidos: {', '.join(sorted(TIPOS_EVENTO_PERMITIDOS))}."
            )

        return cls(
            event_id=limpiar_texto(event_id or str(uuid4()), "event_id"),
            product_id=limpiar_texto(product_id, "product_id"),
            event_type=tipo_normalizado,
            location=limpiar_texto(location, "location"),
            actor_id=limpiar_texto(actor_id, "actor_id"),
            timestamp=validar_timestamp_iso(timestamp or ahora_utc_iso()),
            signature=str(signature).strip(),
            public_key=limpiar_texto(public_key, "public_key"),
        )
   # Método de creación controlada de eventos.
    # Normaliza el tipo de evento a mayúsculas, valida que esté permitido
    # y limpia todos los campos obligatorios.
    # Si no se proporciona event_id, genera uno único con uuid4.
    # Si no se proporciona timestamp, usa la fecha actual en UTC.

    @classmethod
    def desde_diccionario(cls, datos: Dict[str, Any]) -> "EventoTrazabilidad":
        campos_obligatorios = {
            "event_id",
            "product_id",
            "event_type",
            "location",
            "actor_id",
            "timestamp",
            "signature",
            "public_key",
        }
        faltantes = [campo for campo in campos_obligatorios if campo not in datos]
        if faltantes:
            raise ValueError(f"Faltan campos del evento: {', '.join(sorted(faltantes))}.")

        return cls.crear(
            event_id=str(datos["event_id"]),
            product_id=str(datos["product_id"]),
            event_type=str(datos["event_type"]),
            location=str(datos["location"]),
            actor_id=str(datos["actor_id"]),
            timestamp=str(datos["timestamp"]),
            signature=str(datos["signature"]),
            public_key=str(datos["public_key"]),
        )
    # Reconstruye un evento a partir de un diccionario.
    # Se usa cuando el evento llega por la API o se carga desde almacenamiento.
    # También comprueba que estén todos los campos obligatorios.
    
    def to_dict(self) -> Dict[str, str]:
        """Alias de compatibilidad con la versión anterior."""

        return self.a_diccionario()

    @classmethod
    def create(cls, **kwargs: Any) -> "EventoTrazabilidad":
        """Alias de compatibilidad con la versión anterior."""

        return cls.crear(**kwargs)

    @classmethod
    def from_dict(cls, datos: Dict[str, Any]) -> "EventoTrazabilidad":
        """Alias de compatibilidad con la versión anterior."""

        return cls.desde_diccionario(datos)


TraceabilityEvent = EventoTrazabilidad
ALLOWED_EVENT_TYPES = TIPOS_EVENTO_PERMITIDOS
utc_now_iso = ahora_utc_iso




# Este archivo define el modelo de evento logístico y sus reglas básicas.
# Es la primera capa de validación antes de guardar información en la blockchain.
