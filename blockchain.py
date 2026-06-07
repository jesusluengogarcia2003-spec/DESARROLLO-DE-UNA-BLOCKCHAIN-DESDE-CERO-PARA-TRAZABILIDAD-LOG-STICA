"""Logica central de la blockchain academica para trazabilidad."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from block import Block
from models import ESTADOS_FINALES, TRANSICIONES_VALIDAS, EventoTrazabilidad, ahora_utc_iso
from wallet import Wallet


GENESIS_TIMESTAMP = "2026-01-01T00:00:00+00:00"
SYSTEM_EVENT_VALIDATOR_ADDED = "VALIDATOR_ADDED"
SYSTEM_EVENT_VALIDATOR_REMOVED = "VALIDATOR_REMOVED"


class Blockchain:
    """Blockchain simple orientada a registrar eventos logisticos auditables."""

    def __init__(
        self,
        validadores_autorizados: Optional[Set[str]] = None,
        claves_validadores: Optional[Dict[str, str]] = None,
        poa_turn_timeout_seconds: int = 0,
    ) -> None:
        self.validadores_iniciales: Set[str] = set(validadores_autorizados or set())
        self.validadores_autorizados: Set[str] = set(self.validadores_iniciales)
        self.claves_validadores_iniciales: Dict[str, str] = {
            str(validator_id).strip(): str(public_key).strip()
            for validator_id, public_key in (claves_validadores or {}).items()
            if str(validator_id).strip() and str(public_key).strip()
        }
        self.claves_validadores: Dict[str, str] = dict(self.claves_validadores_iniciales)
        self.poa_turn_timeout_seconds = max(0, int(poa_turn_timeout_seconds))
        self.cadena: List[Block] = [self._crear_bloque_genesis()]
        self.eventos_pendientes: List[Dict[str, Any]] = []
        self.eventos_sistema_pendientes: List[Dict[str, Any]] = []
        self.estado_actual_por_producto: Dict[str, Dict[str, Any]] = {}

    def _crear_bloque_genesis(self) -> Block:
        bloque_genesis = Block(
            index=0,
            timestamp=GENESIS_TIMESTAMP,
            events=[],
            previous_hash="0",
        )
        bloque_genesis.sellar()
        return bloque_genesis

    # Inicializa la blockchain con un bloque génesis fijo.
    # También crea una lista de eventos pendientes que aún no han sido minados.

    @property
    def ultimo_bloque(self) -> Block:
        return self.cadena[-1]

    def ordenar_validadores_autorizados(
        self,
        indice_bloque: Optional[int] = None,
        cadena: Optional[List[Block]] = None,
    ) -> List[str]:
        """Devuelve un orden estable de validadores para aplicar turnos PoA."""

        validadores = (
            self._obtener_validadores_para_altura(indice_bloque, cadena)
            if indice_bloque is not None
            else self.validadores_autorizados
        )
        return sorted(validador for validador in validadores if str(validador).strip())

    def obtener_validador_en_turno(
        self,
        indice_bloque: Optional[int] = None,
        cadena: Optional[List[Block]] = None,
    ) -> Optional[str]:
        """Calcula el validador esperado para una altura concreta de la cadena."""

        cadena_objetivo = cadena or self.cadena
        indice = len(cadena_objetivo) if indice_bloque is None else int(indice_bloque)
        if indice <= 0:
            return None

        validadores = self.ordenar_validadores_autorizados(indice, cadena_objetivo)
        if not validadores:
            return None

        posicion_turno = (indice - 1) % len(validadores)
        return validadores[posicion_turno]

    def obtener_validadores_habilitados(
        self,
        indice_bloque: Optional[int] = None,
        referencia_tiempo: Optional[str] = None,
        cadena: Optional[List[Block]] = None,
    ) -> List[str]:
        """Devuelve validadores que pueden firmar una altura tras aplicar timeout de turno."""

        cadena_objetivo = cadena or self.cadena
        indice = len(cadena_objetivo) if indice_bloque is None else int(indice_bloque)
        validadores = self.ordenar_validadores_autorizados(indice, cadena_objetivo)
        if not validadores:
            return []

        validador_base = self.obtener_validador_en_turno(indice, cadena_objetivo)
        if not validador_base:
            return []
        if self.poa_turn_timeout_seconds <= 0:
            return [validador_base]
        if indice <= 0 or indice > len(cadena_objetivo):
            return [validador_base]

        bloque_anterior = cadena_objetivo[indice - 1]
        if bloque_anterior.index == 0:
            return [validador_base]

        momento_anterior = bloque_anterior.timestamp_validacion or bloque_anterior.timestamp
        momento_referencia = referencia_tiempo or ahora_utc_iso()
        segundos_transcurridos = self._segundos_entre_timestamps(momento_anterior, momento_referencia)
        if segundos_transcurridos is None:
            return [validador_base]

        turnos_expirados = int(max(0, segundos_transcurridos) // self.poa_turn_timeout_seconds)
        cantidad_habilitada = min(len(validadores), turnos_expirados + 1)
        posicion_base = validadores.index(validador_base)
        return [
            validadores[(posicion_base + desplazamiento) % len(validadores)]
            for desplazamiento in range(cantidad_habilitada)
        ]

    def obtener_validador_activo(
        self,
        indice_bloque: Optional[int] = None,
        referencia_tiempo: Optional[str] = None,
    ) -> Optional[str]:
        """Devuelve el validador de la ventana actual tras posibles saltos de turno."""

        habilitados = self.obtener_validadores_habilitados(indice_bloque, referencia_tiempo)
        return habilitados[-1] if habilitados else None

    def crear_evento(
        self,
        product_id: str,
        event_type: str,
        location: str,
        actor_id: str,
        wallet: Wallet,
    ) -> EventoTrazabilidad:
        evento = EventoTrazabilidad.crear(
            product_id=product_id,
            event_type=event_type,
            location=location,
            actor_id=actor_id,
            public_key=wallet.clave_publica_pem,
        )
        evento.signature = wallet.firmar_datos(evento.datos_para_firma())
        return evento
    # Crea un evento de trazabilidad completo y lo firma con la wallet del actor.
    # Este evento aún no está en la blockchain, solo preparado para ser añadido.

    def agregar_evento_pendiente(
        self,
        datos_evento: Dict[str, Any],
        clave_publica_externa: Optional[str] = None,
    ) -> Dict[str, Any]:
        datos_normalizados = dict(datos_evento)
        if clave_publica_externa and not datos_normalizados.get("public_key"):
            datos_normalizados["public_key"] = clave_publica_externa
        if (
            clave_publica_externa
            and datos_normalizados.get("public_key")
            and datos_normalizados["public_key"] != clave_publica_externa
        ):
            raise ValueError("La clave publica externa no coincide con la clave publica del evento.")

        evento = EventoTrazabilidad.desde_diccionario(datos_normalizados)
        self._validar_evento_en_cadena(evento, incluir_eventos_pendientes=True)
        evento_dict = evento.a_diccionario()
        self.eventos_pendientes.append(evento_dict)
        return evento_dict

    def datos_para_firma_evento_sistema(self, evento_sistema: Dict[str, Any]) -> Dict[str, Any]:
        """Devuelve la carga canonica que aprueban los validadores para un evento de sistema."""

        datos = dict(evento_sistema)
        datos.pop("approvals", None)
        return datos

    def crear_evento_sistema_validador(
        self,
        tipo: str,
        validator_id: str,
        effective_from_block: int,
        approvals: Optional[List[Dict[str, Any]]] = None,
        validator_public_key: str = "",
    ) -> Dict[str, Any]:
        """Crea un evento de sistema para anadir o quitar validadores."""

        tipo_normalizado = str(tipo).strip().upper()
        if tipo_normalizado not in {SYSTEM_EVENT_VALIDATOR_ADDED, SYSTEM_EVENT_VALIDATOR_REMOVED}:
            raise ValueError("Tipo de evento de sistema no soportado.")

        evento = {
            "type": tipo_normalizado,
            "validator_id": str(validator_id).strip(),
            "effective_from_block": int(effective_from_block),
            "approvals": list(approvals or []),
        }
        if validator_public_key:
            evento["validator_public_key"] = str(validator_public_key).strip()
        return evento

    def agregar_evento_sistema_pendiente(self, evento_sistema: Dict[str, Any]) -> Dict[str, Any]:
        """Valida y guarda un evento de sistema pendiente de minado."""

        evento = dict(evento_sistema)
        self._validar_evento_sistema(evento, indice_bloque=len(self.cadena), cadena_contexto=self.cadena)
        clave = self._clave_evento_sistema(evento)
        if any(self._clave_evento_sistema(existente) == clave for existente in self.eventos_sistema_pendientes):
            raise ValueError("El evento de sistema ya esta pendiente.")
        self.eventos_sistema_pendientes.append(evento)
        return evento

# Recibe un evento externo, lo valida completamente y lo añade a la lista de pendientes.
# Aquí se evita que entren eventos falsos, duplicados o incoherentes.

    def minar_eventos_pendientes(
        self,
        creador_id: str,
        wallet_creador: Wallet,
        validador_id: Optional[str] = None,
        wallet_validador: Optional[Wallet] = None,
    ) -> Optional[Block]:
        if not self.eventos_pendientes and not self.eventos_sistema_pendientes:
            return None

        if not str(creador_id).strip():
            raise ValueError("Debes indicar el identificador del creador del bloque.")

        if wallet_validador is None:
            wallet_validador = wallet_creador
        if not str(validador_id or "").strip():
            validador_id = creador_id
        if not self.es_validador_autorizado(validador_id):
            raise ValueError("Nodo no autorizado para validar bloques")

        validadores_habilitados = self.obtener_validadores_habilitados(len(self.cadena))
        if validadores_habilitados and validador_id not in validadores_habilitados:
            raise ValueError(
                "No es el turno del validador indicado. "
                f"En esta altura pueden validar: {', '.join(validadores_habilitados)}."
            )

        bloque = Block(
            index=len(self.cadena),
            timestamp=ahora_utc_iso(),
            events=self.eventos_pendientes.copy(),
            previous_hash=self.ultimo_bloque.hash,
            system_events=self.eventos_sistema_pendientes.copy(),
        )
        bloque.sellar()
        bloque.firmar_creador(creador_id=creador_id, wallet_creador=wallet_creador)
        bloque.firmar_validador(
            validador_id=validador_id,
            wallet_validador=wallet_validador,
        )
        self.cadena.append(bloque)
        self._actualizar_indice_con_bloque(bloque)
        self.eventos_pendientes.clear()
        self.eventos_sistema_pendientes.clear()
        self._actualizar_validadores_actuales()
        return bloque
# Agrupa los eventos pendientes en un nuevo bloque.
# Calcula su hash, lo enlaza con el bloque anterior y lo añade a la cadena.
# Después limpia la lista de eventos pendientes.

    def obtener_historial_producto(self, product_id: str) -> List[Dict[str, Any]]:
        historial: List[Dict[str, Any]] = []
        for bloque in self.cadena:
            for evento in bloque.events:
                if evento.get("product_id") == product_id:
                    historial.append(evento)
        return sorted(historial, key=lambda item: item["timestamp"])

    def obtener_estado_producto(self, product_id: str) -> Optional[Dict[str, Any]]:
        estado = self.estado_actual_por_producto.get(product_id)
        if estado is None:
            return None

        print(f"[blockchain] estado obtenido desde indice: {product_id}")
        return {
            "product_id": product_id,
            **dict(estado),
        }

    def reconstruir_indice_estados(self, emitir_logs: bool = True) -> None:
        self.estado_actual_por_producto = {}
        for bloque in self.cadena:
            self._actualizar_indice_con_bloque(bloque, emitir_logs=False)

        if emitir_logs:
            print(
                "[blockchain] indice reconstruido: "
                f"{len(self.estado_actual_por_producto)} productos indexados"
            )


# Devuelve todos los eventos de un producto ordenados por fecha.
# Permite reconstruir su historial completo dentro de la blockchain.

    def es_cadena_valida(self, cadena: Optional[List[Block]] = None) -> bool:
        cadena_objetivo = cadena or self.cadena
        if not cadena_objetivo:
            return False

        bloque_genesis_esperado = self._crear_bloque_genesis()
        if cadena_objetivo[0].a_diccionario() != bloque_genesis_esperado.a_diccionario():
            return False

        ids_evento: Set[str] = set()
        estado_por_producto: Dict[str, str] = {}

        for indice, bloque in enumerate(cadena_objetivo):
            if bloque.hash != bloque.calcular_hash():
                return False
            if bloque.index != indice:
                return False

            if indice > 0:
                bloque_anterior = cadena_objetivo[indice - 1]
                if bloque.previous_hash != bloque_anterior.hash:
                    return False
                try:
                    self._validar_firmas_bloque(bloque, cadena_contexto=cadena_objetivo)
                    self._validar_eventos_sistema_bloque(
                        bloque.system_events,
                        indice_bloque=bloque.index,
                        cadena_contexto=cadena_objetivo,
                    )
                except ValueError:
                    return False

            for datos_evento in bloque.events:
                try:
                    evento = EventoTrazabilidad.desde_diccionario(datos_evento)
                    self._validar_evento_aislado(evento)
                except ValueError:
                    return False

                if evento.event_id in ids_evento:
                    return False

                try:
                    self._validar_secuencia_evento(
                        evento=evento,
                        existe_producto=evento.product_id in estado_por_producto,
                        estado_anterior=estado_por_producto.get(evento.product_id),
                    )
                except ValueError:
                    return False

                estado_por_producto[evento.product_id] = evento.event_type
                ids_evento.add(evento.event_id)

        return True
# Verifica que toda la cadena es válida.
# Comprueba hashes, enlaces entre bloques y validez de todos los eventos.
# Detecta cualquier manipulación o inconsistencia.

    def validar_cadena_datos(self, datos_cadena: List[Dict[str, Any]]) -> bool:
        try:
            cadena = [Block.desde_diccionario(datos_bloque) for datos_bloque in datos_cadena]
        except (KeyError, TypeError, ValueError):
            return False
        return self.es_cadena_valida(cadena)
# Valida una cadena recibida en formato JSON antes de aceptarla.
# Evita que un nodo adopte una blockchain corrupta o manipulada.

    def cargar_cadena_desde_datos(self, datos_cadena: List[Dict[str, Any]]) -> bool:
        if not self.validar_cadena_datos(datos_cadena):
            return False
        self.cadena = [Block.desde_diccionario(datos_bloque) for datos_bloque in datos_cadena]
        self.reconstruir_indice_estados()
        self._actualizar_validadores_actuales()
        self.eventos_pendientes = self._sanear_eventos_pendientes(self.eventos_pendientes)
        return True

    def reemplazar_cadena(self, datos_cadena: List[Dict[str, Any]]) -> bool:
        if not self.validar_cadena_datos(datos_cadena):
            return False

        nueva_cadena = [Block.desde_diccionario(datos_bloque) for datos_bloque in datos_cadena]
        if self.comparar_cadenas(nueva_cadena, self.cadena) <= 0:
            return False

        cadena_anterior = [Block.desde_diccionario(bloque.a_diccionario()) for bloque in self.cadena]
        pendientes_anteriores = [dict(evento) for evento in self.eventos_pendientes]
        eventos_huerfanos = self._obtener_eventos_huerfanos(cadena_anterior, nueva_cadena)
        self.cadena = nueva_cadena
        self.reconstruir_indice_estados()
        self._actualizar_validadores_actuales()
        eventos_a_recuperar = self._fusionar_eventos_unicos(pendientes_anteriores, eventos_huerfanos)
        self.eventos_pendientes = self._sanear_eventos_pendientes(eventos_a_recuperar)
        return True
# Reemplaza la cadena local si recibe una cadena válida más larga.
# Implementa un consenso simple entre nodos.

    def agregar_bloque_recibido(self, datos_bloque: Dict[str, Any]) -> Dict[str, Any]:
        """Valida y añade un bloque recibido desde otro nodo."""

        try:
            bloque = Block.desde_diccionario(datos_bloque)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("El bloque recibido no tiene una estructura valida.") from exc

        if bloque.index == 0:
            raise ValueError("El bloque genesis no se acepta mediante propagacion remota.")

        if self.contiene_bloque(bloque.hash):
            return {
                "agregado": False,
                "duplicado": True,
                "mensaje": "Bloque ya existente. Ignorado.",
                "bloque": bloque,
            }

        if bloque.hash != bloque.calcular_hash():
            raise ValueError("El hash del bloque recibido no es valido.")

        if bloque.index == len(self.cadena) - 1 and len(self.cadena) > 1:
            bloque_actual_misma_altura = self.cadena[bloque.index]
            if (
                bloque.previous_hash == self.cadena[bloque.index - 1].hash
                and bloque.hash != bloque_actual_misma_altura.hash
            ):
                raise ValueError(
                    "Existe una bifurcacion valida en esta altura. "
                    "Ejecuta la resolucion de conflictos para elegir la rama canonica."
                )

        if bloque.index != len(self.cadena):
            raise ValueError("El indice del bloque recibido no coincide con la longitud esperada.")

        if bloque.previous_hash != self.ultimo_bloque.hash:
            raise ValueError("El previous_hash del bloque recibido no coincide con el ultimo bloque local.")

        cadena_temporal = self.cadena + [bloque]
        self._validar_firmas_bloque(bloque, cadena_contexto=cadena_temporal)
        if not self.es_cadena_valida(cadena_temporal):
            raise ValueError("El bloque recibido invalida la cadena local.")

        self.cadena.append(bloque)
        self._actualizar_indice_con_bloque(bloque)
        self._actualizar_validadores_actuales()
        self._eliminar_eventos_pendientes_confirmados(bloque.events)
        return {
            "agregado": True,
            "duplicado": False,
            "mensaje": "Bloque agregado correctamente a la cadena local.",
            "bloque": bloque,
        }

    def a_diccionario(self) -> Dict[str, Any]:
        return {
            "length": len(self.cadena),
            "chain": [bloque.a_diccionario() for bloque in self.cadena],
            "pending_events": self.eventos_pendientes,
            "pending_system_events": self.eventos_sistema_pendientes,
        }

    def contiene_evento(self, event_id: str) -> bool:
        if any(evento.get("event_id") == event_id for evento in self.eventos_pendientes):
            return True
        for bloque in self.cadena:
            if any(evento.get("event_id") == event_id for evento in bloque.events):
                return True
        return False
# Comprueba si un evento ya existe en la blockchain o en pendientes.
# Evita duplicados.

    def contiene_bloque(self, hash_bloque: str) -> bool:
        """Comprueba si un bloque ya forma parte de la cadena local."""

        return any(bloque.hash == hash_bloque for bloque in self.cadena)

    def comparar_cadenas(self, cadena_candidata: List[Block], cadena_base: List[Block]) -> int:
        """Compara dos cadenas válidas y decide cuál debe ser la canónica."""

        if len(cadena_candidata) != len(cadena_base):
            return 1 if len(cadena_candidata) > len(cadena_base) else -1

        for bloque_candidato, bloque_base in zip(cadena_candidata, cadena_base):
            if bloque_candidato.hash == bloque_base.hash:
                continue
            return self._comparar_bloques_en_bifurcacion(bloque_candidato, bloque_base)

        return 0

    def comparar_cadenas_datos(
        self,
        datos_cadena_candidata: List[Dict[str, Any]],
        datos_cadena_base: List[Dict[str, Any]],
    ) -> int:
        """Compara dos cadenas serializadas en formato JSON."""

        cadena_candidata = [Block.desde_diccionario(datos_bloque) for datos_bloque in datos_cadena_candidata]
        cadena_base = [Block.desde_diccionario(datos_bloque) for datos_bloque in datos_cadena_base]
        return self.comparar_cadenas(cadena_candidata, cadena_base)

    def es_validador_autorizado(self, validador_id: Optional[str]) -> bool:
        """Comprueba si un identificador pertenece al conjunto PoA autorizado."""

        if not str(validador_id or "").strip():
            return False
        if not self.validadores_autorizados:
            return True
        return str(validador_id).strip() in self.validadores_autorizados

    def _sanear_eventos_pendientes(self, eventos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        eventos_validos: List[Dict[str, Any]] = []
        blockchain_auxiliar = Blockchain(
            validadores_autorizados=self.validadores_autorizados,
            claves_validadores=self.claves_validadores,
            poa_turn_timeout_seconds=self.poa_turn_timeout_seconds,
        )
        blockchain_auxiliar.cadena = [Block.desde_diccionario(bloque.a_diccionario()) for bloque in self.cadena]
        blockchain_auxiliar.reconstruir_indice_estados(emitir_logs=False)

        for datos_evento in eventos:
            try:
                blockchain_auxiliar.agregar_evento_pendiente(datos_evento)
            except ValueError:
                continue
            eventos_validos.append(dict(datos_evento))

        return eventos_validos

    def _fusionar_eventos_unicos(self, *colecciones: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Combina eventos evitando duplicados por event_id y preservando el orden."""

        fusionados: List[Dict[str, Any]] = []
        vistos: Set[str] = set()

        for coleccion in colecciones:
            for evento in coleccion:
                event_id = str(evento.get("event_id", "")).strip()
                if not event_id or event_id in vistos:
                    continue
                fusionados.append(dict(evento))
                vistos.add(event_id)

        return fusionados

    def _obtener_eventos_huerfanos(
        self,
        cadena_anterior: List[Block],
        nueva_cadena: List[Block],
    ) -> List[Dict[str, Any]]:
        """Recupera eventos de la rama descartada para reintentarlos si siguen siendo válidos."""

        ids_eventos_nuevos = {
            str(evento.get("event_id", "")).strip()
            for bloque in nueva_cadena
            for evento in bloque.events
            if str(evento.get("event_id", "")).strip()
        }

        eventos_huerfanos: List[Dict[str, Any]] = []
        for bloque in cadena_anterior[1:]:
            for evento in bloque.events:
                event_id = str(evento.get("event_id", "")).strip()
                if not event_id or event_id in ids_eventos_nuevos:
                    continue
                eventos_huerfanos.append(dict(evento))

        return eventos_huerfanos

    def _eliminar_eventos_pendientes_confirmados(self, eventos_bloque: List[Dict[str, Any]]) -> None:
        """Elimina del pool local los eventos ya confirmados en un bloque aceptado."""

        ids_confirmados = {evento.get("event_id") for evento in eventos_bloque}
        self.eventos_pendientes = [
            evento
            for evento in self.eventos_pendientes
            if evento.get("event_id") not in ids_confirmados
        ]

    def _validar_evento_en_cadena(
        self,
        evento: EventoTrazabilidad,
        incluir_eventos_pendientes: bool,
    ) -> None:
        self._validar_evento_aislado(evento)

        if self.contiene_evento(evento.event_id):
            raise ValueError(f"El event_id '{evento.event_id}' ya existe en la blockchain o en pendientes.")

        existe_producto, estado_anterior = self._obtener_contexto_producto_para_validacion(
            producto_id=evento.product_id,
            incluir_eventos_pendientes=incluir_eventos_pendientes,
        )
        self._validar_secuencia_evento(
            evento=evento,
            existe_producto=existe_producto,
            estado_anterior=estado_anterior,
        )

    def _validar_evento_aislado(self, evento: EventoTrazabilidad) -> None:
        if not evento.signature:
            raise ValueError("La firma del evento no puede estar vacia.")
        if not Wallet.verificar_firma(
            evento.datos_para_firma(),
            evento.signature,
            evento.public_key,
        ):
            raise ValueError("La firma del evento no es valida para la clave publica indicada.")

    def _validar_secuencia_evento(
        self,
        evento: EventoTrazabilidad,
        existe_producto: bool,
        estado_anterior: Optional[str],
    ) -> None:
        if not existe_producto and evento.event_type != "CREATED":
            raise ValueError("El primer evento de un producto debe ser CREATED.")

        if existe_producto and evento.event_type == "CREATED":
            raise ValueError("No se permite mas de un evento CREATED para el mismo producto.")

        if estado_anterior in ESTADOS_FINALES:
            raise ValueError("No se permiten eventos adicionales tras DELIVERED.")

        transiciones_permitidas = TRANSICIONES_VALIDAS.get(estado_anterior, set())
        if evento.event_type not in transiciones_permitidas:
            estado_legible = estado_anterior or "INICIO"
            raise ValueError(
                f"Transicion de trazabilidad no valida: {estado_legible} -> {evento.event_type}."
            )

    def _validar_eventos_sistema_bloque(
        self,
        eventos_sistema: List[Dict[str, Any]],
        indice_bloque: int,
        cadena_contexto: List[Block],
    ) -> None:
        for evento_sistema in eventos_sistema:
            self._validar_evento_sistema(evento_sistema, indice_bloque, cadena_contexto)

    def _validar_evento_sistema(
        self,
        evento_sistema: Dict[str, Any],
        indice_bloque: int,
        cadena_contexto: List[Block],
    ) -> None:
        firmantes_validos, total_validadores = self.validar_propuesta_evento_sistema(
            evento_sistema,
            indice_bloque,
            cadena_contexto,
        )

        if len(firmantes_validos) <= total_validadores / 2:
            raise ValueError("El evento de sistema no tiene aprobaciones de mas del 50% de validadores.")

    def validar_propuesta_evento_sistema(
        self,
        evento_sistema: Dict[str, Any],
        indice_bloque: Optional[int] = None,
        cadena_contexto: Optional[List[Block]] = None,
    ) -> tuple[Set[str], int]:
        """Valida una propuesta de sistema aunque aun no tenga mayoria suficiente."""

        cadena_objetivo = cadena_contexto or self.cadena
        indice = len(cadena_objetivo) if indice_bloque is None else int(indice_bloque)
        tipo = str(evento_sistema.get("type", "")).strip().upper()
        validator_id = str(evento_sistema.get("validator_id", "")).strip()
        if tipo not in {SYSTEM_EVENT_VALIDATOR_ADDED, SYSTEM_EVENT_VALIDATOR_REMOVED}:
            raise ValueError("Tipo de evento de sistema no soportado.")
        if not validator_id:
            raise ValueError("El evento de sistema debe incluir validator_id.")

        effective_from_block = int(evento_sistema.get("effective_from_block", 0))
        if effective_from_block <= indice:
            raise ValueError("effective_from_block debe ser posterior al bloque que aprueba el cambio.")

        validadores_actuales = self._obtener_validadores_para_altura(indice, cadena_objetivo)
        if not validadores_actuales:
            raise ValueError("No hay validadores activos para aprobar el evento de sistema.")
        if tipo == SYSTEM_EVENT_VALIDATOR_ADDED and validator_id in validadores_actuales:
            raise ValueError("El validador ya existe en el conjunto activo.")
        if tipo == SYSTEM_EVENT_VALIDATOR_ADDED and not str(evento_sistema.get("validator_public_key", "")).strip():
            raise ValueError("VALIDATOR_ADDED debe incluir validator_public_key.")
        if tipo == SYSTEM_EVENT_VALIDATOR_REMOVED and validator_id not in validadores_actuales:
            raise ValueError("El validador a eliminar no existe en el conjunto activo.")
        if tipo == SYSTEM_EVENT_VALIDATOR_REMOVED and len(validadores_actuales) <= 1:
            raise ValueError("No se puede eliminar el ultimo validador activo.")

        approvals = evento_sistema.get("approvals", [])
        if not isinstance(approvals, list):
            raise ValueError("approvals debe ser una lista.")

        carga = self.datos_para_firma_evento_sistema(evento_sistema)
        firmantes_validos: Set[str] = set()
        for approval in approvals:
            if not isinstance(approval, dict):
                continue
            approver_id = str(approval.get("validator_id", "")).strip()
            signature = str(approval.get("signature", "")).strip()
            public_key = str(approval.get("validator_public_key", "")).strip()
            if approver_id not in validadores_actuales or approver_id in firmantes_validos:
                continue
            public_key_registrada = self._obtener_clave_validador_para_altura(
                approver_id,
                indice,
                cadena_objetivo,
            )
            if public_key_registrada and public_key.strip() != public_key_registrada:
                continue
            if not signature or not public_key:
                continue
            if Wallet.verificar_firma(carga, signature, public_key):
                firmantes_validos.add(approver_id)

        return firmantes_validos, len(validadores_actuales)

    def _clave_evento_sistema(self, evento_sistema: Dict[str, Any]) -> tuple[str, str, int]:
        return (
            str(evento_sistema.get("type", "")).strip().upper(),
            str(evento_sistema.get("validator_id", "")).strip(),
            int(evento_sistema.get("effective_from_block", 0)),
        )

    def _obtener_validadores_para_altura(
        self,
        indice_bloque: Optional[int],
        cadena: Optional[List[Block]] = None,
    ) -> Set[str]:
        indice = len(cadena or self.cadena) if indice_bloque is None else int(indice_bloque)
        validadores = set(self.validadores_iniciales)
        cadena_objetivo = cadena or self.cadena
        for bloque in cadena_objetivo:
            if bloque.index >= indice:
                break
            for evento_sistema in bloque.system_events:
                effective_from_block = int(evento_sistema.get("effective_from_block", 0))
                if effective_from_block > indice:
                    continue
                tipo = str(evento_sistema.get("type", "")).strip().upper()
                validator_id = str(evento_sistema.get("validator_id", "")).strip()
                if tipo == SYSTEM_EVENT_VALIDATOR_ADDED and validator_id:
                    validadores.add(validator_id)
                elif tipo == SYSTEM_EVENT_VALIDATOR_REMOVED and validator_id:
                    validadores.discard(validator_id)
        return validadores

    def _actualizar_validadores_actuales(self) -> None:
        self.validadores_autorizados = self._obtener_validadores_para_altura(len(self.cadena), self.cadena)
        self.claves_validadores = self._obtener_claves_validadores_para_altura(len(self.cadena), self.cadena)

    def _obtener_claves_validadores_para_altura(
        self,
        indice_bloque: Optional[int],
        cadena: Optional[List[Block]] = None,
    ) -> Dict[str, str]:
        indice = len(cadena or self.cadena) if indice_bloque is None else int(indice_bloque)
        claves = dict(self.claves_validadores_iniciales)
        cadena_objetivo = cadena or self.cadena
        for bloque in cadena_objetivo:
            if bloque.index >= indice:
                break
            for evento_sistema in bloque.system_events:
                effective_from_block = int(evento_sistema.get("effective_from_block", 0))
                if effective_from_block > indice:
                    continue
                tipo = str(evento_sistema.get("type", "")).strip().upper()
                validator_id = str(evento_sistema.get("validator_id", "")).strip()
                if tipo == SYSTEM_EVENT_VALIDATOR_ADDED and validator_id:
                    clave_publica = str(evento_sistema.get("validator_public_key", "")).strip()
                    if clave_publica:
                        claves[validator_id] = clave_publica
                elif tipo == SYSTEM_EVENT_VALIDATOR_REMOVED and validator_id:
                    claves.pop(validator_id, None)
        return claves

    def _obtener_clave_validador_para_altura(
        self,
        validator_id: str,
        indice_bloque: Optional[int],
        cadena: Optional[List[Block]] = None,
    ) -> str:
        return self._obtener_claves_validadores_para_altura(indice_bloque, cadena).get(
            str(validator_id).strip(),
            "",
        )

    def _validar_firmas_bloque(
        self,
        bloque: Block,
        cadena_contexto: Optional[List[Block]] = None,
    ) -> None:
        """Comprueba la firma del creador y la del validador del bloque bajo PoA basico."""

        if not bloque.tiene_firmas_bloque():
            raise ValueError("El bloque no contiene firmas completas de creador y validador.")
        if not self.es_validador_autorizado(bloque.validador_id):
            raise ValueError("El bloque ha sido firmado por un validador no autorizado.")
        clave_registrada = self._obtener_clave_validador_para_altura(
            bloque.validador_id,
            bloque.index,
            cadena_contexto,
        )
        if clave_registrada and bloque.validador_public_key.strip() != clave_registrada:
            raise ValueError("La clave publica del validador no coincide con la registrada.")
        validadores_habilitados = self.obtener_validadores_habilitados(
            bloque.index,
            referencia_tiempo=bloque.timestamp_validacion or bloque.timestamp,
            cadena=cadena_contexto,
        )
        if validadores_habilitados and bloque.validador_id not in validadores_habilitados:
            raise ValueError(
                "El bloque no respeta el turno PoA de validacion. "
                f"En la altura {bloque.index} pueden validar: {', '.join(validadores_habilitados)}."
            )
        if not bloque.validador_public_key:
            raise ValueError("El bloque no incluye validator_public_key.")

        if not Wallet.verificar_firma(
            bloque.datos_para_firma_creador(),
            bloque.firma_creador,
            bloque.creador_public_key,
        ):
            raise ValueError("La firma del creador del bloque no es valida.")

        if not Wallet.verificar_firma(
            bloque.datos_para_firma_validador(),
            bloque.firma_validador,
            clave_registrada or bloque.validador_public_key,
        ):
            raise ValueError("La firma del bloque no es valida.")

    def _comparar_bloques_en_bifurcacion(self, bloque_candidato: Block, bloque_base: Block) -> int:
        """Resuelve empates de longitud entre dos ramas válidas."""

        clave_candidata = self._clave_consenso_bloque(bloque_candidato)
        clave_base = self._clave_consenso_bloque(bloque_base)
        if clave_candidata == clave_base:
            return 0
        return 1 if clave_candidata < clave_base else -1

    def _clave_consenso_bloque(self, bloque: Block) -> tuple[str, str, str]:
        """Genera una clave estable para elegir una rama canónica en caso de empate."""

        return (
            str(bloque.timestamp_validacion or bloque.timestamp),
            str(bloque.validador_id),
            str(bloque.hash),
        )

    def _segundos_entre_timestamps(self, inicio: str, fin: str) -> Optional[float]:
        try:
            inicio_dt = datetime.fromisoformat(str(inicio).replace("Z", "+00:00"))
            fin_dt = datetime.fromisoformat(str(fin).replace("Z", "+00:00"))
        except ValueError:
            return None

        if inicio_dt.tzinfo is None:
            inicio_dt = inicio_dt.replace(tzinfo=timezone.utc)
        if fin_dt.tzinfo is None:
            fin_dt = fin_dt.replace(tzinfo=timezone.utc)
        return (fin_dt - inicio_dt).total_seconds()

    def _obtener_historial_eventos_producto(
        self,
        producto_id: str,
        incluir_eventos_pendientes: bool,
    ) -> List[EventoTrazabilidad]:
        historial: List[EventoTrazabilidad] = []
        for bloque in self.cadena:
            for datos_evento in bloque.events:
                if datos_evento.get("product_id") == producto_id:
                    historial.append(EventoTrazabilidad.desde_diccionario(datos_evento))

        if incluir_eventos_pendientes:
            for datos_evento in self.eventos_pendientes:
                if datos_evento.get("product_id") == producto_id:
                    historial.append(EventoTrazabilidad.desde_diccionario(datos_evento))

        return historial

    def _obtener_contexto_producto_para_validacion(
        self,
        producto_id: str,
        incluir_eventos_pendientes: bool,
    ) -> tuple[bool, Optional[str]]:
        estado_indexado = self.estado_actual_por_producto.get(producto_id)
        existe_producto = estado_indexado is not None
        estado_anterior = (
            str(estado_indexado.get("estado_actual"))
            if estado_indexado is not None
            else None
        )

        if not incluir_eventos_pendientes:
            return existe_producto, estado_anterior

        for datos_evento in self.eventos_pendientes:
            if datos_evento.get("product_id") != producto_id:
                continue

            evento_pendiente = EventoTrazabilidad.desde_diccionario(datos_evento)
            self._validar_secuencia_evento(
                evento=evento_pendiente,
                existe_producto=existe_producto,
                estado_anterior=estado_anterior,
            )
            existe_producto = True
            estado_anterior = evento_pendiente.event_type

        return existe_producto, estado_anterior

    def _actualizar_indice_con_bloque(self, bloque: Block, emitir_logs: bool = True) -> None:
        if bloque.index == 0:
            return

        for datos_evento in bloque.events:
            producto_id = str(datos_evento.get("product_id", "")).strip()
            if not producto_id:
                continue

            self.estado_actual_por_producto[producto_id] = {
                "estado_actual": datos_evento.get("event_type"),
                "timestamp_ultimo_evento": datos_evento.get("timestamp"),
                "actor_id_ultimo_evento": datos_evento.get("actor_id"),
                "ultimo_bloque": bloque.index,
                "hash_ultimo_bloque": bloque.hash,
            }
            if emitir_logs:
                print(
                    "[blockchain] producto actualizado: "
                    f"{producto_id} -> {datos_evento.get('event_type')}"
                )

    @property
    def chain(self) -> List[Block]:
        return self.cadena

    @chain.setter
    def chain(self, valor: List[Block]) -> None:
        self.cadena = valor

    @property
    def pending_events(self) -> List[Dict[str, Any]]:
        return self.eventos_pendientes

    @pending_events.setter
    def pending_events(self, valor: List[Dict[str, Any]]) -> None:
        self.eventos_pendientes = valor

    @property
    def last_block(self) -> Block:
        return self.ultimo_bloque

    def create_event(
        self,
        product_id: str,
        event_type: str,
        location: str,
        actor_id: str,
        wallet: Wallet,
    ) -> EventoTrazabilidad:
        return self.crear_evento(product_id, event_type, location, actor_id, wallet)

    def add_pending_event(
        self,
        event_data: Dict[str, Any],
        public_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.agregar_evento_pendiente(event_data, public_key)

    def contiene_event(self, event_id: str) -> bool:
        """Alias de compatibilidad para comprobar duplicados de eventos."""

        return self.contiene_evento(event_id)

    def contains_event(self, event_id: str) -> bool:
        """Alias en ingles de compatibilidad para comprobar duplicados."""

        return self.contiene_evento(event_id)

    def contains_block(self, hash_bloque: str) -> bool:
        """Alias en ingles de compatibilidad para comprobar duplicados de bloques."""

        return self.contiene_bloque(hash_bloque)

    def mine_pending_events(
        self,
        creador_id: str,
        wallet_creador: Wallet,
        validador_id: Optional[str] = None,
        wallet_validador: Optional[Wallet] = None,
    ) -> Optional[Block]:
        return self.minar_eventos_pendientes(
            creador_id=creador_id,
            wallet_creador=wallet_creador,
            validador_id=validador_id,
            wallet_validador=wallet_validador,
        )

    def configurar_validadores_autorizados(self, validadores_autorizados: Set[str]) -> None:
        self.validadores_autorizados = set(validadores_autorizados)

    def get_product_history(self, product_id: str) -> List[Dict[str, Any]]:
        return self.obtener_historial_producto(product_id)

    def get_product_state(self, product_id: str) -> Optional[Dict[str, Any]]:
        return self.obtener_estado_producto(product_id)

    def is_chain_valid(self, chain: Optional[List[Block]] = None) -> bool:
        return self.es_cadena_valida(chain)

    def valid_chain(self, chain_data: List[Dict[str, Any]]) -> bool:
        return self.validar_cadena_datos(chain_data)

    def to_dict(self) -> Dict[str, Any]:
        return self.a_diccionario()

    def replace_chain(self, chain_data: List[Dict[str, Any]]) -> bool:
        return self.reemplazar_cadena(chain_data)
