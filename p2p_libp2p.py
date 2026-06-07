"""Transporte libp2p opcional para comunicar nodos entre redes."""

from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from node import Node


PROTOCOLO_BLOCKCHAIN = "/tfm-logistics-blockchain/1.0.0"
MAX_READ_LEN = 2**24
P2P_GOSSIP_TTL_POR_DEFECTO = 3
P2P_STREAM_TIMEOUT_SECONDS = 10
TIPOS_GOSSIP_P2P = {"NEW_EVENT", "NEW_BLOCK", "VALIDATOR_PROPOSAL"}
P2P_KEY_SECRET_BYTES = 32


try:
    import multiaddr
    import trio
    from libp2p import new_host
    from libp2p.crypto.secp256k1 import create_new_key_pair
    from libp2p.custom_types import TProtocol
    from libp2p.network.stream.net_stream import INetStream
    from libp2p.peer.peerinfo import info_from_p2p_addr
except ImportError:  # pragma: no cover - depende de instalacion opcional
    multiaddr = None
    trio = None
    new_host = None
    create_new_key_pair = None
    TProtocol = None
    INetStream = Any
    info_from_p2p_addr = None


class LibP2PNodeService:
    """Ejecuta un host libp2p en segundo plano y traduce mensajes al Node."""

    def __init__(
        self,
        node: Node,
        listen_host: str,
        listen_port: int,
        bootstrap_peers: Optional[Iterable[str]] = None,
        sync_on_startup: bool = False,
    ) -> None:
        self.node = node
        self.listen_host = listen_host
        self.listen_port = int(listen_port)
        self.bootstrap_peers = []
        for peer in bootstrap_peers or []:
            peer_limpio = peer.strip()
            if peer_limpio and peer_limpio not in self.bootstrap_peers:
                self.bootstrap_peers.append(peer_limpio)
        if hasattr(self.node, "registrar_p2p_peers"):
            self.node.registrar_p2p_peers(self.bootstrap_peers)
        self.sync_on_startup = bool(sync_on_startup)
        self.enabled = False
        self.started = False
        self.last_error = ""
        self.peer_id = ""
        self.listen_addrs: List[str] = []
        self.connected_peers: List[str] = []
        self.key_secret_path = self._obtener_ruta_secreto_p2p()
        self._host: Any = None
        self._trio_token: Any = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        return all([multiaddr, trio, new_host, create_new_key_pair, TProtocol, info_from_p2p_addr])

    def start(self) -> None:
        if self.started or self.enabled:
            return
        if not self.available:
            self.last_error = "La dependencia 'libp2p' no esta instalada."
            return

        self.enabled = True
        self._thread = threading.Thread(
            target=self._run_background,
            name=f"libp2p-{self.node.node_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self.enabled = False
        if self._host is not None and self._trio_token is not None and trio is not None:
            try:
                trio.from_thread.run(self._host.close, trio_token=self._trio_token)
            except Exception as exc:  # pragma: no cover - cierre defensivo
                self.last_error = str(exc)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "available": self.available,
                "started": self.started,
                "peer_id": self.peer_id,
                "listen_addrs": list(self.listen_addrs),
                "bootstrap_peers": list(self.bootstrap_peers),
                "known_peers": self.node.listar_p2p_peers() if hasattr(self.node, "listar_p2p_peers") else [],
                "connected_peers": list(self.connected_peers),
                "last_error": self.last_error,
                "protocol": PROTOCOLO_BLOCKCHAIN,
            }

    def broadcast_event(self, event: Dict[str, Any], ttl: int = P2P_GOSSIP_TTL_POR_DEFECTO) -> List[str]:
        return self._call_async("NEW_EVENT", {"event": event, "ttl": self._normalizar_ttl(ttl)})

    def broadcast_block(self, block: Dict[str, Any], ttl: int = P2P_GOSSIP_TTL_POR_DEFECTO) -> List[str]:
        return self._call_async("NEW_BLOCK", {"block": block, "ttl": self._normalizar_ttl(ttl)})

    def broadcast_system_proposal(
        self,
        system_event: Dict[str, Any],
        ttl: int = P2P_GOSSIP_TTL_POR_DEFECTO,
    ) -> List[str]:
        return self._call_async("VALIDATOR_PROPOSAL", {"system_event": system_event, "ttl": self._normalizar_ttl(ttl)})

    def request_peer_discovery(self) -> Dict[str, Any]:
        peers_antes = set(self.node.listar_p2p_peers() if hasattr(self.node, "listar_p2p_peers") else [])
        consultados = self._call_async("GET_PEERS", {})
        peers_despues = set(self.node.listar_p2p_peers() if hasattr(self.node, "listar_p2p_peers") else [])
        return {
            "requested_to": consultados,
            "peers_discovered": sorted(peers_despues - peers_antes),
            "known_peers": sorted(peers_despues),
        }

    def request_sync(self) -> Dict[str, Any]:
        resultados = self._call_async("GET_CHAIN", {})
        return {"requested_to": resultados}

    def _call_async(self, message_type: str, payload: Dict[str, Any]) -> List[str]:
        if not self.started or self._trio_token is None or trio is None:
            return []
        try:
            return trio.from_thread.run(
                self._broadcast_async,
                message_type,
                payload,
                trio_token=self._trio_token,
            )
        except Exception as exc:
            self.last_error = str(exc)
            return []

    def _run_background(self) -> None:
        assert trio is not None
        try:
            trio.run(self._run)
        except Exception as exc:
            self.last_error = str(exc)
            self.enabled = False
            self.started = False

    async def _run(self) -> None:
        assert trio is not None
        assert multiaddr is not None
        assert new_host is not None
        assert create_new_key_pair is not None
        assert TProtocol is not None

        secret = self._cargar_o_crear_secreto_p2p()
        key_pair = create_new_key_pair(secret)
        host = new_host(key_pair=key_pair)
        protocol_id = TProtocol(PROTOCOLO_BLOCKCHAIN)
        listen_addr = multiaddr.Multiaddr(f"/ip4/{self.listen_host}/tcp/{self.listen_port}")

        async def stream_handler(stream: INetStream) -> None:
            await self._handle_stream(stream)

        host.set_stream_handler(protocol_id, stream_handler)
        self._host = host
        self._trio_token = trio.lowlevel.current_trio_token()

        async with host.run(listen_addrs=[listen_addr]):
            with self._lock:
                self.started = True
                self.peer_id = host.get_id().to_string()
                self.listen_addrs = [self._format_listen_addr(str(addr), self.peer_id) for addr in host.get_addrs()]
                self.last_error = ""

            await self._connect_bootstrap_peers()
            await self._broadcast_async("GET_PEERS", {})
            if self.sync_on_startup:
                await self._broadcast_async("GET_CHAIN", {})

            while self.enabled:
                with self._lock:
                    self.connected_peers = [
                        peer.to_string()
                        for peer in host.get_connected_peers()
                    ]
                await trio.sleep(2)

    async def _connect_bootstrap_peers(self) -> None:
        assert multiaddr is not None
        assert info_from_p2p_addr is not None
        if self._host is None:
            return

        for peer_addr in self.bootstrap_peers:
            try:
                peer_info = info_from_p2p_addr(multiaddr.Multiaddr(peer_addr))
                await self._host.connect(peer_info)
                if hasattr(self.node, "registrar_p2p_peers"):
                    self.node.registrar_p2p_peers([peer_addr])
            except Exception as exc:
                self.last_error = f"No se pudo conectar con bootstrap {peer_addr}: {exc}"

    async def _broadcast_async(
        self,
        message_type: str,
        payload: Dict[str, Any],
        exclude_peer_id: str | None = None,
    ) -> List[str]:
        if self._host is None or multiaddr is None or info_from_p2p_addr is None or TProtocol is None:
            return []

        destinos = []
        ids_destino = set()
        known_peers = list(self.bootstrap_peers)
        if hasattr(self.node, "listar_p2p_peers"):
            for peer in self.node.listar_p2p_peers():
                if peer not in known_peers:
                    known_peers.append(peer)

        for bootstrap in known_peers:
            try:
                peer_info = info_from_p2p_addr(multiaddr.Multiaddr(bootstrap))
            except Exception:
                continue
            if exclude_peer_id and peer_info.peer_id.to_string() == exclude_peer_id:
                continue
            if peer_info.peer_id in ids_destino:
                continue
            destinos.append(peer_info)
            ids_destino.add(peer_info.peer_id)

        enviados: List[str] = []
        for peer_info in destinos:
            try:
                await self._host.connect(peer_info)
                stream = await self._host.new_stream(peer_info.peer_id, [TProtocol(PROTOCOLO_BLOCKCHAIN)])
                respuesta = await self._send_message(stream, message_type, payload)
                self._apply_response(respuesta)
                if hasattr(self.node, "registrar_p2p_peers") and peer_info.addrs:
                    self.node.registrar_p2p_peers(
                        [str(peer_info.addrs[0]) + f"/p2p/{peer_info.peer_id.to_string()}"]
                    )
                enviados.append(peer_info.peer_id.to_string())
            except Exception as exc:
                self.last_error = str(exc)

        for peer_id in self._host.get_connected_peers():
            if peer_id in ids_destino:
                continue
            if exclude_peer_id and peer_id.to_string() == exclude_peer_id:
                continue
            try:
                stream = await self._host.new_stream(peer_id, [TProtocol(PROTOCOLO_BLOCKCHAIN)])
                respuesta = await self._send_message(stream, message_type, payload)
                self._apply_response(respuesta)
                enviados.append(peer_id.to_string())
            except Exception as exc:
                self.last_error = str(exc)
        return enviados

    async def _send_message(
        self,
        stream: INetStream,
        message_type: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        mensaje = {
            "type": message_type,
            "from_node_id": self.node.node_id,
            "from_peer_id": self.peer_id,
            "payload": payload,
        }
        if trio is None:
            return {}

        with trio.move_on_after(P2P_STREAM_TIMEOUT_SECONDS) as scope:
            await stream.write(json.dumps(mensaje, separators=(",", ":")).encode("utf-8"))
            raw = await stream.read(MAX_READ_LEN)

        if scope.cancelled_caught:
            raise TimeoutError(f"Timeout esperando respuesta libp2p para {message_type}.")
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    async def _handle_stream(self, stream: INetStream) -> None:
        try:
            if trio is None:
                return

            with trio.move_on_after(P2P_STREAM_TIMEOUT_SECONDS) as scope:
                raw = await stream.read(MAX_READ_LEN)
                mensaje = json.loads(raw.decode("utf-8")) if raw else {}
                respuesta = self._process_message(mensaje)
                await self._rebroadcast_received_message(mensaje, respuesta)
                await stream.write(json.dumps(respuesta, separators=(",", ":")).encode("utf-8"))

            if scope.cancelled_caught:
                self.last_error = "Timeout procesando stream libp2p entrante."
        except Exception as exc:
            respuesta_error = {"ok": False, "error": str(exc)}
            try:
                await stream.write(json.dumps(respuesta_error).encode("utf-8"))
            except Exception:
                pass

    def _process_message(self, mensaje: Dict[str, Any]) -> Dict[str, Any]:
        tipo = str(mensaje.get("type", "")).strip().upper()
        payload = mensaje.get("payload", {}) if isinstance(mensaje.get("payload", {}), dict) else {}
        origen = str(mensaje.get("from_node_id", "")).strip()

        try:
            if tipo == "NEW_EVENT":
                resultado = self.node.agregar_evento(
                    datos_evento=payload.get("event", {}),
                    clave_publica_externa=payload.get("event", {}).get("public_key", ""),
                    propagar=False,
                    ttl=self._normalizar_ttl(payload.get("ttl", P2P_GOSSIP_TTL_POR_DEFECTO)),
                    origin_node=f"libp2p:{origen}",
                )
                return {"ok": True, "type": "EVENT_ACCEPTED", "result": self._json_safe(resultado)}

            if tipo == "NEW_BLOCK":
                resultado = self.node.agregar_bloque_recibido(
                    datos_bloque=payload.get("block", {}),
                    propagar=False,
                    ttl=self._normalizar_ttl(payload.get("ttl", P2P_GOSSIP_TTL_POR_DEFECTO)),
                    origin_node=f"libp2p:{origen}",
                )
                return {"ok": True, "type": "BLOCK_ACCEPTED", "result": self._json_safe(resultado)}

            if tipo == "VALIDATOR_PROPOSAL":
                resultado = self.node.agregar_propuesta_evento_sistema(
                    payload.get("system_event", {}),
                )
                return {"ok": True, "type": "VALIDATOR_PROPOSAL_ACCEPTED", "result": self._json_safe(resultado)}

            if tipo == "GET_CHAIN":
                return {
                    "ok": True,
                    "type": "CHAIN_RESPONSE",
                    "chain": self.node.blockchain.a_diccionario(),
                }

            if tipo == "GET_PEERS":
                known_peers = self.node.listar_p2p_peers() if hasattr(self.node, "listar_p2p_peers") else []
                return {
                    "ok": True,
                    "type": "PEERS_RESPONSE",
                    "listen_addrs": list(self.listen_addrs),
                    "known_peers": known_peers,
                }

            if tipo == "GET_STATUS":
                return {
                    "ok": True,
                    "type": "STATUS_RESPONSE",
                    "status": self.node.obtener_estado_consenso(),
                }

            return {"ok": False, "error": f"Tipo de mensaje no soportado: {tipo}"}
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    async def _rebroadcast_received_message(self, mensaje: Dict[str, Any], respuesta: Dict[str, Any]) -> None:
        if not self._should_rebroadcast(mensaje, respuesta):
            return

        tipo = str(mensaje.get("type", "")).strip().upper()
        payload = dict(mensaje.get("payload", {}) if isinstance(mensaje.get("payload", {}), dict) else {})
        ttl_reenvio = self._normalizar_ttl(payload.get("ttl", P2P_GOSSIP_TTL_POR_DEFECTO)) - 1
        payload["ttl"] = ttl_reenvio
        origen_peer = str(mensaje.get("from_peer_id", "")).strip() or None
        reenviados = await self._broadcast_async(tipo, payload, exclude_peer_id=origen_peer)
        if reenviados:
            respuesta["p2p_gossip_forwarded_to"] = reenviados

    def _should_rebroadcast(self, mensaje: Dict[str, Any], respuesta: Dict[str, Any]) -> bool:
        tipo = str(mensaje.get("type", "")).strip().upper()
        if tipo not in TIPOS_GOSSIP_P2P or not respuesta.get("ok"):
            return False

        payload = mensaje.get("payload", {}) if isinstance(mensaje.get("payload", {}), dict) else {}
        if self._normalizar_ttl(payload.get("ttl", P2P_GOSSIP_TTL_POR_DEFECTO)) <= 1:
            return False

        resultado = respuesta.get("result", {})
        if isinstance(resultado, dict):
            if resultado.get("duplicate") or resultado.get("duplicado"):
                return False
            if resultado.get("agregado") is False:
                return False
        return True

    def _apply_response(self, respuesta: Dict[str, Any]) -> None:
        if respuesta.get("type") == "PEERS_RESPONSE":
            peers = []
            for clave in ("listen_addrs", "known_peers"):
                valor = respuesta.get(clave, [])
                if isinstance(valor, list):
                    peers.extend(str(peer) for peer in valor)
            self._registrar_peers_descubiertos(peers)
            return

        if respuesta.get("type") != "CHAIN_RESPONSE":
            return
        cadena = respuesta.get("chain", {}).get("chain", [])
        if isinstance(cadena, list):
            reemplazada = self.node.blockchain.reemplazar_cadena(cadena)
            if reemplazada and hasattr(self.node, "_guardar_estado"):
                self.node._guardar_estado()

    def _registrar_peers_descubiertos(self, peers: Iterable[str]) -> List[str]:
        if not hasattr(self.node, "registrar_p2p_peers"):
            return []

        candidatos = []
        listen_addrs_propias = set(self.listen_addrs)
        for peer in peers:
            peer_limpio = str(peer).strip()
            if not peer_limpio:
                continue
            if peer_limpio in listen_addrs_propias:
                continue
            if self.peer_id and f"/p2p/{self.peer_id}" in peer_limpio:
                continue
            candidatos.append(peer_limpio)

        return self.node.registrar_p2p_peers(candidatos)

    def _obtener_ruta_secreto_p2p(self) -> Path:
        directorio = getattr(self.node, "directorio_storage", None)
        if directorio is None:
            directorio = Path("data") / str(self.node.node_id)
        return Path(directorio) / f"{self.node.node_id}_p2p_secret.key"

    def _cargar_o_crear_secreto_p2p(self) -> bytes:
        """Carga el secreto secp256k1 persistente o crea uno nuevo para mantener el peer_id."""

        try:
            if self.key_secret_path.exists():
                contenido = self.key_secret_path.read_text(encoding="utf-8").strip()
                secreto = bytes.fromhex(contenido)
                if len(secreto) == P2P_KEY_SECRET_BYTES:
                    return secreto
        except (OSError, ValueError):
            pass

        secreto = secrets.token_bytes(P2P_KEY_SECRET_BYTES)
        try:
            self.key_secret_path.parent.mkdir(parents=True, exist_ok=True)
            self.key_secret_path.write_text(secreto.hex(), encoding="utf-8")
        except OSError as exc:
            self.last_error = f"No se pudo guardar la identidad libp2p persistente: {exc}"
        return secreto

    def _json_safe(self, valor: Any) -> Any:
        if hasattr(valor, "a_diccionario"):
            return valor.a_diccionario()
        if isinstance(valor, dict):
            return {clave: self._json_safe(contenido) for clave, contenido in valor.items()}
        if isinstance(valor, list):
            return [self._json_safe(contenido) for contenido in valor]
        return valor

    def _normalizar_ttl(self, ttl: Any) -> int:
        try:
            ttl_normalizado = int(ttl)
        except (TypeError, ValueError):
            ttl_normalizado = P2P_GOSSIP_TTL_POR_DEFECTO
        return max(0, ttl_normalizado)

    def _format_listen_addr(self, address: str, peer_id: str) -> str:
        if "/p2p/" in address:
            return address
        return f"{address}/p2p/{peer_id}"
