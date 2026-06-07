"""Configuracion minima de la aplicacion blockchain."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set


AUTHORIZED_VALIDATORS = {"node-5000", "node-5001", "node-5002"}


def _cargar_validadores_autorizados() -> Set[str]:
    """Carga los validadores autorizados desde entorno o usa un conjunto por defecto."""

    valor = os.getenv("AUTHORIZED_VALIDATORS", "").strip()
    if not valor:
        return set(AUTHORIZED_VALIDATORS)

    validadores = {
        identificador.strip()
        for identificador in valor.split(",")
        if identificador.strip()
    }
    return validadores or set(AUTHORIZED_VALIDATORS)


def _cargar_claves_validadores() -> Dict[str, str]:
    """Carga el registro inicial validator_id -> public_key desde JSON."""

    ruta = os.getenv("VALIDATOR_KEYS_FILE", "").strip()
    if ruta:
        try:
            datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return _normalizar_claves_validadores(datos)

    valor = os.getenv("VALIDATOR_PUBLIC_KEYS", "").strip()
    if not valor:
        return {}
    try:
        datos = json.loads(valor)
    except json.JSONDecodeError:
        return {}
    return _normalizar_claves_validadores(datos)


def _normalizar_claves_validadores(datos: object) -> Dict[str, str]:
    if not isinstance(datos, dict):
        return {}

    claves: Dict[str, str] = {}
    for validator_id, valor in datos.items():
        identificador = str(validator_id).strip()
        if not identificador:
            continue
        if isinstance(valor, dict):
            clave_publica = str(valor.get("public_key", "")).strip()
        else:
            clave_publica = str(valor).strip()
        if clave_publica:
            claves[identificador] = clave_publica
    return claves


def _cargar_nodos_semilla() -> List[str]:
    """Carga nodos semilla iniciales desde entorno."""

    valor = os.getenv("SEED_NODES", "").strip()
    if not valor:
        return []

    seeds = []
    for direccion in valor.split(","):
        direccion_limpia = direccion.strip()
        if direccion_limpia and direccion_limpia not in seeds:
            seeds.append(direccion_limpia)
    return seeds


def _cargar_p2p_bootstrap_peers() -> List[str]:
    """Carga peers libp2p semilla desde entorno en formato multiaddr."""

    valor = os.getenv("P2P_BOOTSTRAP_PEERS", "").strip()
    if not valor:
        return []

    peers = []
    for direccion in valor.split(","):
        direccion_limpia = direccion.strip()
        if direccion_limpia and direccion_limpia not in peers:
            peers.append(direccion_limpia)
    return peers


def _cargar_bool_entorno(nombre: str, por_defecto: bool = False) -> bool:
    valor = os.getenv(nombre)
    if valor is None:
        return por_defecto
    return valor.strip().lower() in {"1", "true", "si", "yes"}


@dataclass(frozen=True)
class Config:
    """Centraliza los parametros minimos del nodo y del PoA basico."""

    host: str = os.getenv("BLOCKCHAIN_HOST", "127.0.0.1")
    puerto: int = int(os.getenv("BLOCKCHAIN_PORT", "5000"))
    identificador_nodo: str = os.getenv("NODE_ID", "")
    url_nodo: str = os.getenv("NODE_URL", "")
    directorio_cadena: str = os.getenv("CHAIN_STORAGE_DIR", "data")
    validadores_autorizados: Set[str] = field(default_factory=_cargar_validadores_autorizados)
    claves_validadores: Dict[str, str] = field(default_factory=_cargar_claves_validadores)
    nodos_semilla: List[str] = field(default_factory=_cargar_nodos_semilla)
    auto_registrar_semillas: bool = _cargar_bool_entorno("AUTO_REGISTER_SEEDS", False)
    auto_descubrir_al_arrancar: bool = _cargar_bool_entorno("AUTO_DISCOVER_ON_STARTUP", False)
    auto_resolver_conflictos_al_arrancar: bool = _cargar_bool_entorno("AUTO_RESOLVE_ON_STARTUP", False)
    p2p_habilitado: bool = _cargar_bool_entorno("P2P_ENABLED", False)
    p2p_host: str = os.getenv("P2P_HOST", "0.0.0.0")
    p2p_puerto: int = int(os.getenv("P2P_PORT", "4001"))
    p2p_bootstrap_peers: List[str] = field(default_factory=_cargar_p2p_bootstrap_peers)
    p2p_sync_al_arrancar: bool = _cargar_bool_entorno("P2P_SYNC_ON_STARTUP", False)
    poa_turn_timeout_seconds: int = int(os.getenv("POA_TURN_TIMEOUT_SECONDS", "60"))
    debug: bool = os.getenv("FLASK_DEBUG", "0") == "1"

    @property
    def direccion_nodo(self) -> str:
        url = self.url_nodo.strip()
        if url:
            return url.rstrip("/")
        return f"http://{self.host}:{self.puerto}"

    @property
    def port(self) -> int:
        return self.puerto

    @property
    def node_id(self) -> str:
        return self.identificador_nodo

    @property
    def node_url(self) -> str:
        return self.direccion_nodo

    @property
    def chain_storage_dir(self) -> str:
        return self.directorio_cadena

    @property
    def authorized_validators(self) -> Set[str]:
        return set(self.validadores_autorizados)

    @property
    def validator_public_keys(self) -> Dict[str, str]:
        return dict(self.claves_validadores)

    @property
    def seed_nodes(self) -> List[str]:
        return list(self.nodos_semilla)

    @property
    def p2p_enabled(self) -> bool:
        return self.p2p_habilitado

    @property
    def p2p_port(self) -> int:
        return self.p2p_puerto

    @property
    def p2p_bootstrap(self) -> List[str]:
        return list(self.p2p_bootstrap_peers)

    @property
    def poa_turn_timeout(self) -> int:
        return self.poa_turn_timeout_seconds


DEFAULT_CONFIG = Config()
