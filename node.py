"""Servicio de nodo para persistencia, red y consenso academico."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set
from urllib.parse import urlparse

import requests

from blockchain import Blockchain
from wallet import Wallet

TTL_GOSSIP_POR_DEFECTO = 3


class Node:
    """Orquesta la blockchain local, los nodos vecinos y la sincronizacion."""

    def __init__(
        self,
        node_id: str,
        node_url: str,
        storage_dir: str = "data",
        validadores_autorizados: Set[str] | None = None,
        claves_validadores: Dict[str, str] | None = None,
        poa_turn_timeout_seconds: int = 0,
    ) -> None:
        self.id_nodo = node_id
        self.url_nodo = self._normalizar_direccion(node_url)
        self.validadores_autorizados: Set[str] = set(validadores_autorizados or set())
        self.claves_validadores: Dict[str, str] = dict(claves_validadores or {})
        self.poa_turn_timeout_seconds = max(0, int(poa_turn_timeout_seconds))
        self.nodos_conocidos: Set[str] = set()
        self.p2p_peers_conocidos: Set[str] = set()
        self.directorio_storage = Path(storage_dir)
        self.directorio_storage.mkdir(parents=True, exist_ok=True)
        self.ruta_storage = self.directorio_storage / f"{self.id_nodo}_chain.json"
        # Ruta del archivo JSON donde este nodo guarda su copia local de la blockchain,
        # los eventos pendientes y la lista de nodos conocidos.
        self.blockchain = Blockchain(
            validadores_autorizados=self.validadores_autorizados,
            claves_validadores=self.claves_validadores,
            poa_turn_timeout_seconds=self.poa_turn_timeout_seconds,
        )
        self.seen_events: Set[str] = set()
        self.seen_blocks: Set[str] = set()
        self.propuestas_eventos_sistema: List[Dict[str, Any]] = []
        self._cargar_estado()
        self._reconstruir_memoria_mensajes()
# Inicializa el nodo con su identificador, URL y carpeta de almacenamiento.
# Crea una blockchain local y luego intenta recuperar el estado guardado en disco.

    def registrar_nodos(self, direcciones: Iterable[str], descubrir: bool = False) -> List[str]:
        registrados: List[str] = []
        for direccion in direcciones:
            normalizada = self._normalizar_direccion(direccion)
            if not normalizada:
                continue
            if normalizada == self.url_nodo:
                print(f"[{self.id_nodo}] peer ignorado por ser self: {normalizada}")
                continue
            if normalizada in self.nodos_conocidos:
                print(f"[{self.id_nodo}] peer ignorado por duplicado: {normalizada}")
                continue
            self.nodos_conocidos.add(normalizada)
            registrados.append(normalizada)

        if descubrir and registrados:
            for direccion in registrados:
                self._descubrir_desde_peer(direccion)
        self._guardar_estado()
        return registrados
    # Registra nuevos nodos vecinos.
# Evita registrar direcciones inválidas, duplicadas o la propia URL del nodo.

    def registrar_p2p_peers(self, direcciones: Iterable[str]) -> List[str]:
        registrados: List[str] = []
        for direccion in direcciones:
            normalizada = self._normalizar_p2p_peer(direccion)
            if not normalizada or normalizada in self.p2p_peers_conocidos:
                continue
            self.p2p_peers_conocidos.add(normalizada)
            registrados.append(normalizada)

        if registrados:
            self._guardar_estado()
        return registrados

    def listar_p2p_peers(self) -> List[str]:
        return sorted(self.p2p_peers_conocidos)

    def _normalizar_p2p_peer(self, direccion: str) -> str:
        candidata = str(direccion).strip()
        if not candidata:
            return ""
        if "/p2p/" not in candidata:
            return ""
        return candidata

    def bootstrap_desde_semillas(
        self,
        semillas: Iterable[str],
        *,
        descubrir: bool = True,
        resolver_conflictos: bool = False,
    ) -> Dict[str, Any]:
        """Registra nodos semilla y opcionalmente descubre peers y sincroniza."""

        semillas_limpias = [direccion for direccion in semillas if str(direccion).strip()]
        registrados = self.registrar_nodos(semillas_limpias, descubrir=descubrir)
        descubrimiento = None
        if descubrir:
            descubrimiento = self.descubrir_peers()

        resolucion = None
        if resolver_conflictos:
            resolucion = self.resolver_conflictos()

        return {
            "seed_nodes": semillas_limpias,
            "registered_nodes": registrados,
            "discovery": descubrimiento,
            "resolve": resolucion,
            "known_nodes": self.listar_nodos(),
        }

    def descubrir_peers(self) -> Dict[str, Any]:
        """Descubre peers nuevos consultando los peers ya conocidos."""

        peers_antes = sorted(self.nodos_conocidos)
        peers_descubiertos: Set[str] = set()

        for direccion_nodo in list(self.nodos_conocidos):
            nuevos = self._descubrir_desde_peer(direccion_nodo)
            peers_descubiertos.update(nuevos)

        self._guardar_estado()
        return {
            "peers_antes": peers_antes,
            "peers_descubiertos": sorted(peers_descubiertos),
            "peers_totales": sorted(self.nodos_conocidos),
        }

    def resolver_conflictos(self) -> Dict[str, Any]:
        reemplazada = False
        mejor_cadena = [bloque.a_diccionario() for bloque in self.blockchain.cadena]

        for direccion_nodo in self.nodos_conocidos:
            try:
                respuesta = requests.get(f"{direccion_nodo}/chain", timeout=5)
                respuesta.raise_for_status()
                carga = respuesta.json()
            except requests.RequestException:
                continue

            cadena_candidata = carga.get("chain", [])
            if not self.blockchain.validar_cadena_datos(cadena_candidata):
                continue

            if self.blockchain.comparar_cadenas_datos(cadena_candidata, mejor_cadena) > 0:
                mejor_cadena = cadena_candidata

        if mejor_cadena:
            reemplazada = self.blockchain.reemplazar_cadena(mejor_cadena)
            if reemplazada:
                self._reconstruir_memoria_mensajes()
                self._guardar_estado()

        return {
            "replaced": reemplazada,
            "length": len(self.blockchain.cadena),
            "chain": [bloque.a_diccionario() for bloque in self.blockchain.cadena],
        }

    # Consulta las cadenas de los nodos conocidos y aplica el consenso.
# Si encuentra una cadena válida más larga, reemplaza la cadena local.

    def agregar_evento(
        self,
        datos_evento: Dict[str, Any],
        clave_publica_externa: str | None = None,
        propagar: bool = True,
        ttl: int = TTL_GOSSIP_POR_DEFECTO,
        origin_node: str | None = None,
    ) -> Dict[str, Any]:
        self._asegurar_cadena_local_valida()
        ttl = self._normalizar_ttl(ttl)
        event_id = str(datos_evento.get("event_id", "")).strip()
        if origin_node:
            print(f"[{self.id_nodo}] evento recibido por gossip: {event_id or 'sin-id'}")

        if event_id and (event_id in self.seen_events or self.blockchain.contiene_event(event_id)):
            print(f"[{self.id_nodo}] evento ignorado por duplicado: {event_id}")
            return {
                "event": dict(datos_evento),
                "propagated_to": [],
                "duplicate": True,
                "recovered_events": [],
                "mensaje": "Evento ya existente. Ignorado.",
            }

        evento = self.blockchain.agregar_evento_pendiente(datos_evento, clave_publica_externa)

        self._marcar_evento_visto(evento.get("event_id", ""))

        propagados: List[str] = []
        if propagar:
            ttl_reenvio = ttl if not origin_node else ttl - 1
            if ttl_reenvio <= 0:
                print(f"[{self.id_nodo}] ttl agotado para evento: {evento.get('event_id', '')}")
            else:
                propagados = self.propagar_evento_gossip(
                    evento=evento,
                    ttl=ttl_reenvio,
                    origin_node=origin_node,
                )
        self._guardar_estado()
        return {
            "event": evento,
            "propagated_to": propagados,
            "duplicate": False,
            "recovered_events": [],
            "mensaje": "Evento anadido al pool de pendientes.",
        }
# Añade un evento a la blockchain local como pendiente.
# Si el evento es válido, puede propagarlo a otros nodos conocidos.
# Finalmente guarda el estado actualizado en disco.

    def _ejecutar_minado(
        self,
        creador_id: str,
        wallet_creador: Wallet,
        validador_id: str | None = None,
        wallet_validador: Wallet | None = None,
        propagar: bool = True,
        ttl: int = TTL_GOSSIP_POR_DEFECTO,
    ) -> Dict[str, Any]:
        self._asegurar_cadena_local_valida()
        ttl = self._normalizar_ttl(ttl)
        if not self.blockchain.es_validador_autorizado(self.id_nodo):
            raise ValueError("Nodo no autorizado para validar bloques")

        if validador_id and validador_id != self.id_nodo:
            raise ValueError("El validador del bloque debe coincidir con el nodo que realiza el minado.")

        if wallet_validador is None:
            if creador_id != self.id_nodo:
                raise ValueError(
                    "Debes proporcionar la clave privada del validador cuando el creador y el validador son distintos."
                )
            wallet_validador = wallet_creador

        validador_id = self.id_nodo
        bloque = self.blockchain.minar_eventos_pendientes(
            creador_id=creador_id,
            wallet_creador=wallet_creador,
            validador_id=validador_id,
            wallet_validador=wallet_validador,
        )
        if bloque:
            self._marcar_bloque_visto(bloque.hash)

        propagados: List[str] = []
        if bloque and propagar:
            propagados = self.propagar_bloque_gossip(
                bloque=bloque.a_diccionario(),
                ttl=ttl,
                origin_node=None,
            )
        self._guardar_estado()
        return {
            "created": bloque is not None,
            "block": bloque.a_diccionario() if bloque else None,
            "pending_events": len(self.blockchain.eventos_pendientes),
            "propagated_to": propagados,
        }
# Mina los eventos pendientes creando un nuevo bloque.
# Después propaga el bloque a otros nodos. `resolve_conflicts()` queda como respaldo.

    def minar(
        self,
        creador_id: str,
        wallet_creador: Wallet,
        validador_id: str | None = None,
        wallet_validador: Wallet | None = None,
        propagar: bool = True,
        ttl: int = TTL_GOSSIP_POR_DEFECTO,
    ) -> Dict[str, Any]:
        return self._ejecutar_minado(
            creador_id=creador_id,
            wallet_creador=wallet_creador,
            validador_id=validador_id,
            wallet_validador=wallet_validador,
            propagar=propagar,
            ttl=ttl,
        )

    def agregar_bloque_recibido(
        self,
        datos_bloque: Dict[str, Any],
        propagar: bool = True,
        ttl: int = TTL_GOSSIP_POR_DEFECTO,
        origin_node: str | None = None,
    ) -> Dict[str, Any]:
        ttl = self._normalizar_ttl(ttl)
        hash_bloque = str(datos_bloque.get("hash", ""))
        if origin_node:
            print(f"[{self.id_nodo}] bloque recibido por gossip: {hash_bloque}")
        else:
            print(f"[{self.id_nodo}] bloque recibido: {hash_bloque}")

        if hash_bloque and (hash_bloque in self.seen_blocks or self.blockchain.contiene_bloque(hash_bloque)):
            print(f"[{self.id_nodo}] bloque ignorado por duplicado: {hash_bloque}")
            return {
                "agregado": False,
                "duplicado": True,
                "mensaje": "Bloque ya existente. Ignorado.",
                "bloque": datos_bloque,
                "propagated_to": [],
            }

        try:
            resultado = self.blockchain.agregar_bloque_recibido(datos_bloque)
        except ValueError as exc:
            print(f"[{self.id_nodo}] bloque rechazado: {hash_bloque} -> {exc}")
            raise

        if resultado["duplicado"]:
            print(f"[{self.id_nodo}] bloque duplicado ignorado: {hash_bloque}")
            resultado["propagated_to"] = []
            return resultado

        self._marcar_bloque_visto(resultado["bloque"].hash)
        resultado["recovered_events"] = []
        self._guardar_estado()
        print(f"[{self.id_nodo}] bloque aceptado: {hash_bloque}")
        propagados: List[str] = []
        if propagar:
            ttl_reenvio = ttl if not origin_node else ttl - 1
            if ttl_reenvio <= 0:
                print(f"[{self.id_nodo}] ttl agotado para bloque: {hash_bloque}")
            else:
                propagados = self.propagar_bloque_gossip(
                    bloque=resultado["bloque"].a_diccionario(),
                    ttl=ttl_reenvio,
                    origin_node=origin_node,
                )
        resultado["propagated_to"] = propagados
        return resultado

    def listar_nodos(self) -> List[str]:
        return sorted(self.nodos_conocidos)

    def obtener_info_peers(self) -> Dict[str, Any]:
        """Devuelve la informacion minima para peer discovery."""

        return {
            "node_id": self.id_nodo,
            "node_url": self.url_nodo,
            "nodes": self.listar_nodos(),
            "p2p_known_peers": self.listar_p2p_peers(),
            "count": len(self.nodos_conocidos),
        }

    def obtener_estado_consenso(self) -> Dict[str, Any]:
        """Expone el estado actual del consenso PoA del nodo."""

        siguiente_altura = len(self.blockchain.cadena)
        validador_esperado = self.blockchain.obtener_validador_en_turno(siguiente_altura)
        validadores_habilitados = self.blockchain.obtener_validadores_habilitados(siguiente_altura)
        validador_turno = validadores_habilitados[-1] if validadores_habilitados else validador_esperado
        return {
            "node_id": self.id_nodo,
            "chain_valid": self.blockchain.es_cadena_valida(),
            "chain_length": len(self.blockchain.cadena),
            "next_block_index": siguiente_altura,
            "validator_in_turn": validador_turno,
            "expected_validator": validador_esperado,
            "eligible_validators": validadores_habilitados,
            "turn_timeout_seconds": self.poa_turn_timeout_seconds,
            "node_can_mine_now": self.id_nodo in validadores_habilitados,
            "authorized_validators": sorted(self.blockchain.validadores_autorizados),
            "registered_validator_keys": sorted(self.blockchain.claves_validadores.keys()),
            "known_nodes": self.listar_nodos(),
            "p2p_known_peers": self.listar_p2p_peers(),
            "pending_events": len(self.blockchain.eventos_pendientes),
        }

    def obtener_eventos_pendientes(self) -> List[Dict[str, Any]]:
        return list(self.blockchain.eventos_pendientes)

    def obtener_eventos_sistema_pendientes(self) -> List[Dict[str, Any]]:
        return list(self.blockchain.eventos_sistema_pendientes)

    def agregar_evento_sistema(
        self,
        evento_sistema: Dict[str, Any],
    ) -> Dict[str, Any]:
        self._asegurar_cadena_local_valida()
        evento = self.blockchain.agregar_evento_sistema_pendiente(evento_sistema)
        self._guardar_estado()
        return {
            "system_event": evento,
            "mensaje": "Evento de sistema anadido al pool de pendientes.",
        }

    def obtener_propuestas_eventos_sistema(self) -> List[Dict[str, Any]]:
        return [dict(propuesta) for propuesta in self.propuestas_eventos_sistema]

    def agregar_propuesta_evento_sistema(self, evento_sistema: Dict[str, Any]) -> Dict[str, Any]:
        """Guarda/mezcla una propuesta de sistema; si tiene mayoria, pasa a pendientes."""

        self._asegurar_cadena_local_valida()
        propuesta = dict(evento_sistema)
        firmantes_validos, total_validadores = self.blockchain.validar_propuesta_evento_sistema(propuesta)
        clave = self.blockchain._clave_evento_sistema(propuesta)

        existente = None
        for propuesta_existente in self.propuestas_eventos_sistema:
            if self.blockchain._clave_evento_sistema(propuesta_existente) == clave:
                existente = propuesta_existente
                break

        if existente is not None:
            propuesta = self._fusionar_aprobaciones_propuesta(existente, propuesta)
            self.propuestas_eventos_sistema.remove(existente)
            firmantes_validos, total_validadores = self.blockchain.validar_propuesta_evento_sistema(propuesta)

        tiene_mayoria = len(firmantes_validos) > total_validadores / 2
        if tiene_mayoria:
            if not any(
                self.blockchain._clave_evento_sistema(evento) == clave
                for evento in self.blockchain.eventos_sistema_pendientes
            ):
                self.blockchain.agregar_evento_sistema_pendiente(propuesta)
            mensaje = "Propuesta de sistema aprobada y anadida a pendientes."
        else:
            self.propuestas_eventos_sistema.append(propuesta)
            mensaje = "Propuesta de sistema guardada a la espera de mas aprobaciones."

        self._guardar_estado()
        return {
            "system_event": propuesta,
            "approved": tiene_mayoria,
            "valid_approvals": sorted(firmantes_validos),
            "required": int(total_validadores // 2 + 1),
            "mensaje": mensaje,
        }

    def aprobar_propuesta_evento_sistema(
        self,
        tipo: str,
        validator_id: str,
        effective_from_block: int,
        approver_id: str,
        wallet_approver: Wallet,
    ) -> Dict[str, Any]:
        clave_buscada = (str(tipo).strip().upper(), str(validator_id).strip(), int(effective_from_block))
        propuesta = None
        for candidata in self.propuestas_eventos_sistema:
            if self.blockchain._clave_evento_sistema(candidata) == clave_buscada:
                propuesta = dict(candidata)
                break

        if propuesta is None:
            raise ValueError("No existe una propuesta pendiente con esos datos.")

        payload = self.blockchain.datos_para_firma_evento_sistema(propuesta)
        approval = {
            "validator_id": str(approver_id).strip(),
            "validator_public_key": wallet_approver.public_key_pem,
            "signature": wallet_approver.sign_event(payload),
        }
        propuesta["approvals"] = [
            aprobacion
            for aprobacion in propuesta.get("approvals", [])
            if str(aprobacion.get("validator_id", "")).strip() != approver_id
        ] + [approval]
        return self.agregar_propuesta_evento_sistema(propuesta)

    def obtener_estado_producto(self, product_id: str) -> Dict[str, Any] | None:
        return self.blockchain.obtener_estado_producto(product_id)

    def propagar_evento_gossip(
        self,
        evento: Dict[str, Any],
        ttl: int,
        origin_node: str | None = None,
    ) -> List[str]:
        ttl = self._normalizar_ttl(ttl, por_defecto=0)
        if ttl <= 0:
            print(f"[{self.id_nodo}] ttl agotado para evento: {evento.get('event_id', '')}")
            return []

        nodos_confirmados: List[str] = []
        event_id = str(evento.get("event_id", ""))
        origen_normalizado = self._normalizar_direccion(origin_node or "")
        for direccion_nodo in self.nodos_conocidos:
            if direccion_nodo == self.url_nodo:
                continue
            if origen_normalizado and direccion_nodo == origen_normalizado:
                continue
            try:
                respuesta = requests.post(
                    f"{direccion_nodo}/events/new",
                    json={
                        "event": evento,
                        "public_key": evento.get("public_key", ""),
                        "propagate": True,
                        "ttl": ttl,
                        "origin_node": self.url_nodo,
                    },
                    timeout=5,
                )
                if respuesta.ok:
                    nodos_confirmados.append(direccion_nodo)
                    print(f"[{self.id_nodo}] reenviado a peer {direccion_nodo}: evento {event_id}")
            except requests.RequestException:
                continue
        return nodos_confirmados
# Envía un evento válido a los nodos vecinos.
# Usa memoria de mensajes vistos, ttl y origin_node para evitar bucles simples.

    def propagar_bloque_gossip(
        self,
        bloque: Dict[str, Any],
        ttl: int,
        origin_node: str | None = None,
    ) -> List[str]:
        ttl = self._normalizar_ttl(ttl, por_defecto=0)
        if ttl <= 0:
            print(f"[{self.id_nodo}] ttl agotado para bloque: {bloque.get('hash', '')}")
            return []

        nodos_confirmados: List[str] = []
        hash_bloque = str(bloque.get("hash", ""))
        origen_normalizado = self._normalizar_direccion(origin_node or "")
        for direccion_nodo in self.nodos_conocidos:
            if direccion_nodo == self.url_nodo:
                continue
            if origen_normalizado and direccion_nodo == origen_normalizado:
                continue
            try:
                respuesta = requests.post(
                    f"{direccion_nodo}/blocks/new",
                    json={
                        "block": bloque,
                        "propagate": True,
                        "ttl": ttl,
                        "origin_node": self.url_nodo,
                    },
                    timeout=5,
                )
                if respuesta.status_code in {200, 201}:
                    nodos_confirmados.append(direccion_nodo)
                    print(f"[{self.id_nodo}] reenviado a peer {direccion_nodo}: bloque {hash_bloque}")
            except requests.RequestException:
                continue
        return nodos_confirmados

    def _descubrir_desde_peer(self, direccion_nodo: str) -> Set[str]:
        """Consulta un peer y aprende sus vecinos conocidos."""

        print(f"[{self.id_nodo}] peer consultado: {direccion_nodo}")
        try:
            respuesta = requests.get(f"{direccion_nodo}/peers", timeout=5)
            respuesta.raise_for_status()
            carga = respuesta.json()
        except requests.RequestException:
            return set()

        candidatos = [carga.get("node_url", "")] + list(carga.get("nodes", []))
        nuevos_peers: Set[str] = set()

        for candidato in candidatos:
            normalizada = self._normalizar_direccion(str(candidato))
            if not normalizada:
                continue
            if normalizada == self.url_nodo:
                print(f"[{self.id_nodo}] peer ignorado por ser self: {normalizada}")
                continue
            if normalizada in self.nodos_conocidos:
                print(f"[{self.id_nodo}] peer ignorado por duplicado: {normalizada}")
                continue
            self.nodos_conocidos.add(normalizada)
            nuevos_peers.add(normalizada)
            print(f"[{self.id_nodo}] peer descubierto: {normalizada}")

        return nuevos_peers

    def _reconstruir_memoria_mensajes(self) -> None:
        """Reconstruye las memorias de eventos y bloques vistos desde el estado local."""

        self.seen_events = set()
        self.seen_blocks = set()
        self.seen_events = {
            evento.get("event_id", "")
            for evento in self.blockchain.eventos_pendientes
            if evento.get("event_id")
        }
        for bloque in self.blockchain.cadena:
            if bloque.hash:
                self.seen_blocks.add(bloque.hash)
            for evento in bloque.events:
                event_id = evento.get("event_id", "")
                if event_id:
                    self.seen_events.add(event_id)

    def _marcar_evento_visto(self, event_id: str) -> None:
        if event_id:
            self.seen_events.add(event_id)

    def _marcar_bloque_visto(self, hash_bloque: str) -> None:
        if hash_bloque:
            self.seen_blocks.add(hash_bloque)

    def _fusionar_aprobaciones_propuesta(
        self,
        propuesta_base: Dict[str, Any],
        propuesta_nueva: Dict[str, Any],
    ) -> Dict[str, Any]:
        fusionada = dict(propuesta_base)
        aprobaciones: Dict[str, Dict[str, Any]] = {}
        for aprobacion in list(propuesta_base.get("approvals", [])) + list(propuesta_nueva.get("approvals", [])):
            if not isinstance(aprobacion, dict):
                continue
            validator_id = str(aprobacion.get("validator_id", "")).strip()
            if validator_id:
                aprobaciones[validator_id] = dict(aprobacion)
        fusionada["approvals"] = list(aprobaciones.values())
        return fusionada

    def _normalizar_ttl(self, ttl: Any, por_defecto: int = TTL_GOSSIP_POR_DEFECTO) -> int:
        try:
            ttl_normalizado = int(ttl)
        except (TypeError, ValueError):
            return max(0, por_defecto)
        return max(0, ttl_normalizado)

    def _asegurar_cadena_local_valida(self) -> None:
        """Bloquea operaciones locales si la cadena del nodo es inconsistente."""

        if not self.blockchain.es_cadena_valida():
            raise ValueError(
                "La cadena local del nodo es invalida. "
                "Debes resolver conflictos o resincronizar antes de continuar."
            )

    def _notificar_actualizacion_cadena(self) -> List[str]:
        nodos_notificados: List[str] = []
        for direccion_nodo in self.nodos_conocidos:
            try:
                respuesta = requests.get(f"{direccion_nodo}/nodes/resolve", timeout=5)
                if respuesta.ok:
                    nodos_notificados.append(direccion_nodo)
            except requests.RequestException:
                continue
        return nodos_notificados
# Notifica a los nodos vecinos que la cadena local ha cambiado.
# Los vecinos ejecutan su resolución de conflictos para sincronizarse.

    def _normalizar_direccion(self, direccion: str) -> str:
        candidata = direccion.strip()
        if not candidata:
            return ""

        if not candidata.startswith(("http://", "https://")):
            candidata = f"http://{candidata}"

        parseada = urlparse(candidata)
        if not parseada.netloc:
            return ""
        return f"{parseada.scheme}://{parseada.netloc}"
# Normaliza las direcciones de nodos para que todas tengan el mismo formato.
# Si falta http://, lo añade automáticamente.

    def _cargar_estado(self) -> None:
        if not self.ruta_storage.exists():
            return

        try:
            datos = json.loads(self.ruta_storage.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        datos_cadena = datos.get("chain", [])
        if datos_cadena:
            self.blockchain.cargar_cadena_desde_datos(datos_cadena)

        eventos_pendientes = []
        for datos_evento in datos.get("pending_events", []):
            try:
                evento = self.blockchain.agregar_evento_pendiente(datos_evento)
            except ValueError:
                continue
            eventos_pendientes.append(evento)
        self.blockchain.eventos_pendientes = eventos_pendientes
        eventos_sistema_pendientes = []
        for evento_sistema in datos.get("pending_system_events", []):
            try:
                evento = self.blockchain.agregar_evento_sistema_pendiente(evento_sistema)
            except (TypeError, ValueError):
                continue
            eventos_sistema_pendientes.append(evento)
        self.blockchain.eventos_sistema_pendientes = eventos_sistema_pendientes
        self.propuestas_eventos_sistema = []
        for propuesta in datos.get("system_event_proposals", []):
            if not isinstance(propuesta, dict):
                continue
            try:
                self.blockchain.validar_propuesta_evento_sistema(propuesta)
            except ValueError:
                continue
            self.propuestas_eventos_sistema.append(dict(propuesta))

        self.nodos_conocidos = {
            direccion
            for direccion in (
                self._normalizar_direccion(direccion) for direccion in datos.get("nodes", [])
            )
            if direccion and direccion != self.url_nodo
        }
        self.p2p_peers_conocidos = {
            direccion
            for direccion in (
                self._normalizar_p2p_peer(direccion) for direccion in datos.get("p2p_known_peers", [])
            )
            if direccion
        }

# Recupera desde disco la cadena, los eventos pendientes y los nodos conocidos.
# Permite que el nodo conserve su estado tras reiniciarse.

    def _guardar_estado(self) -> None:
        carga = {
            "node_id": self.id_nodo,
            "node_url": self.url_nodo,
            "chain": [bloque.a_diccionario() for bloque in self.blockchain.cadena],
            "pending_events": self.blockchain.eventos_pendientes,
            "pending_system_events": self.blockchain.eventos_sistema_pendientes,
            "system_event_proposals": self.propuestas_eventos_sistema,
            "nodes": sorted(self.nodos_conocidos),
            "p2p_known_peers": sorted(self.p2p_peers_conocidos),
        }
        self.ruta_storage.write_text(json.dumps(carga, indent=2), encoding="utf-8")
# Guarda el estado actual del nodo en un archivo JSON.
# Incluye la blockchain, eventos pendientes y nodos conocidos.


    @property
    def node_id(self) -> str:
        return self.id_nodo

    @property
    def node_url(self) -> str:
        return self.url_nodo

    @property
    def nodes(self) -> Set[str]:
        return self.nodos_conocidos

    @property
    def authorized_validators(self) -> Set[str]:
        return set(self.blockchain.validadores_autorizados)

    def register_nodes(self, addresses: Iterable[str], discover: bool = False) -> List[str]:
        return self.registrar_nodos(addresses, descubrir=discover)

    def resolve_conflicts(self) -> Dict[str, Any]:
        return self.resolver_conflictos()

    def add_event(
        self,
        event_data: Dict[str, Any],
        public_key: str | None = None,
        propagar: bool = True,
        ttl: int = TTL_GOSSIP_POR_DEFECTO,
        origin_node: str | None = None,
    ) -> Dict[str, Any]:
        return self.agregar_evento(
            event_data,
            public_key,
            propagar=propagar,
            ttl=ttl,
            origin_node=origin_node,
        )

    def add_received_block(
        self,
        block_data: Dict[str, Any],
        propagar: bool = True,
        ttl: int = TTL_GOSSIP_POR_DEFECTO,
        origin_node: str | None = None,
    ) -> Dict[str, Any]:
        return self.agregar_bloque_recibido(
            block_data,
            propagar=propagar,
            ttl=ttl,
            origin_node=origin_node,
        )

    def discover_peers(self) -> Dict[str, Any]:
        return self.descubrir_peers()

    def get_peers_info(self) -> Dict[str, Any]:
        return self.obtener_info_peers()

    def get_product_state(self, product_id: str) -> Dict[str, Any] | None:
        return self.obtener_estado_producto(product_id)

    def mine(
        self,
        creador_id: str,
        wallet_creador: Wallet,
        validador_id: str | None = None,
        wallet_validador: Wallet | None = None,
        propagar: bool = True,
        ttl: int = TTL_GOSSIP_POR_DEFECTO,
    ) -> Dict[str, Any]:
        return self._ejecutar_minado(
            creador_id=creador_id,
            wallet_creador=wallet_creador,
            validador_id=validador_id,
            wallet_validador=wallet_validador,
            propagar=propagar,
            ttl=ttl,
        )
