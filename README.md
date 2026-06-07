# Blockchain academica para trazabilidad logistica

Prototipo academico en Python de una blockchain sencilla orientada a la trazabilidad de productos o paquetes logisticos. El sistema registra eventos firmados digitalmente, agrupa eventos pendientes en bloques, valida la coherencia de la cadena y sincroniza nodos con un Proof of Authority basico.

## 1. Estructura del proyecto

```text
tfm/
|-- app.py
|-- block.py
|-- blockchain.py
|-- config.py
|-- models.py
|-- node.py
|-- wallet.py
|-- requirements.txt
|-- README.md
|-- data/
|-- sample_data/
|   |-- example_events.json
|   `-- wallets/
`-- scripts/
    |-- create_signed_event.py
    |-- generate_wallet.py
    |-- run_distributed_node.ps1
    |-- run_testnet.ps1
    `-- simulate_flow.py
```

## 2. Arquitectura actualizada

- `models.py`: define el evento de trazabilidad, la limpieza de campos y las transiciones logicas permitidas.
- `wallet.py`: implementa una wallet RSA, firma, verificacion y derivacion de una direccion publica simple.
- `block.py`: mantiene la estructura del bloque, su hash SHA-256 determinista y las firmas de creador y validador.
- `blockchain.py`: concentra el bloque genesis fijo, los eventos pendientes, la validacion completa, el control de duplicados, la secuencia de trazabilidad, la verificacion de firmas de bloque, el PoA por turnos, la resolucion determinista de bifurcaciones y un indice auxiliar de estado por producto.
- `node.py`: gestiona persistencia local, nodos conocidos, nodos semilla, peer discovery, gossip propagation de eventos y bloques, rechazo de eventos fuera de secuencia y resolucion de conflictos de respaldo.
- `app.py`: expone la API Flask, incluyendo recepcion de bloques propagados, gossip, descubrimiento de peers, estado del consenso y bootstrap automatico opcional al arrancar.

## 3. Identidad del actor y wallet

Cada evento incluye ahora `public_key`, de modo que el propio evento es autosuficiente para auditar su firma dentro de la blockchain.

Flujo simplificado:

1. Un actor genera su wallet RSA.
2. La clave privada firma el contenido canonico del evento.
3. La clave publica viaja y se persiste dentro del propio evento.
4. La cadena vuelve a verificar la firma cuando valida bloques recibidos o persistidos.

La wallet tambien puede derivar una direccion publica resumida desde la clave publica mediante SHA-256 truncado. Esta direccion sirve como apoyo explicativo, pero la fuente de verificacion sigue siendo la clave publica completa.

## 4. Firma de bloques por creador y validador

Cada bloque no genesis puede incluir dos niveles de autoria criptografica:

- `creador_id`, `creador_public_key`, `firma_creador`
- `validador_id`, `validador_public_key`, `firma_validador`

El flujo es el siguiente:

1. Se construye el bloque con sus eventos y `previous_hash`.
2. Se calcula el `hash` estructural del bloque.
3. El creador o proponente firma ese bloque ya sellado.
4. El validador firma despues el bloque junto con la firma del creador.

Con esto se puede demostrar quien propuso el bloque y quien lo valido, y ademas sirve de base para el PoA basico actual.

## 5. Proof of Authority basico

El consenso actual es un PoA basico y controlado:

- solo los nodos incluidos en `AUTHORIZED_VALIDATORS` pueden validar bloques
- el validador debe coincidir con el turno esperado para la altura del bloque, salvo que el turno haya expirado
- un bloque no genesis debe incluir `validator_id`
- debe incluir `validator_public_key`
- debe incluir una firma valida del bloque, expuesta tambien como `block_signature`
- si el validador no esta autorizado, el bloque se rechaza
- si el bloque llega fuera de turno y el timeout no ha expirado, tambien se rechaza

La lista puede definirse en `config.py` o mediante la variable de entorno:

```powershell
$env:AUTHORIZED_VALIDATORS="node-5000,node-5001,node-5002"
```

La rotacion se calcula de forma determinista ordenando los validadores autorizados y asignando el turno segun el indice del bloque. Ejemplo simple:

- bloque `1` -> primer validador ordenado
- bloque `2` -> segundo
- bloque `3` -> tercero
- y asi sucesivamente en bucle

Para evitar que la cadena se detenga si un validador esta caido, existe un timeout de turno configurable:

```powershell
$env:POA_TURN_TIMEOUT_SECONDS="60"
```

Si el validador esperado no produce el bloque durante ese intervalo desde el ultimo bloque confirmado, el siguiente validador queda habilitado para minar esa misma altura. Si pasan mas intervalos, se van habilitando mas validadores autorizados de forma determinista. Todos los nodos pueden verificarlo usando el timestamp del bloque anterior y el timestamp de validacion del bloque recibido.

## 5.1. Cambios dinamicos de validadores

Los bloques separan dos tipos de contenido:

- `events`: eventos logisticos de productos
- `system_events`: eventos internos de la red

Normalmente `system_events` esta vacio. Se usa para cambios como anadir o eliminar validadores:

```json
{
  "type": "VALIDATOR_ADDED",
  "validator_id": "node-C",
  "effective_from_block": 25,
  "approvals": [
    {
      "validator_id": "node-A",
      "validator_public_key": "...",
      "signature": "..."
    },
    {
      "validator_id": "node-B",
      "validator_public_key": "...",
      "signature": "..."
    }
  ]
}
```

Reglas:

- el cambio debe tener aprobaciones de mas del 50% de los validadores activos en esa altura
- cada aprobacion firma los datos canonicos del evento de sistema
- `effective_from_block` debe ser posterior al bloque que aprueba el cambio
- los bloques antiguos se validan con el conjunto de validadores que estaba activo en su altura
- un bloque minado por un validador en turno no basta para cambiar la red si no trae aprobaciones suficientes

Con esto, si `node-A` intenta anadir `node-C` sin aprobacion de `node-B`, `node-B` rechaza el bloque aunque este firmado por `node-A`.

Valor base del proyecto:

```python
AUTHORIZED_VALIDATORS = {"node-5000", "node-5001", "node-5002"}
```

## 6. Flujo de propagacion de bloques

El flujo actual de red es:

1. un nodo autorizado mina un bloque
2. el bloque queda firmado por creador y validador
3. el nodo emisor propaga ese bloque a sus nodos conocidos mediante `POST /blocks/new`
4. cada nodo receptor valida:
   - `validator_id`
   - `validator_public_key`
   - `block_signature`
   - `hash`
   - `previous_hash`
   - `index`
   - eventos incluidos
5. si el bloque es valido, se agrega a la cadena local
6. si no es valido, se rechaza

`resolve_conflicts()` se mantiene como mecanismo de respaldo si un nodo no recibe un bloque propagado a tiempo.

## 7. Reglas de validacion de trazabilidad

La blockchain valida no solo hashes y enlaces entre bloques, sino tambien:

- estructura completa de cada evento
- presencia de `public_key`
- firma valida
- firma valida del creador del bloque
- firma valida del validador del bloque
- `validator_id` autorizado en PoA
- `validator_public_key` presente
- ausencia de `event_id` duplicados
- coherencia basica de la secuencia logistica

Reglas academicas incluidas:

- el primer evento de un producto debe ser `CREATED`
- no se permite mas de un `CREATED` para el mismo producto
- no se permiten eventos despues de `DELIVERED`
- las transiciones entre estados deben ser razonables

## 8. Indice auxiliar de estado por producto

La blockchain mantiene en memoria un indice `estado_actual_por_producto` para acelerar consultas y validaciones del ultimo estado confirmado de cada producto.

Este indice almacena, por `product_id`:

- `estado_actual`
- `timestamp_ultimo_evento`
- `actor_id_ultimo_evento`
- `ultimo_bloque`
- `hash_ultimo_bloque`

Puntos importantes:

- la blockchain completa sigue siendo la fuente oficial de verdad
- el indice no sustituye a la cadena ni altera los bloques
- el indice se reconstruye si se carga la cadena desde disco o si se reemplaza la cadena por consenso
- cuando entra un bloque nuevo valido, solo se actualizan los productos afectados por ese bloque

Ventajas academicas:

- mejor rendimiento para consultas frecuentes
- mejor escalabilidad cuando crece el numero de bloques
- validacion mas rapida del estado logistico previo

## 9. Red distribuida y transporte P2P

Cada nodo conoce su propia URL real, por ejemplo `http://127.0.0.1:5000`, y la usa para evitar autorregistro.

Capacidades incluidas:

- registro manual de nodos vecinos
- peer discovery a partir de nodos ya conocidos
- propagacion simple de eventos a otros nodos
- gossip propagation de eventos y bloques mediante TTL
- propagacion directa de bloques ya minados mediante `POST /blocks/new`
- bootstrap inicial mediante nodos semilla
- transporte libp2p opcional para comunicar nodos entre maquinas y redes distintas
- persistencia local en JSON
- adopcion de la mejor cadena valida como respaldo, con desempate determinista y recuperacion de eventos huerfanos

La API HTTP se mantiene como interfaz local de administracion y compatibilidad. Para despliegues entre ordenadores de redes distintas, se puede activar libp2p con `P2P_ENABLED=1`. En ese modo los nodos exponen una multiaddr, se conectan a peers semilla y envian eventos y bloques con el protocolo `/tfm-logistics-blockchain/1.0.0`.

El transporte libp2p es opcional: si la dependencia no esta instalada, el nodo sigue funcionando por HTTP y `/p2p/status` indica que libp2p no esta disponible.

## 10. Gossip propagation

La gossip propagation permite que un evento o bloque valido no se quede solo en los peers directos del nodo emisor.

Flujo conceptual:

1. Nodo A recibe o genera un bloque o evento valido.
2. A lo envia a sus peers conocidos.
3. Si B lo acepta y el `ttl` sigue siendo mayor que cero, B lo reenvia a C.
4. Si C lo acepta y aun queda `ttl`, C puede reenviarlo a D.
5. Cada nodo evita bucles simples mediante:
   - `seen_events`
   - `seen_blocks`
   - `origin_node`
   - `ttl`

Campos auxiliares del mensaje:

- `propagate`: activa o desactiva el reenvio automatico
- `ttl`: numero maximo de saltos restantes para el mensaje
- `origin_node`: URL del nodo que envio el mensaje inmediatamente anterior

Este gossip sigue siendo academico: no usa gossip probabilistico, no selecciona subconjuntos de peers y no implementa confirmaciones ni reintentos complejos.

## 11. Peer discovery

El peer discovery permite que un nodo aprenda automaticamente nuevos peers a partir de otros nodos que ya conoce.

Flujo conceptual:

1. Nodo A conoce a Nodo B.
2. Nodo A consulta `GET /peers` de Nodo B.
3. Nodo B responde con su propia URL y con su lista de nodos conocidos.
4. Si B conoce a C y D, entonces A puede incorporar automaticamente C y D.
5. A ignora:
   - su propia URL
   - peers duplicados
   - URLs invalidas

Este mecanismo es complementario al gossip. Solo se ejecuta cuando se solicita de forma explicita o cuando se activa de forma opcional al registrar un nodo.

## 12. Transporte libp2p

El modulo `p2p_libp2p.py` anade una capa de comunicacion P2P real basada en py-libp2p. Esta capa no sustituye al consenso PoA: solo cambia como se comunican los nodos.

Mensajes soportados:

- `NEW_EVENT`: envia un evento firmado a otro nodo
- `NEW_BLOCK`: envia un bloque validado
- `VALIDATOR_PROPOSAL`: envia una propuesta de evento de sistema
- `GET_CHAIN`: solicita la cadena del peer para sincronizacion
- `GET_STATUS`: solicita estado de consenso del peer
- `GET_PEERS`: solicita las multiaddrs y peers libp2p conocidos por el peer

Endpoints nuevos:

- `GET /p2p/status`: muestra si libp2p esta disponible, el `peer_id`, las multiaddrs y peers conectados
- `POST /p2p/sync`: solicita sincronizacion de cadena a los peers libp2p configurados
- `POST /p2p/discover`: solicita descubrimiento de peers mediante libp2p

Variables de entorno:

```powershell
$env:P2P_ENABLED="1"
$env:P2P_HOST="0.0.0.0"
$env:P2P_PORT="4001"
$env:P2P_BOOTSTRAP_PEERS="/ip4/203.0.113.10/tcp/4001/p2p/PEER_ID"
$env:P2P_SYNC_ON_STARTUP="1"
```

Ejemplo de arranque del primer nodo publico:

```powershell
$env:BLOCKCHAIN_HOST="0.0.0.0"
$env:BLOCKCHAIN_PORT="5000"
$env:NODE_ID="node-A"
$env:NODE_URL="http://IP_PUBLICA_A:5000"
$env:AUTHORIZED_VALIDATORS="node-A,node-B,node-C"
$env:P2P_ENABLED="1"
$env:P2P_PORT="4001"
python app.py
```

Despues consulta:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/p2p/status
```

La respuesta incluye una multiaddr similar a:

```text
/ip4/0.0.0.0/tcp/4001/p2p/12D3Koo...
```

En otro ordenador, usa esa multiaddr como bootstrap:

```powershell
$env:BLOCKCHAIN_HOST="0.0.0.0"
$env:BLOCKCHAIN_PORT="5001"
$env:NODE_ID="node-B"
$env:NODE_URL="http://IP_PUBLICA_B:5001"
$env:AUTHORIZED_VALIDATORS="node-A,node-B,node-C"
$env:P2P_ENABLED="1"
$env:P2P_PORT="4001"
$env:P2P_BOOTSTRAP_PEERS="/ip4/IP_PUBLICA_A/tcp/4001/p2p/12D3Koo..."
$env:P2P_SYNC_ON_STARTUP="1"
python app.py
```

Para redes reales hay que permitir el puerto P2P en firewall/router o usar una direccion publica alcanzable. py-libp2p soporta mecanismos de NAT traversal, pero este prototipo usa inicialmente bootstrap directo por multiaddr.

El descubrimiento libp2p implementado es un intercambio explicito de peers: el nodo consulta por stream a sus bootstrap peers y peers conectados, recibe sus `listen_addrs` y `known_peers`, y registra las multiaddrs nuevas para futuros envios. No usa Kademlia DHT ni mDNS; es una capa academica de peer discovery sobre el transporte libp2p.

La propagacion tipo gossip tambien se aplica sobre libp2p para `NEW_EVENT`, `NEW_BLOCK` y `VALIDATOR_PROPOSAL`. Cuando un nodo recibe y acepta uno de estos mensajes por stream, lo reenvia a otros peers libp2p conocidos con `ttl` reducido y excluyendo el `from_peer_id` de origen. La deteccion de duplicados sigue apoyandose en la memoria local de eventos y bloques vistos del nodo. No se usa GossipSub nativo; es flooding controlado sobre streams libp2p.

## 13. Requisitos previos

- Python 3.11 o superior recomendado
- `pip` disponible en el entorno

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 14. Ejecucion de nodos

### Nodo individual

```powershell
$env:BLOCKCHAIN_PORT=5000
$env:NODE_ID="node-5000"
$env:NODE_URL="http://127.0.0.1:5000"
$env:AUTHORIZED_VALIDATORS="node-5000,node-5001,node-5002"
$env:POA_TURN_TIMEOUT_SECONDS="60"
python app.py
```

### Testnet local con 3 nodos

```powershell
.\scripts\run_testnet.ps1
```

Puertos sugeridos:

- `5000`
- `5001`
- `5002`

### Nodo distribuido en una maquina real

El script `scripts/run_distributed_node.ps1` deja el nodo preparado para escuchar fuera de `localhost`, anunciar su URL real y arrancar con semillas conocidas.

Con libp2p:

```powershell
.\scripts\run_distributed_node.ps1 `
  -NodeId node-B `
  -NodeUrl http://IP_PUBLICA_B:5001 `
  -Port 5001 `
  -AuthorizedValidators node-A,node-B,node-C `
  -EnableP2P `
  -P2PPort 4001 `
  -P2PBootstrapPeers "/ip4/IP_PUBLICA_A/tcp/4001/p2p/12D3Koo..." `
  -P2PSyncOnStartup
```

## 15. CLI sencilla

Para no escribir llamadas largas con `Invoke-RestMethod`, se incluye una CLI ligera:

```powershell
.\bc.bat status
.\bc.bat p2p
.\bc.bat chain
.\bc.bat pending
```

Crear y enviar un evento firmado:

```powershell
.\bc.bat event --product PKG-001 --type CREATED --location Madrid --actor factory-a
```

Minar con el validador en turno:

```powershell
.\bc.bat mine --validator node-A
```

Proponer anadir un validador con aprobaciones de los validadores actuales:

```powershell
.\bc.bat add-validator --validator node-C --effective-from-block 25 --approver node-A --approver node-B
.\bc.bat mine --validator node-A
```

Flujo distribuido, sin compartir claves privadas entre nodos:

En el nodo C se genera o consulta su wallet publica:

```powershell
.\bc.bat wallet node-C
```

En el nodo A se crea una propuesta firmada solo por A y se propaga por libp2p:

```powershell
.\bc.bat proposal-create-add --validator node-C --effective-from-block 25 --approver node-A --validator-public-key sample_data\wallets\node-C_public.pem
```

En el nodo B se revisa la propuesta recibida y se aprueba con la clave privada local de B:

```powershell
.\bc.bat --node http://127.0.0.1:5001 proposal-approve --type VALIDATOR_ADDED --validator node-C --effective-from-block 25 --approver node-B
```

Cuando la propuesta supera mas del 50% de aprobaciones, pasa automaticamente a `pending_system_events`. Despues mina el validador en turno:

```powershell
.\bc.bat mine --validator node-A
```

Proponer eliminar un validador:

```powershell
.\bc.bat remove-validator --validator node-C --effective-from-block 30 --approver node-A --approver node-B
.\bc.bat mine --validator node-B
```

Si el nodo corre en otro puerto:

```powershell
.\bc.bat --node http://127.0.0.1:5001 status
.\bc.bat --node http://127.0.0.1:5001 mine --validator node-B
```

```powershell
.\scripts\run_distributed_node.ps1 `
  -NodeId "node-A" `
  -NodeUrl "http://192.168.1.20:5000" `
  -Port 5000 `
  -AuthorizedValidators "node-A","node-B","node-C" `
  -SeedNodes "http://192.168.1.21:5000","http://192.168.1.22:5000" `
  -AutoRegisterSeeds `
  -AutoDiscoverOnStartup `
  -AutoResolveOnStartup
```

Este script configura automaticamente:

- `BLOCKCHAIN_HOST=0.0.0.0`
- `NODE_URL` con la direccion real del nodo
- `SEED_NODES` para bootstrap
- registro automatico de semillas
- peer discovery al arrancar
- resolucion de conflictos al arrancar

### Arranque entre ordenadores

Para usar la red entre maquinas distintas:

1. Cada ordenador debe ejecutar su propio nodo.
2. Cada nodo debe anunciar una URL accesible por los demas (`NODE_URL`).
3. Si los nodos estan en la misma red local, usa las IP privadas del tipo `192.168.x.x`.
4. Si estan en redes distintas, usa IP publica, dominio o una red privada entre equipos.
5. Abre el puerto configurado en el firewall del sistema operativo.
6. Arranca cada nodo con al menos una semilla valida para que la red haga bootstrap.

Ejemplo simple con tres nodos:

- `node-A` -> `http://IP_A:5000`
- `node-B` -> `http://IP_B:5000`
- `node-C` -> `http://IP_C:5000`

En todos debe coincidir la misma lista de `AUTHORIZED_VALIDATORS`.

## 14. Endpoints disponibles

- `GET /health`
- `GET /chain`
- `GET /nodes`
- `GET /peers`
- `GET /events/pending`
- `GET /consensus/status`
- `POST /events/new`
- `POST /blocks/new`
- `POST /mine`
- `GET /products/<product_id>/history`
- `GET /products/<product_id>/state`
- `POST /nodes/register`
- `POST /nodes/discover`
- `GET /nodes/resolve`

## 15. Ejemplos de uso

### Generar una wallet

```bash
python scripts/generate_wallet.py --actor factory-madrid
python scripts/generate_wallet.py --actor warehouse-barcelona
```

### Crear un evento firmado

```bash
python scripts/create_signed_event.py ^
  --product-id PKG-001 ^
  --event-type CREATED ^
  --location "Madrid Factory" ^
  --actor-id factory-madrid ^
  --private-key sample_data/wallets/factory-madrid_private.pem ^
  --public-key sample_data/wallets/factory-madrid_public.pem
```

### Registrar nodos

```bash
curl -X POST http://127.0.0.1:5000/nodes/register ^
  -H "Content-Type: application/json" ^
  -d "{\"nodes\": [\"http://127.0.0.1:5001\", \"http://127.0.0.1:5002\"]}"
```

### Registrar nodos y descubrir peers automaticamente

```bash
curl -X POST http://127.0.0.1:5000/nodes/register ^
  -H "Content-Type: application/json" ^
  -d "{\"nodes\": [\"http://127.0.0.1:5001\"], \"discover\": true}"
```

### Consultar peers conocidos

```bash
curl http://127.0.0.1:5000/peers
```

### Ejecutar peer discovery manualmente

```bash
curl -X POST http://127.0.0.1:5000/nodes/discover
```

### Enviar un evento

```bash
curl -X POST http://127.0.0.1:5000/events/new ^
  -H "Content-Type: application/json" ^
  --data @created_event.json
```

Si el evento llega fuera de orden o no cumple la logica de trazabilidad en el momento de recepcion, el nodo lo rechaza. Esto evita que un evento prematuro quede guardado y pueda volverse valido mas adelante por cambios posteriores en la cadena.

### Enviar un evento con gossip y TTL explicito

```bash
curl -X POST http://127.0.0.1:5000/events/new ^
  -H "Content-Type: application/json" ^
  -d "{\"event\": { ... }, \"propagate\": true, \"ttl\": 3}"
```

### Enviar un bloque manualmente

```bash
curl -X POST http://127.0.0.1:5001/blocks/new ^
  -H "Content-Type: application/json" ^
  --data @bloque.json
```

### Enviar un bloque con gossip controlado

```bash
curl -X POST http://127.0.0.1:5001/blocks/new ^
  -H "Content-Type: application/json" ^
  -d "{\"block\": { ... }, \"propagate\": true, \"ttl\": 2, \"origin_node\": \"http://127.0.0.1:5000\"}"
```

### Minar un bloque firmado bajo PoA basico

Si no se indica `validator`, el propio creador actuara tambien como validador.

```bash
curl -X POST http://127.0.0.1:5000/mine ^
  -H "Content-Type: application/json" ^
  -d "{\"creator\":{\"actor_id\":\"node-5000\",\"private_key\":\"PEM_PRIVADA_CREADOR\",\"public_key\":\"PEM_PUBLICA_CREADOR\"},\"validator\":{\"actor_id\":\"node-5000\",\"private_key\":\"PEM_PRIVADA_VALIDADOR\",\"public_key\":\"PEM_PUBLICA_VALIDADOR\"}}"
```

### Consultar historial

```bash
curl http://127.0.0.1:5000/products/PKG-001/history
```

### Consultar el estado actual indexado

```bash
curl http://127.0.0.1:5000/products/PKG-001/state
```

### Resolver conflictos

```bash
curl http://127.0.0.1:5001/nodes/resolve
```

### Consultar el estado del consenso

```bash
curl http://127.0.0.1:5000/consensus/status
```

Devuelve, entre otros datos:

- longitud local de cadena
- si la cadena local es valida
- siguiente indice esperado
- validador en turno
- si este nodo puede minar ahora
- validadores autorizados
- peers conocidos
- eventos pendientes

## 16. Prueba manual del indice de estados

1. Crear eventos para varios productos, por ejemplo `PKG-001`, `PKG-002` y `PKG-003`.
2. Minar varios bloques en un nodo autorizado.
3. Consultar:

```bash
curl http://127.0.0.1:5000/products/PKG-001/state
```

4. Verificar que la respuesta devuelve el ultimo estado confirmado, el actor, el timestamp y el ultimo bloque.
5. Consultar tambien `/products/PKG-001/history` y comprobar que el ultimo evento del historial coincide con el estado indexado.
6. Reiniciar el nodo o forzar una recarga de cadena desde disco.
7. Confirmar que el indice se reconstruye y que `/products/PKG-001/state` sigue devolviendo el mismo resultado.

## 17. Prueba manual de peer discovery

### Escenario A -> B -> C y D

1. Arrancar `node-5000`, `node-5001`, `node-5002` y `node-5003`.
2. Registrar en `node-5001` a `node-5002` y `node-5003`.
3. Registrar en `node-5000` solo a `node-5001`.
4. Ejecutar:

```bash
curl -X POST http://127.0.0.1:5000/nodes/discover
```

5. `node-5000` debe consultar a `node-5001` y aprender automaticamente `node-5002` y `node-5003`.
6. Comprobarlo con:

```bash
curl http://127.0.0.1:5000/peers
```

## 18. Prueba manual de propagacion de bloques y gossip

### Escenario base entre `node-5000` y `node-5001`

1. Arrancar `node-5000` y `node-5001` con:

```powershell
$env:AUTHORIZED_VALIDATORS="node-5000,node-5001,node-5002"
```

2. Registrar nodos desde `node-5000`:

```bash
curl -X POST http://127.0.0.1:5000/nodes/register ^
  -H "Content-Type: application/json" ^
  -d "{\"nodes\": [\"http://127.0.0.1:5001\"]}"
```

3. Crear un evento en `node-5000`.
4. Llamar a `POST /mine` en `node-5000`.
5. El nodo `5000` debe propagar automaticamente el bloque a `node-5001`.
6. Si `node-5001` conoce a mas peers y el `ttl` lo permite, debe reenviar ese bloque automaticamente.
7. Comprobar en `GET /chain` de `node-5001` que el nuevo bloque ya aparece sin necesidad de `resolve_conflicts()`.

### Escenario A -> B -> C -> D con gossip

1. Arrancar `node-5000`, `node-5001`, `node-5002` y `node-5003`.
2. Registrar peers en cadena:
   - `node-5000` conoce a `node-5001`
   - `node-5001` conoce a `node-5002`
   - `node-5002` conoce a `node-5003`
3. Crear un evento en `node-5000` con `ttl: 3`.
4. Minar en `node-5000`.
5. El bloque debe viajar de `5000` a `5001`, de `5001` a `5002` y de `5002` a `5003`.
6. Los nodos no deben reenviar de vuelta al peer del que lo recibieron y deben ignorar duplicados por `hash`.

### Bloque manipulado

1. Obtener un bloque valido desde `GET /chain`.
2. Alterar `block_signature`, `validator_public_key`, `previous_hash` o cualquier evento.
3. Enviar el bloque alterado a:

```bash
curl -X POST http://127.0.0.1:5001/blocks/new ^
  -H "Content-Type: application/json" ^
  --data @bloque_manipulado.json
```

4. El nodo receptor debe responder `400` y rechazarlo.

## 19. Prueba manual del PoA basico

### Caso 1. `node-5000` autorizado mina correctamente

1. Arrancar el nodo con:

```powershell
$env:NODE_ID="node-5000"
$env:AUTHORIZED_VALIDATORS="node-5000,node-5001,node-5002"
python app.py
```

2. Registrar eventos pendientes.
3. Llamar a `POST /mine` con un `creator` valido.
4. El bloque debe crearse y en `/chain` debe aparecer `validator_id: "node-5000"`.

### Caso 2. `node-5005` no autorizado intenta minar

1. Arrancar el nodo con:

```powershell
$env:NODE_ID="node-5005"
$env:AUTHORIZED_VALIDATORS="node-5000,node-5001,node-5002"
python app.py
```

2. Registrar eventos pendientes.
3. Llamar a `POST /mine`.
4. La respuesta debe fallar con el mensaje:

```text
Nodo no autorizado para validar bloques
```

### Caso 3. Bloque con firma invalida

1. Crear un bloque valido en un nodo autorizado.
2. Alterar en el JSON resultante `block_signature`, `validator_public_key` o cualquier dato del bloque.
3. Forzar una validacion de cadena, por ejemplo al resolver conflictos entre nodos.
4. El bloque manipulado debe ser rechazado porque la firma del bloque ya no coincide.

## 20. Flujo de demostracion recomendado

1. Generar una wallet para `factory-madrid`.
2. Levantar `node-A`, `node-B` y `node-C` con `scripts/run_distributed_node.ps1`.
3. Comprobar `/health` y `/consensus/status` en los tres nodos.
4. Enviar un evento a cualquier nodo de entrada.
5. Verificar que el evento aparece en `pending_events` si es valido, o que se rechaza si no cumple la secuencia logistica.
6. Comprobar en `/consensus/status` cual es el validador en turno.
7. Ejecutar `POST /mine` en el nodo al que le toca validar.
8. Consultar `/chain`, `/events/pending`, `/products/PKG-001/history` y `/products/PKG-001/state` en todos los nodos.
9. Si quieres observar gossip, crea topologias en cadena y usa `ttl` mayor que `1`.
10. Usar `resolve_conflicts()` solo si se quiere comprobar el mecanismo de respaldo o resincronizar un nodo atrasado.

