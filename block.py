"""Entidad de bloque para la cadena académica de trazabilidad."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from models import ahora_utc_iso
from wallet import Wallet


@dataclass
class Block:
    """Representa un bloque enlazado con el hash del bloque anterior."""

    index: int
    # Número de posición del bloque dentro de la cadena.
    # Permite saber el orden cronológico de los bloques.
    timestamp: str
    # Fecha y hora en la que el bloque se crea y se incorpora a la cadena.
    # Es diferente del timestamp de cada evento individual.
    events: List[Dict[str, Any]]
    # Lista de eventos de trazabilidad incluidos en el bloque.
    # Son los datos que la blockchain quiere registrar de forma permanente.
    previous_hash: str
    # Hash del bloque anterior.
    # Sirve para enlazar este bloque con el anterior y formar la cadena.
    # Si un bloque anterior cambia, este enlace deja de ser válido.
    system_events: List[Dict[str, Any]] = field(default_factory=list)
    # Eventos internos de la red, como cambios de validadores.
    # No son eventos logisticos de productos.
    creador_id: str = field(default="")
    # Identidad lógica del nodo o actor que propone el bloque.
    creador_public_key: str = field(default="")
    # Clave pública del creador del bloque.
    firma_creador: str = field(default="")
    # Firma digital del creador sobre el contenido sellado del bloque.
    validador_id: str = field(default="")
    # Identidad lógica del nodo o actor que valida el bloque.
    validador_public_key: str = field(default="")
    # Clave pública del validador del bloque.
    firma_validador: str = field(default="")
    # Firma digital del validador sobre el bloque y la firma del creador.
    timestamp_validacion: str = field(default="")
    # Fecha y hora en la que el bloque fue validado criptográficamente.
    hash: str = field(default="")
    # Hash propio del bloque.
    # Actúa como una huella digital del contenido del bloque.
    # Si cambia cualquier dato del bloque, el hash calculado será distinto.

    def calcular_hash(self) -> str:
        """Calcula un hash SHA-256 determinista del contenido del bloque."""

        contenido_bloque = self.datos_base_para_hash()
        # Contenido real que se usará para calcular el hash del bloque.
        # No se incluyen las firmas porque se calculan después del sellado.
        serializado = json.dumps(contenido_bloque, sort_keys=True, separators=(",", ":"))
        # Convierte el bloque a JSON de forma determinista.
        # Así todos los nodos calculan el mismo hash para el mismo contenido.
        return hashlib.sha256(serializado.encode("utf-8")).hexdigest()

    def datos_base_para_hash(self) -> Dict[str, Any]:
        """Contenido estructural del bloque usado para el hash."""

        datos = {
            "index": self.index,
            "timestamp": self.timestamp,
            "events": self.events,
            "previous_hash": self.previous_hash,
        }
        if self.system_events:
            datos["system_events"] = self.system_events
        return datos
        # No se incluye el propio hash para evitar dependencia circular.

    def sellar(self) -> None:
        """Guarda el hash calculado dentro del propio bloque."""

        self.hash = self.calcular_hash()
        # Sella el bloque guardando su hash calculado.
        # A partir de aquí, cualquier cambio en el bloque podrá detectarse.

    def a_diccionario(self) -> Dict[str, Any]:
        datos = asdict(self)
        datos["creator_id"] = self.creador_id
        datos["creator_public_key"] = self.creador_public_key
        datos["creator_signature"] = self.firma_creador
        datos["validator_id"] = self.validador_id
        datos["validator_public_key"] = self.validador_public_key
        datos["block_signature"] = self.firma_validador
        return datos
        # Convierte el bloque a diccionario para poder guardarlo,
        # enviarlo por la API o compararlo con bloques de otros nodos.

    def datos_para_firma_creador(self) -> Dict[str, Any]:
        """Carga canónica que debe firmar quien propone el bloque."""

        return {
            **self.datos_base_para_hash(),
            "hash": self.hash,
            "creador_id": self.creador_id,
            "creador_public_key": self.creador_public_key,
        }

    def datos_para_firma_validador(self) -> Dict[str, Any]:
        """Carga canónica que debe firmar quien valida el bloque."""

        return {
            **self.datos_para_firma_creador(),
            "firma_creador": self.firma_creador,
            "validador_id": self.validador_id,
            "validador_public_key": self.validador_public_key,
            "timestamp_validacion": self.timestamp_validacion,
        }

    def firmar_creador(self, creador_id: str, wallet_creador: Wallet) -> None:
        """Asocia autoría criptográfica al creador o proponente del bloque."""

        self.creador_id = str(creador_id).strip()
        self.creador_public_key = wallet_creador.clave_publica_pem
        self.firma_creador = wallet_creador.firmar_datos(self.datos_para_firma_creador())

    def firmar_validador(
        self,
        validador_id: str,
        wallet_validador: Wallet,
        timestamp_validacion: str | None = None,
    ) -> None:
        """Asocia la validación criptográfica del bloque."""

        self.validador_id = str(validador_id).strip()
        self.validador_public_key = wallet_validador.clave_publica_pem
        self.timestamp_validacion = timestamp_validacion or ahora_utc_iso()
        self.firma_validador = wallet_validador.firmar_datos(self.datos_para_firma_validador())

    def tiene_firmas_bloque(self) -> bool:
        """Indica si el bloque tiene las firmas mínimas de creador y validador."""

        return all(
            [
                self.creador_id,
                self.creador_public_key,
                self.firma_creador,
                self.validador_id,
                self.validador_public_key,
                self.firma_validador,
                self.timestamp_validacion,
            ]
        )

    def es_bloque_legacy_sin_firma(self) -> bool:
        """Permite reconocer bloques antiguos creados antes de esta ampliación."""

        return not any(
            [
                self.creador_id,
                self.creador_public_key,
                self.firma_creador,
                self.validador_id,
                self.validador_public_key,
                self.firma_validador,
                self.timestamp_validacion,
            ]
        )

    @classmethod
    def desde_diccionario(cls, datos: Dict[str, Any]) -> "Block":
        return cls(
            index=int(datos["index"]),
            timestamp=str(datos["timestamp"]),
            events=list(datos["events"]),
            previous_hash=str(datos["previous_hash"]),
            system_events=list(datos.get("system_events", [])),
            creador_id=str(datos.get("creador_id") or datos.get("creator_id", "")),
            creador_public_key=str(datos.get("creador_public_key") or datos.get("creator_public_key", "")),
            firma_creador=str(datos.get("firma_creador") or datos.get("creator_signature", "")),
            validador_id=str(datos.get("validador_id") or datos.get("validator_id", "")),
            validador_public_key=str(
                datos.get("validador_public_key") or datos.get("validator_public_key", "")
            ),
            firma_validador=str(datos.get("firma_validador") or datos.get("block_signature", "")),
            timestamp_validacion=str(datos.get("timestamp_validacion", "")),
            hash=str(datos.get("hash", "")),
        )
    # Reconstruye un bloque a partir de datos en formato diccionario.
    # Se usa al cargar la blockchain desde disco o al recibirla de otro nodo.

    def calculate_hash(self) -> str:
        return self.calcular_hash()

    def seal(self) -> None:
        self.sellar()

    def to_dict(self) -> Dict[str, Any]:
        return self.a_diccionario()

    @property
    def block_signature(self) -> str:
        return self.firma_validador

    @property
    def validator_id(self) -> str:
        return self.validador_id

    @property
    def validator_public_key(self) -> str:
        return self.validador_public_key

    @classmethod
    def from_dict(cls, datos: Dict[str, Any]) -> "Block":
        return cls.desde_diccionario(datos)
