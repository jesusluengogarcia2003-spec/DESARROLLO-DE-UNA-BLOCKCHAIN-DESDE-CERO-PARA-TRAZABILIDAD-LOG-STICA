"""Aplicacion Flask que expone la API HTTP del nodo."""

from __future__ import annotations

import os
from typing import Any, Dict

from flask import Flask, jsonify, request

from config import Config
from node import Node
from p2p_libp2p import LibP2PNodeService
from wallet import Wallet


def _a_bool(valor: Any, por_defecto: bool = True) -> bool:
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return por_defecto
    if isinstance(valor, str):
        return valor.strip().lower() in {"1", "true", "si", "yes"}
    return bool(valor)


def _a_ttl(valor: Any, por_defecto: int = 3) -> int:
    try:
        ttl = int(valor)
    except (TypeError, ValueError):
        return max(0, por_defecto)
    return max(0, ttl)


def _construir_wallet_desde_payload(
    datos_identidad: Dict[str, Any] | None,
    nombre_bloque: str,
) -> tuple[str, Wallet]:
    if not isinstance(datos_identidad, dict):
        raise ValueError(f"Debes proporcionar el objeto '{nombre_bloque}' con sus datos de firma.")

    actor_id = str(datos_identidad.get("actor_id", "")).strip()
    clave_privada = str(datos_identidad.get("private_key", "")).strip()
    clave_publica = str(datos_identidad.get("public_key", "")).strip()

    if not actor_id:
        raise ValueError(f"Falta '{nombre_bloque}.actor_id'.")
    if not clave_privada:
        raise ValueError(f"Falta '{nombre_bloque}.private_key'.")
    if not clave_publica:
        raise ValueError(f"Falta '{nombre_bloque}.public_key'.")

    return actor_id, Wallet.desde_claves_pem(clave_privada, clave_publica)


def create_app(config: Config | None = None) -> Flask:
    app = Flask(__name__)
    configuracion = config or Config()

    identificador_nodo = configuracion.identificador_nodo or f"node-{configuracion.puerto}"
    directorio_storage = os.path.join(configuracion.directorio_cadena, identificador_nodo)
    servicio_nodo = Node(
        node_id=identificador_nodo,
        node_url=configuracion.direccion_nodo,
        storage_dir=directorio_storage,
        validadores_autorizados=configuracion.validadores_autorizados,
        claves_validadores=configuracion.claves_validadores,
        poa_turn_timeout_seconds=configuracion.poa_turn_timeout_seconds,
    )
    bootstrap_resultado = None
    if configuracion.auto_registrar_semillas and configuracion.seed_nodes:
        bootstrap_resultado = servicio_nodo.bootstrap_desde_semillas(
            configuracion.seed_nodes,
            descubrir=configuracion.auto_descubrir_al_arrancar,
            resolver_conflictos=configuracion.auto_resolver_conflictos_al_arrancar,
        )
    if hasattr(servicio_nodo, "registrar_p2p_peers"):
        servicio_nodo.registrar_p2p_peers(configuracion.p2p_bootstrap_peers)
    p2p_bootstrap_peers = (
        servicio_nodo.listar_p2p_peers()
        if hasattr(servicio_nodo, "listar_p2p_peers")
        else configuracion.p2p_bootstrap_peers
    )
    servicio_p2p = LibP2PNodeService(
        node=servicio_nodo,
        listen_host=configuracion.p2p_host,
        listen_port=configuracion.p2p_puerto,
        bootstrap_peers=p2p_bootstrap_peers,
        sync_on_startup=configuracion.p2p_sync_al_arrancar,
    )
    if configuracion.p2p_habilitado:
        servicio_p2p.start()
    app.config["NODE_SERVICE"] = servicio_nodo
    app.config["P2P_SERVICE"] = servicio_p2p
    app.config["APP_CONFIG"] = configuracion
    app.config["BOOTSTRAP_RESULT"] = bootstrap_resultado
    # Crea la aplicación web que permite interactuar con la blockchain.
# Inicializa un nodo que será usado por todos los endpoints.

    @app.get("/health")
    def health() -> Any:
        return jsonify(
            {
                "status": "ok",
                "node_id": servicio_nodo.node_id,
                "node_url": servicio_nodo.node_url,
                "chain_length": len(servicio_nodo.blockchain.cadena),
                "pending_events": len(servicio_nodo.blockchain.eventos_pendientes),
                "chain_valid": servicio_nodo.blockchain.es_cadena_valida(),
                "known_nodes": servicio_nodo.listar_nodos(),
                "authorized_validators": sorted(servicio_nodo.authorized_validators),
                "seed_nodes": configuracion.seed_nodes,
                "bootstrap_result": app.config.get("BOOTSTRAP_RESULT"),
                "consensus": "PoA basico",
                "p2p": servicio_p2p.status(),
            }
        )
# Endpoint de salud del nodo.
# Permite comprobar si el nodo está activo y obtener información básica.

    @app.get("/chain")
    def get_chain() -> Any:
        return jsonify(servicio_nodo.blockchain.a_diccionario())
# Devuelve la blockchain completa.
# Se usa para inspección y para sincronización entre nodos.

    @app.get("/nodes")
    def get_nodes() -> Any:
        return jsonify(servicio_nodo.obtener_info_peers())
# Devuelve los nodos vecinos registrados.
# Permite ver con qué nodos está conectado este nodo.

    @app.get("/peers")
    def get_peers() -> Any:
        return jsonify(servicio_nodo.obtener_info_peers())
# Alias explicito para peer discovery.
# Devuelve la misma informacion minima que /nodes para consulta entre peers.

    @app.get("/events/pending")
    def get_pending_events() -> Any:
        eventos = servicio_nodo.obtener_eventos_pendientes()
        return jsonify(
            {
                "pending_events": eventos,
                "count": len(eventos),
                "pending_system_events": servicio_nodo.obtener_eventos_sistema_pendientes(),
                "system_count": len(servicio_nodo.obtener_eventos_sistema_pendientes()),
                "system_event_proposals": servicio_nodo.obtener_propuestas_eventos_sistema(),
                "proposal_count": len(servicio_nodo.obtener_propuestas_eventos_sistema()),
            }
        )
# Devuelve los eventos pendientes de minado.
# Son eventos válidos que aún no han sido incluidos en un bloque.

    @app.post("/system-events/new")
    def new_system_event() -> Any:
        carga: Dict[str, Any] = request.get_json(silent=True) or {}
        evento_sistema = carga.get("system_event") if isinstance(carga.get("system_event"), dict) else carga
        if not isinstance(evento_sistema, dict):
            return jsonify({"message": "La peticion debe incluir un evento de sistema valido."}), 400

        try:
            resultado = servicio_nodo.agregar_evento_sistema(evento_sistema)
        except ValueError as exc:
            return jsonify({"message": str(exc), "system_event": evento_sistema}), 400

        return jsonify({"message": resultado["mensaje"], "system_event": resultado["system_event"]}), 201

    @app.post("/system-events/proposals/new")
    def new_system_event_proposal() -> Any:
        carga: Dict[str, Any] = request.get_json(silent=True) or {}
        propuesta = carga.get("system_event") if isinstance(carga.get("system_event"), dict) else carga
        propagar = _a_bool(carga.get("propagate", True))
        if not isinstance(propuesta, dict):
            return jsonify({"message": "La peticion debe incluir una propuesta de sistema valida."}), 400

        try:
            resultado = servicio_nodo.agregar_propuesta_evento_sistema(propuesta)
        except ValueError as exc:
            return jsonify({"message": str(exc), "system_event": propuesta}), 400

        propagados = servicio_p2p.broadcast_system_proposal(resultado["system_event"]) if propagar else []
        return jsonify({"message": resultado["mensaje"], **resultado, "p2p_propagated_to": propagados}), 201

    @app.post("/system-events/proposals/approve")
    def approve_system_event_proposal() -> Any:
        carga: Dict[str, Any] = request.get_json(silent=True) or {}
        try:
            approver_id, wallet_approver = _construir_wallet_desde_payload(carga.get("approver"), "approver")
            resultado = servicio_nodo.aprobar_propuesta_evento_sistema(
                tipo=str(carga.get("type", "")),
                validator_id=str(carga.get("validator_id", "")),
                effective_from_block=int(carga.get("effective_from_block", 0)),
                approver_id=approver_id,
                wallet_approver=wallet_approver,
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"message": str(exc)}), 400

        propagados = servicio_p2p.broadcast_system_proposal(resultado["system_event"])
        return jsonify({"message": resultado["mensaje"], **resultado, "p2p_propagated_to": propagados}), 201

    @app.post("/system-events/proposals/propagate")
    def propagate_system_event_proposal() -> Any:
        carga: Dict[str, Any] = request.get_json(silent=True) or {}
        tipo = str(carga.get("type", "")).strip().upper()
        validator_id = str(carga.get("validator_id", "")).strip()
        try:
            effective_from_block = int(carga.get("effective_from_block", 0))
        except (TypeError, ValueError):
            return jsonify({"message": "effective_from_block debe ser numerico."}), 400

        propuesta = None
        clave_buscada = (tipo, validator_id, effective_from_block)
        for candidata in servicio_nodo.obtener_propuestas_eventos_sistema():
            clave = servicio_nodo.blockchain._clave_evento_sistema(candidata)
            if clave == clave_buscada:
                propuesta = candidata
                break

        if propuesta is None:
            return jsonify({"message": "No existe una propuesta pendiente con esos datos."}), 404

        propagados = servicio_p2p.broadcast_system_proposal(propuesta)
        return jsonify(
            {
                "message": "Propuesta de sistema propagada por libp2p.",
                "system_event": propuesta,
                "p2p_propagated_to": propagados,
            }
        )

    @app.get("/consensus/status")
    def consensus_status() -> Any:
        return jsonify(servicio_nodo.obtener_estado_consenso())
# Devuelve el estado del consenso PoA del nodo.
# Permite saber si le toca validar y como esta la cadena local.

    @app.get("/p2p/status")
    def p2p_status() -> Any:
        return jsonify(servicio_p2p.status())

    @app.post("/p2p/sync")
    def p2p_sync() -> Any:
        if not servicio_p2p.started:
            return jsonify({"message": "El transporte libp2p no esta iniciado.", "p2p": servicio_p2p.status()}), 503
        resultado = servicio_p2p.request_sync()
        return jsonify({"message": "Sincronizacion P2P solicitada.", **resultado, "p2p": servicio_p2p.status()})

    @app.post("/p2p/discover")
    def p2p_discover() -> Any:
        if not servicio_p2p.started:
            return jsonify({"message": "El transporte libp2p no esta iniciado.", "p2p": servicio_p2p.status()}), 503
        resultado = servicio_p2p.request_peer_discovery()
        return jsonify({"message": "Descubrimiento P2P solicitado.", **resultado, "p2p": servicio_p2p.status()})

    @app.post("/events/new")
    def new_event() -> Any:
        carga: Dict[str, Any] = request.get_json(silent=True) or {}
        if isinstance(carga.get("event"), dict):
            datos_evento = carga.get("event")
        elif isinstance(carga, dict) and {"event_id", "product_id", "event_type"} <= set(carga.keys()):
            datos_evento = carga
        else:
            datos_evento = None
        clave_publica = carga.get("public_key", "")
        propagar = _a_bool(carga.get("propagate", True))
        ttl = _a_ttl(carga.get("ttl", 3))
        origin_node = str(carga.get("origin_node", "")).strip() or None

        if not datos_evento:
            return (
                jsonify(
                    {
                        "message": "La peticion debe incluir un objeto 'event' valido.",
                    }
                ),
                400,
            )

        try:
            resultado = servicio_nodo.agregar_evento(
                datos_evento=datos_evento,
                clave_publica_externa=clave_publica or None,
                propagar=propagar,
                ttl=ttl,
                origin_node=origin_node,
            )
        except ValueError as exc:
            return jsonify({"message": str(exc), "event": datos_evento}), 400

        codigo = 200 if resultado.get("duplicate") else 201
        return (
            jsonify(
                {
                    "message": resultado["mensaje"],
                    "event": resultado["event"],
                    "duplicate": resultado["duplicate"],
                    "recovered_events": resultado.get("recovered_events", []),
                    "propagated_to": resultado["propagated_to"],
                    "p2p_propagated_to": (
                        servicio_p2p.broadcast_event(resultado["event"])
                        if not resultado.get("duplicate")
                        else []
                    ),
                }
            ),
            codigo,
        )
# Recibe un evento desde el exterior.
# Lo valida y lo añade a la blockchain como pendiente.
# Si es correcto, puede propagarse a otros nodos.

    @app.post("/mine")
    def mine() -> Any:
        carga: Dict[str, Any] = request.get_json(silent=True) or {}
        propagar = _a_bool(carga.get("propagate", True))
        ttl = _a_ttl(carga.get("ttl", 3))
        try:
            creador_id, wallet_creador = _construir_wallet_desde_payload(carga.get("creator"), "creator")

            if carga.get("validator"):
                validador_id, wallet_validador = _construir_wallet_desde_payload(
                    carga.get("validator"),
                    "validator",
                )
            else:
                validador_id, wallet_validador = creador_id, wallet_creador

            resultado = servicio_nodo.minar(
                creador_id=creador_id,
                wallet_creador=wallet_creador,
                validador_id=validador_id,
                wallet_validador=wallet_validador,
                propagar=propagar,
                ttl=ttl,
            )
        except ValueError as exc:
            codigo = 403 if str(exc) == "Nodo no autorizado para validar bloques" else 400
            return jsonify({"message": str(exc)}), codigo

        if not resultado["created"]:
            return jsonify({"message": "No hay eventos pendientes para incluir en un bloque."}), 200
        p2p_propagados = servicio_p2p.broadcast_block(resultado["block"]) if resultado["block"] else []
        resultado["p2p_propagated_to"] = p2p_propagados
        return jsonify({"message": "Nuevo bloque creado correctamente.", **resultado}), 201
# Crea un nuevo bloque con los eventos pendientes.
# Después de minar, la cadena crece y se puede propagar a otros nodos.

    @app.post("/blocks/new")
    def new_block() -> Any:
        carga: Dict[str, Any] = request.get_json(silent=True) or {}
        datos_bloque = carga.get("block") if isinstance(carga.get("block"), dict) else carga
        propagar = _a_bool(carga.get("propagate", True))
        ttl = _a_ttl(carga.get("ttl", 3))
        origin_node = str(carga.get("origin_node", "")).strip() or None

        if not datos_bloque or not isinstance(datos_bloque, dict):
            return jsonify({"message": "La peticion debe incluir un bloque valido en formato JSON."}), 400

        try:
            resultado = servicio_nodo.agregar_bloque_recibido(
                datos_bloque,
                propagar=propagar,
                ttl=ttl,
                origin_node=origin_node,
            )
        except ValueError as exc:
            return jsonify({"message": str(exc)}), 400

        bloque_respuesta = resultado["bloque"]
        if hasattr(bloque_respuesta, "a_diccionario"):
            bloque_respuesta = bloque_respuesta.a_diccionario()

        codigo = 200 if resultado["duplicado"] else 201
        return (
            jsonify(
                {
                    "message": resultado["mensaje"],
                    "duplicate": resultado["duplicado"],
                    "block": bloque_respuesta,
                    "recovered_events": resultado.get("recovered_events", []),
                    "propagated_to": resultado.get("propagated_to", []),
                    "p2p_propagated_to": (
                        servicio_p2p.broadcast_block(bloque_respuesta)
                        if not resultado["duplicado"]
                        else []
                    ),
                }
            ),
            codigo,
        )
# Recibe un bloque ya minado desde otro nodo.
# Lo valida completamente y solo lo añade a la cadena si supera todas las comprobaciones.

    @app.get("/products/<product_id>/history")
    def product_history(product_id: str) -> Any:
        historial = servicio_nodo.blockchain.obtener_historial_producto(product_id)
        return jsonify(
            {
                "product_id": product_id,
                "events": historial,
                "count": len(historial),
            }
        )
# Devuelve el historial completo de un producto.
# Permite reconstruir su trazabilidad dentro de la blockchain.

    @app.get("/products/<product_id>/state")
    def product_state(product_id: str) -> Any:
        estado = servicio_nodo.obtener_estado_producto(product_id)
        if estado is None:
            return (
                jsonify(
                    {
                        "message": f"No existe un estado confirmado para el producto '{product_id}'.",
                        "product_id": product_id,
                    }
                ),
                404,
            )

        return jsonify(estado)
# Devuelve el ultimo estado confirmado de un producto usando el indice auxiliar.
# Evita recorrer toda la cadena cuando solo se necesita el estado mas reciente.

    @app.post("/nodes/register")
    def register_nodes() -> Any:
        carga = request.get_json(silent=True) or {}
        nodos_a_registrar = carga.get("nodes", [])
        descubrir = _a_bool(carga.get("discover", False), por_defecto=False)
        if not isinstance(nodos_a_registrar, list) or not nodos_a_registrar:
            return jsonify({"message": "Debes proporcionar una lista 'nodes' no vacia."}), 400

        registrados = servicio_nodo.registrar_nodos(nodos_a_registrar, descubrir=descubrir)
        return jsonify(
            {
                "message": "Nodos registrados correctamente.",
                "registered_nodes": registrados,
                "total_nodes": servicio_nodo.listar_nodos(),
            }
        )
# Registra nuevos nodos vecinos.
# Permite conectar este nodo con otros para formar la red.

    @app.post("/nodes/discover")
    def discover_nodes() -> Any:
        resultado = servicio_nodo.descubrir_peers()
        return jsonify(
            {
                "message": "Peer discovery ejecutado correctamente.",
                **resultado,
            }
        )
# Ejecuta el descubrimiento de peers a partir de los nodos ya conocidos.
# Permite aprender automaticamente nuevos nodos de forma controlada.

    @app.get("/nodes/resolve")
    def resolve_nodes() -> Any:
        resultado = servicio_nodo.resolver_conflictos()
        mensaje = (
            "La cadena local ha sido reemplazada por una version valida mas larga."
            if resultado["replaced"]
            else "La cadena local sigue siendo la referencia valida."
        )
        return jsonify({"message": mensaje, **resultado})

    return app

# Ejecuta la resolución de conflictos entre nodos.
# Aplica el consenso adoptando la cadena válida más larga.


if __name__ == "__main__":
    configuracion_runtime = Config(
        host=os.getenv("BLOCKCHAIN_HOST", "127.0.0.1"),
        puerto=int(os.getenv("BLOCKCHAIN_PORT", "5000")),
        identificador_nodo=os.getenv("NODE_ID", ""),
        url_nodo=os.getenv("NODE_URL", ""),
        directorio_cadena=os.getenv("CHAIN_STORAGE_DIR", "data"),
        claves_validadores=Config().claves_validadores,
        p2p_habilitado=os.getenv("P2P_ENABLED", "0").strip().lower() in {"1", "true", "si", "yes"},
        p2p_host=os.getenv("P2P_HOST", "0.0.0.0"),
        p2p_puerto=int(os.getenv("P2P_PORT", "4001")),
        p2p_bootstrap_peers=[
            peer.strip()
            for peer in os.getenv("P2P_BOOTSTRAP_PEERS", "").split(",")
            if peer.strip()
        ],
        p2p_sync_al_arrancar=os.getenv("P2P_SYNC_ON_STARTUP", "0").strip().lower()
        in {"1", "true", "si", "yes"},
        poa_turn_timeout_seconds=int(os.getenv("POA_TURN_TIMEOUT_SECONDS", "60")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
    )
    application = create_app(configuracion_runtime)
    application.run(
        host=configuracion_runtime.host,
        port=configuracion_runtime.puerto,
        debug=configuracion_runtime.debug,
    )
