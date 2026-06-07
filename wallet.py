"""Wallet RSA sencilla para firmar y verificar eventos logísticos."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# Importamos herramientas para generar claves RSA, firmar datos,
# verificar firmas digitales y guardar/cargar claves desde archivos.
# También se usan utilidades para convertir firmas a texto y serializar datos.

@dataclass
class Wallet:
    """Gestiona claves RSA, firma digital y una identidad pública derivada."""

    clave_privada_pem: str
    clave_publica_pem: str

    # Wallet representa la identidad digital de un actor de la red.
    # No almacena criptomonedas, sino una clave privada y una clave pública.
    # La clave privada firma eventos y la clave pública permite verificarlos.
    
    @classmethod
    def generar_claves(cls, tamano_clave: int = 2048) -> "Wallet":
        clave_privada = rsa.generate_private_key(public_exponent=65537, key_size=tamano_clave)
        privada_pem = clave_privada.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        publica_pem = clave_privada.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
        return cls(clave_privada_pem=privada_pem, clave_publica_pem=publica_pem)

    # Genera una pareja de claves RSA para un actor.
    # La clave privada se usará para firmar eventos.
    # La clave pública se usará para verificar esas firmas.
    # Se guardan en formato PEM, que es un formato de texto estándar para claves.
    # En este prototipo la clave privada no se cifra con contraseña para simplificar.

    def firmar_datos(self, datos: Dict[str, Any]) -> str:
        carga = self._serializar_datos(datos)
        clave_privada = serialization.load_pem_private_key(
            self.clave_privada_pem.encode("utf-8"),
            password=None,
        )
        firma = clave_privada.sign(
            carga,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(firma).decode("utf-8")
    
# Firma los datos recibidos usando la clave privada de la wallet.
# Primero se serializan los datos siempre de la misma forma.
# Después se aplica una firma RSA-PSS con SHA-256.
# La firma se convierte a Base64 para poder guardarla como texto en JSON.

    @staticmethod
    def verificar_firma(
        datos: Dict[str, Any],
        firma: str,
        clave_publica: str,
    ) -> bool:
        try:
            carga = Wallet._serializar_datos(datos)
            clave_publica_obj = serialization.load_pem_public_key(clave_publica.encode("utf-8"))
            firma_decodificada = base64.b64decode(firma.encode("utf-8"))
            clave_publica_obj.verify(
                firma_decodificada,
                carga,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False
# Verifica que una firma digital corresponde a unos datos y a una clave pública.
# Si los datos han sido modificados después de firmarse, la verificación fallará.
# Devuelve True si la firma es válida y False si no lo es.

    @staticmethod
    def derivar_direccion(clave_publica: str) -> str:
        """Genera una identidad pública simple a partir de la clave pública."""

        clave_normalizada = "".join(
            linea.strip()
            for linea in clave_publica.splitlines()
            if "BEGIN" not in linea and "END" not in linea
        )
        return hashlib.sha256(clave_normalizada.encode("utf-8")).hexdigest()[:40]

# Genera una dirección pública resumida a partir de la clave pública.
# Se elimina la cabecera y pie del PEM, se calcula SHA-256
# y se toman los primeros caracteres como identificador corto.
# Esta dirección ayuda a identificar actores, pero la verificación real usa la clave pública completa.

    def obtener_direccion(self) -> str:
        return self.derivar_direccion(self.clave_publica_pem)

# Devuelve la dirección pública resumida de esta wallet.
# Internamente se deriva a partir de la clave pública PEM.

    def guardar_claves(self, directorio: str | Path, nombre: str) -> Tuple[Path, Path]:
        ruta_directorio = Path(directorio)
        ruta_directorio.mkdir(parents=True, exist_ok=True)
        ruta_privada = ruta_directorio / f"{nombre}_private.pem"
        ruta_publica = ruta_directorio / f"{nombre}_public.pem"
        ruta_privada.write_text(self.clave_privada_pem, encoding="utf-8")
        ruta_publica.write_text(self.clave_publica_pem, encoding="utf-8")
        return ruta_privada, ruta_publica
# Guarda la clave privada y la clave pública en archivos PEM.
# Si el directorio no existe, se crea automáticamente.
# Devuelve las rutas de ambos archivos para poder usarlas después.

    @classmethod
    def desde_archivos(cls, ruta_clave_privada: str | Path, ruta_clave_publica: str | Path) -> "Wallet":
        return cls(
            clave_privada_pem=Path(ruta_clave_privada).read_text(encoding="utf-8"),
            clave_publica_pem=Path(ruta_clave_publica).read_text(encoding="utf-8"),
        )

    @classmethod
    def desde_claves_pem(cls, clave_privada_pem: str, clave_publica_pem: str) -> "Wallet":
        return cls(
            clave_privada_pem=clave_privada_pem.strip(),
            clave_publica_pem=clave_publica_pem.strip(),
        )
# Carga una wallet existente desde dos archivos PEM.
# Permite reutilizar claves ya generadas para firmar nuevos eventos.

    @staticmethod
    def _serializar_datos(datos: Dict[str, Any]) -> bytes:
        serializado = json.dumps(datos, sort_keys=True, separators=(",", ":"))
        return serializado.encode("utf-8")

# Convierte los datos a JSON de forma determinista antes de firmar o verificar.
# sort_keys=True ordena las claves para que el resultado sea siempre igual.
# separators elimina espacios innecesarios.
# Finalmente se convierte a bytes porque la criptografía trabaja con bytes.

    @property
    def private_key_pem(self) -> str:
        return self.clave_privada_pem

    @property
    def public_key_pem(self) -> str:
        return self.clave_publica_pem

    @classmethod
    def generate_keys(cls, key_size: int = 2048) -> "Wallet":
        return cls.generar_claves(tamano_clave=key_size)

    def sign_event(self, data: Dict[str, Any]) -> str:
        return self.firmar_datos(data)

    @staticmethod
    def verify_signature(data: Dict[str, Any], signature: str, public_key: str) -> bool:
        return Wallet.verificar_firma(data, signature, public_key)

    def save_keys(self, directory: str | Path, name: str) -> Tuple[Path, Path]:
        return self.guardar_claves(directory, name)

    @classmethod
    def from_files(cls, private_key_path: str | Path, public_key_path: str | Path) -> "Wallet":
        return cls.desde_archivos(private_key_path, public_key_path)

    @classmethod
    def from_pem_strings(cls, private_key_pem: str, public_key_pem: str) -> "Wallet":
        return cls.desde_claves_pem(private_key_pem, public_key_pem)

    def derive_address(self) -> str:
        return self.obtener_direccion()


# Este archivo gestiona la identidad digital de los actores.
# Cada actor tiene una clave privada para firmar eventos
# y una clave pública para que otros nodos puedan verificar esas firmas.
    
